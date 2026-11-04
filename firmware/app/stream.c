// Stream probe and BPC1 checker.
//
// The USB interrupt counts packets, bytes and microframes. The microframe
// number comes from the controller when each packet completes, so a
// microframe without a packet shows up as a gap. Packets complete well
// inside their microframe; if one is ever counted in the next microframe
// instead, the gap it leaves is matched by a "doubled" microframe, and the
// net loss (missed - doubled) stays right.
//
// The main loop drains the receive FIFO, probes the samples for the bits in
// use and for DoP markers, and feeds every frame to the BPC1 checker engine
// (bpc1/bpc1_checker.h), which starts over with each stream. It logs a stat
// line every second, a lock line for each file found, and a result line for
// each file and each stretch of unknown signal the checker finished; the
// last ones come when the stream stops. The run's results go to the LEDs.
//
// Each result line counts the lost packets and FIFO overflows that fall in
// its frames, by their place in the stream, not by the time of the line:
// the interrupt runs ahead of the checker by the FIFO's fill. The interrupt
// notes each hole with the bytes of the stream received before it; the main
// loop turns that into the checker's frame once it has read that far, and
// notes each overflow at the frame where it tells the checker of it. A
// result takes the events before its end frame. Its line waits until a
// later result is sure to come, so that the stream's last line can take
// what is left: the lines add up to the stream stop line.

#include "stream.h"

#include <stdbool.h>
#include <string.h>

#include "board.h"
#include "bpc1_checker.h"
#include "load.h"
#include "log.h"
#include "status.h"
#include "tusb.h"
#include "usb_descriptors.h"

#define UFRAME_MASK   0x3FFFu
#define IDLE_UFRAMES  (STREAM_IDLE_MS * 8u)
#define WINDOW_MS     1000u

// Counters of one stream, written by the USB interrupt.
typedef struct {
  uint32_t seq;        // segment number
  uint64_t packets;
  uint64_t bytes;
  uint64_t uframes;    // microframes from the first packet to the last
  uint32_t missed;     // microframes without a packet
  uint32_t holes;      // runs of microframes without a packet
  uint32_t max_hole;   // the longest run
  uint32_t doubled;    // packets counted in the same microframe as the one before
  uint32_t overflows;  // packets that left the receive FIFO full
  uint16_t min_bytes;
  uint16_t max_bytes;
} counters_t;

static counters_t isr;
static uint32_t isr_last_uframe;
static volatile bool isr_restart = true;

// A hole or a doubled microframe of the interrupt's segment, where it fell
typedef struct {
  uint64_t at;   // bytes of the segment received before it
  int32_t lost;  // net lost packets: missed microframes, less doubled ones
} isr_event_t;

// Events not yet taken by the main loop, which takes them with the counters
// at every pass (take()). When full, an event goes into the newest one.
#define ISR_EVENTS 8u

static isr_event_t isr_events[ISR_EVENTS];
static uint32_t isr_n_events;  // the main loop changes it with interrupts off

static void isr_event(int32_t lost) {
  // A doubled microframe takes back the hole before it: the packet was late,
  // not lost
  if (isr_n_events == ISR_EVENTS || (lost < 0 && isr_n_events > 0 && isr_events[isr_n_events - 1u].lost > 0)) {
    isr_events[isr_n_events - 1u].lost += lost;
  } else {
    isr_events[isr_n_events].at = isr.bytes;
    isr_events[isr_n_events].lost = lost;
    isr_n_events++;
  }
}

void stream_packet_isr(uint16_t n_bytes) {
  // A zero-length packet carries no frames (UAC2 calls it a transfer
  // delimiter): it counts like a microframe without a packet.
  if (n_bytes == 0) {
    return;
  }
  uint32_t uframe = board_usb_microframe();
  uint32_t gap = (uframe - isr_last_uframe) & UFRAME_MASK;
  isr_last_uframe = uframe;

  if (isr_restart || gap > IDLE_UFRAMES) {
    uint32_t seq = isr.seq + 1;
    memset(&isr, 0, sizeof(isr));
    isr.seq = seq;
    isr.min_bytes = n_bytes;
    isr.max_bytes = n_bytes;
    isr_n_events = 0;
    isr_restart = false;
  } else {
    isr.uframes += gap;
    if (gap == 0) {
      isr.doubled++;
      isr_event(-1);
    } else if (gap > 1) {
      isr.missed += gap - 1;
      isr.holes++;
      if (gap - 1 > isr.max_hole) {
        isr.max_hole = gap - 1;
      }
      isr_event((int32_t) (gap - 1));
    }
  }

  isr.packets++;
  isr.bytes += n_bytes;
  if (n_bytes < isr.min_bytes) {
    isr.min_bytes = n_bytes;
  }
  if (n_bytes > isr.max_bytes) {
    isr.max_bytes = n_bytes;
  }
  // The FIFO overwrites its oldest data when full, so more than its depth
  // written and not read means data lost. tu_fifo_count() is capped at the
  // depth and cannot tell that from an exactly full FIFO.
  const tu_fifo_t *ff = tud_audio_get_ep_out_ff();
  uint32_t waiting = tu_ff_overflow_count(ff->depth, ff->wr_idx, ff->rd_idx);
  if (waiting > ff->depth) {
    isr.overflows++;
    waiting = ff->depth;
  }
  load_fifo(waiting);
}

static counters_t snapshot(void) {
  board_irq_disable();
  counters_t c = isr;
  board_irq_enable();
  return c;
}

// The interrupt's segment number: one word, read without turning interrupts off
static uint32_t isr_seq(void) {
  return *(const volatile uint32_t *) &isr.seq;
}

//--------------------------------------------------------------------+
// Main loop side
//--------------------------------------------------------------------+

#define WATCH_MS      250u  // how often the checker's progress is looked at

typedef struct {
  uint32_t probe_frames;
  uint32_t or_bits;     // OR of every sample, left-justified to 32 bits
  uint32_t dop_frames;  // frames with a valid DoP marker
} probe_t;

// Load figures of one second, or the highest of a result's seconds
typedef struct {
  uint32_t isr_pct10;   // time in the USB interrupt, 0.1 %
  uint32_t isr_max;     // cycles of the longest interrupt
  uint32_t ck_cpf;      // checker cycles per frame
  uint32_t ck_pct10;    // time in the checker, 0.1 %
  uint32_t loop_max;    // us, the longest main-loop iteration
  uint32_t fifo_max;    // us of audio, the fullest the receive FIFO got
} figures_t;

// Lost packets or overflows of the stream, where they fell: before the
// checker's frame `at` once placed, before byte `at` of the interrupt's
// segment until then
typedef struct {
  uint64_t at;
  bool placed;
  int32_t lost;
  uint32_t overflows;
} event_t;

// Events not yet charged to a result: those of the file being checked,
// mostly. When full, an event goes into the newest one.
#define EVENTS 64u

// The fields of a result line besides the checker's report
typedef struct {
  uint32_t seq;
  uint32_t stream;
  uint32_t file;
  const bpc1_report_t *r;
  uint32_t rate;
  uint8_t alt, slot, resolution;
  int32_t lost;
  uint32_t overflows;
} result_t;

static struct {
  bool active;
  uint32_t seq;
  counters_t last;      // latest counters of the active stream
  uint64_t idle_packets;  // packets when stream_task() last saw them change
  uint32_t last_packet_ms;
  uint32_t window_ms;
  int32_t lost_logged;  // net lost packets already logged
  uint32_t overflows_logged;
  uint32_t overflows_fed;  // overflows the checker was told of
  uint32_t last_marker;
  probe_t window;       // this second
  int bits;             // last logged, -1 before the first window
  int dop;
  // load figures of this second
  uint32_t window_cycles;  // cycle counter at its start
  uint32_t ck_cycles;
  uint32_t ck_frames;
  figures_t figures;
  // the checker's progress
  uint32_t watch_ms;
  bool locked;
  uint64_t lock_frame;  // first frame of the file the last lock line named
  // lost packets and overflows, by where they fell
  uint64_t fed;             // frames given to the checker
  event_t events[EVENTS];
  uint32_t n_events;
  int32_t lost_charged;     // charged to results so far
  uint32_t overflows_charged;
  // results
  bool held;                // a result whose line waits (pop_results())
  bpc1_report_t held_r;
  int32_t held_lost;
  uint32_t held_overflows;
  uint32_t stream_results;  // result lines of this stream
} s;

static bpc1_checker_t ck;
static uint32_t buf[1024];
static uint32_t words[2 * 1024];  // buf's frames as the checker takes them
static uint32_t results;          // result lines since boot
static char result_text[LOG_LINE_MAX];

static uint32_t frame_bytes(void) {
  return (uint32_t) status.slot * UAC2_CHANNELS;
}

static int32_t net_lost(const counters_t *c) {
  return (int32_t) (c->missed - c->doubled);
}

// Average rate of the stream so far, in 1/100 Hz of the host's clock.
static uint32_t rate_x100(const counters_t *c) {
  uint64_t frames = c->bytes / frame_bytes();
  return (uint32_t) (frames * 800000u / (c->uframes + 1u));
}

static int probe_bits(const probe_t *p) {
  return (p->or_bits == 0) ? 0 : 32 - __builtin_ctz(p->or_bits);
}

static int probe_dop(const probe_t *p) {
  return (status.slot >= 3 && p->dop_frames * 2 > p->probe_frames) ? 1 : 0;
}

static void probe_add(probe_t *p, uint32_t frames, uint32_t or_bits, uint32_t dop) {
  p->or_bits |= or_bits;
  p->dop_frames += dop;
  p->probe_frames += frames;
}

// One frame, both channels left-justified to 32 bits
static inline void probe_frame(uint32_t l, uint32_t r, uint32_t *or_bits, uint32_t *dop, uint32_t *prev) {
  *or_bits |= l | r;
  // DoP: the top byte is 0x05 or 0xFA, alternating, the same in both channels
  uint32_t m = l >> 24;
  if (m == (r >> 24) && (m ^ *prev) == 0xFF && (m == 0x05 || m == 0xFA)) {
    (*dop)++;
  }
  *prev = m;
}

// Probes the frames in b and returns them as 32-bit left-justified words,
// channels interleaved: b itself for 4-byte slots, else in words[].
static const uint32_t *probe(const uint8_t *b, uint32_t frames) {
  uint32_t or_bits = 0;
  uint32_t dop = 0;
  uint32_t prev = s.last_marker;
  const uint32_t *out = words;

  switch (status.slot) {
    case 2:
      for (uint32_t i = 0; i < frames; i++, b += 4) {
        uint32_t l = (uint32_t) (b[0] | (b[1] << 8)) << 16;
        uint32_t r = (uint32_t) (b[2] | (b[3] << 8)) << 16;
        or_bits |= l | r;  // no DoP in 16 bits
        words[2 * i] = l;
        words[2 * i + 1] = r;
      }
      break;
    case 3:
      for (uint32_t i = 0; i < frames; i++, b += 6) {
        uint32_t l = ((uint32_t) b[0] << 8) | ((uint32_t) b[1] << 16) | ((uint32_t) b[2] << 24);
        uint32_t r = ((uint32_t) b[3] << 8) | ((uint32_t) b[4] << 16) | ((uint32_t) b[5] << 24);
        probe_frame(l, r, &or_bits, &dop, &prev);
        words[2 * i] = l;
        words[2 * i + 1] = r;
      }
      break;
    default: {
      const uint32_t *w = (const uint32_t *) (const void *) b;  // buf is word-aligned
      for (uint32_t i = 0; i < frames; i++) {
        probe_frame(w[2 * i], w[2 * i + 1], &or_bits, &dop, &prev);
      }
      out = w;
      break;
    }
  }

  s.last_marker = prev;
  probe_add(&s.window, frames, or_bits, dop);
  return out;
}

//--------------------------------------------------------------------+
// Lost packets and overflows, by where they fell
//--------------------------------------------------------------------+

static void event_add(uint64_t at, bool placed, int32_t lost, uint32_t overflows) {
  event_t *newest = s.n_events ? &s.events[s.n_events - 1u] : NULL;
  // Full, or a doubled microframe that takes back the hole before it
  if (s.n_events == EVENTS || (lost < 0 && newest && newest->lost > 0)) {
    newest->lost += lost;
    newest->overflows += overflows;
    return;
  }
  s.events[s.n_events++] = (event_t) {.at = at, .placed = placed, .lost = lost, .overflows = overflows};
}

// The interrupt's counters into s.last and its events into s.events, both
// as they were at one moment, if the interrupt is still in this stream's
// segment (else false). *read_at: the bytes of the segment the receive
// FIFO has handed out so far, those an overflow skipped included. False
// too after a bus reset, which closes the audio function and its FIFO.
static bool take(int64_t *read_at) {
  isr_event_t e[ISR_EVENTS];
  counters_t c;
  uint32_t n = 0;
  uint16_t waiting = 0;
  const tu_fifo_t *ff = tud_audio_get_ep_out_ff();
  if (ff == NULL) {
    return false;
  }
  board_irq_disable();
  bool same = isr.seq == s.seq;
  if (same) {
    c = isr;
    n = isr_n_events;
    memcpy(e, isr_events, n * sizeof(e[0]));
    isr_n_events = 0;
    waiting = tu_ff_overflow_count(ff->depth, ff->wr_idx, ff->rd_idx);
  }
  board_irq_enable();
  if (!same) {
    return false;
  }
  s.last = c;
  for (uint32_t i = 0; i < n; i++) {
    event_add(e[i].at, false, e[i].lost, 0);
  }
  if (read_at) {
    *read_at = (int64_t) c.bytes - waiting;
  }
  return true;
}

// The chunk of `frames` just read ends at byte read_at of the segment, and
// goes to the checker as its frames s.fed on: the events before read_at get
// their frame. Those before the chunk fell in bytes an overflow skipped:
// they go to its first frame, where the checker is told of the overflow.
static void place(int64_t read_at, uint32_t frames, uint32_t fb) {
  int64_t chunk_at = read_at - (int64_t) frames * fb;
  for (uint32_t i = 0; i < s.n_events; i++) {
    event_t *e = &s.events[i];
    int64_t off = (int64_t) e->at - chunk_at;
    if (!e->placed && (int64_t) e->at < read_at) {
      e->at = s.fed + (off > 0 ? (uint64_t) off / fb : 0u);
      e->placed = true;
    }
  }
}

// The events that fall before the checker's frame `end`, out of the queue
static void charge(uint64_t end, int32_t *lost, uint32_t *overflows) {
  uint32_t k = 0;
  *lost = 0;
  *overflows = 0;
  for (uint32_t i = 0; i < s.n_events; i++) {
    const event_t *e = &s.events[i];
    if (e->placed && e->at < end) {
      *lost += e->lost;
      *overflows += e->overflows;
    } else {
      s.events[k++] = *e;
    }
  }
  s.n_events = k;
  s.lost_charged += *lost;
  s.overflows_charged += *overflows;
}

// Overflows the checker has not been told of: they overwrote frames from
// s.fed on, so the checker marks the file there, and they count there.
static void tell_overflows(void) {
  if (s.last.overflows != s.overflows_fed) {
    event_add(s.fed, true, 0, s.last.overflows - s.overflows_fed);
    s.overflows_fed = s.last.overflows;
    bpc1_checker_overrun(&ck);
  }
}

// Reads the receive FIFO; in a stream, the frames go to the checker too.
static void drain(bool check) {
  uint32_t fb = frame_bytes();
  if (fb == 0) {
    return;
  }
  // Bounded, so that tud_task() runs between bursts
  for (int i = 0; i < 4; i++) {
    uint32_t n = tud_audio_available();
    if (n > sizeof(buf)) {
      n = sizeof(buf);
    }
    n -= n % fb;
    if (n == 0) {
      return;
    }
    // Once the interrupt has begun the next segment, the frames from its
    // first packet on are the next stream's, which starts at the next pass.
    // The interrupt writes a packet to the FIFO before it counts it, and the
    // main loop runs between interrupts: while the segment is still this
    // stream's after the FIFO's count was read, the n bytes counted are too.
    if (check && isr_seq() != s.seq) {
      return;
    }
    n = tud_audio_read(buf, (uint16_t) n);
    uint32_t frames = n / fb;
    const uint32_t *w = probe((const uint8_t *) buf, frames);
    // The counters and events after the read cover every frame read. They
    // count only while the interrupt's segment is this stream.
    int64_t read_at;
    if (check && take(&read_at)) {
      place(read_at, frames, fb);
      // An overflow since the last chunk, during this read included,
      // overwrote frames of this chunk: the checker hears of it before it
      // sees them.
      tell_overflows();
    }
    if (check) {
      uint32_t t0 = board_cycles();
      bpc1_checker_feed(&ck, w, frames);
      s.ck_cycles += board_cycles() - t0;
      s.ck_frames += frames;
      s.fed += frames;
    }
  }
}

//--------------------------------------------------------------------+
// Load figures
//--------------------------------------------------------------------+

static uint32_t cycles_us(uint32_t cycles) {
  return (uint32_t) ((uint64_t) cycles * 1000000u / board_cpu_hz());
}

// The figures since the last call into s.figures
static void figures_take(void) {
  uint32_t now = board_cycles();
  uint32_t elapsed = now - s.window_cycles;
  s.window_cycles = now;
  load_t l = load_take();
  figures_t *f = &s.figures;
  uint32_t fb = frame_bytes();

  f->isr_pct10 = elapsed ? (uint32_t) ((uint64_t) l.isr_cycles * 1000u / elapsed) : 0;
  f->isr_max = l.isr_max;
  f->ck_cpf = s.ck_frames ? s.ck_cycles / s.ck_frames : 0;
  f->ck_pct10 = elapsed ? (uint32_t) ((uint64_t) s.ck_cycles * 1000u / elapsed) : 0;
  f->loop_max = cycles_us(l.loop_max);
  f->fifo_max = (fb && status.rate) ? (uint32_t) ((uint64_t) l.fifo_max * 1000000u / (fb * status.rate)) : 0;
  s.ck_cycles = 0;
  s.ck_frames = 0;
}

//--------------------------------------------------------------------+
// Results
//--------------------------------------------------------------------+

// The result line without its time stamp, in the field order of the README
static void format_result(char *out, uint32_t size, const result_t *x) {
  const bpc1_report_t *r = x->r;
  bool file = r->verdict != BPC1_UNKNOWN_SIGNAL && r->kind != 0;
  char diff[16];
  uint32_t n = 0;
  bpc1_diff_text(r, diff, sizeof(diff));
  log_append(out, size, &n, "result seq=%lu stream=%lu file=%lu verdict=%s kind=%s dop=%u",
             (unsigned long) x->seq, (unsigned long) x->stream, (unsigned long) x->file,
             bpc1_verdict_name(r->verdict), bpc1_kind_name(r->kind), r->dop ? 1u : 0u);
  log_append(out, size, &n, " seed=0x%08lx nch=%u file_rate=%lu depth=%u", (unsigned long) (file ? r->id.seed : 0),
             file ? r->id.nch : 0u, (unsigned long) r->rate, r->depth);
  log_append(out, size, &n, " rate=%lu alt=%u slot=%u res=%u bits=%u end=%s diff=%s", (unsigned long) x->rate,
             x->alt, x->slot, x->resolution, r->bits, bpc1_end_name(r->end), diff);
  uint32_t ms = x->rate ? (uint32_t) (r->frames * 1000u / x->rate) : 0;
  log_append(out, size, &n, " lost=%ld ovf=%lu dur=%lu.%03lu", (long) x->lost, (unsigned long) x->overflows,
             (unsigned long) (ms / 1000u), (unsigned long) (ms % 1000u));
}

// A verdict into the run's results (the LEDs)
static void run_note(uint8_t verdict) {
  bool *flag = verdict == BPC1_BIT_PERFECT      ? &status.run_pass
               : verdict == BPC1_NOT_BIT_PERFECT ? &status.run_fail
               : verdict == BPC1_DEVICE_OVERRUN  ? &status.run_overrun
                                                 : &status.run_unknown;
  if (!*flag) {
    *flag = true;
    status_changed();
  }
}

void stream_run_start(void) {
  status.run_pass = status.run_fail = status.run_unknown = status.run_overrun = false;
  status_changed();
  log_line("run start");
}

// The line of the result that waits
static void log_held(void) {
  if (!s.held) {
    return;
  }
  result_t x = {
    .seq = ++results,
    .stream = status.stream_id,
    .file = s.stream_results + 1u,
    .r = &s.held_r,
    .rate = status.rate,
    .alt = status.alt,
    .slot = status.slot,
    .resolution = status.resolution,
    .lost = s.held_lost,
    .overflows = s.held_overflows,
  };
  format_result(result_text, sizeof(result_text), &x);
  log_line_reserved("%s", result_text);
  s.stream_results++;
  s.held = false;
}

// Each finished result takes the events before its end frame. Its line
// waits for the next result, for the checker to read past the extents
// finished so far (release()) or to open a newer file (watch()), or for the
// stream's stop, which gives the last line the stream's events after it.
static void pop_results(void) {
  bpc1_report_t r;
  while (bpc1_checker_pop(&ck, &r)) {
    log_held();
    s.held_r = r;
    charge(r.first_frame + r.frames, &s.held_lost, &s.held_overflows);
    s.held = true;
    run_note(r.verdict);
  }
}

// Frames past the extents finished so far belong to a result still to
// come, unless they are allowed silence: the waiting line can go.
static void release(void) {
  if (s.held && s.fed > bpc1_checker_covered(&ck)) {
    log_held();
  }
}

void stream_log_result(void) {
  if (results == 0) {
    log_line_reserved("result none");
  } else {
    log_line_reserved("%s", result_text);
  }
}

//--------------------------------------------------------------------+
// The checker's progress: the lock line, a file's first difference
//--------------------------------------------------------------------+

static void watch(void) {
  bpc1_report_t r;
  if (!bpc1_checker_peek(&ck, &r)) {
    return;
  }
  // A file after the waiting result: every later frame is that file's or a
  // later result's
  if (s.held && r.first_frame >= s.held_r.first_frame + s.held_r.frames) {
    log_held();
  }
  if (!s.locked || r.first_frame != s.lock_frame) {
    s.locked = true;
    s.lock_frame = r.first_frame;
    log_line("lock kind=%s dop=%u rate=%lu depth=%u seed=0x%08lx block=%u frame=%llu", bpc1_kind_name(r.kind),
             r.dop ? 1u : 0u, (unsigned long) r.rate, r.depth, (unsigned long) r.id.seed, r.id.block,
             (unsigned long long) r.first_frame);
  }
  if (r.verdict == BPC1_NOT_BIT_PERFECT) {
    run_note(r.verdict);  // red from the file's first difference on
  }
}

//--------------------------------------------------------------------+
// Stream start, stop and the stat line
//--------------------------------------------------------------------+

static void start(const counters_t *c, uint32_t now) {
  s.active = true;
  s.seq = c->seq;
  s.last = *c;
  s.idle_packets = c->packets;
  s.last_packet_ms = now;
  s.window_ms = now;
  s.watch_ms = now;
  s.lost_logged = 0;
  s.overflows_logged = 0;
  s.overflows_fed = 0;
  s.bits = -1;
  s.dop = -1;
  s.last_marker = 0;
  memset(&s.window, 0, sizeof(s.window));
  s.fed = 0;
  s.n_events = 0;
  s.lost_charged = 0;
  s.overflows_charged = 0;
  s.held = false;
  s.locked = false;
  s.stream_results = 0;
  s.ck_cycles = 0;
  s.ck_frames = 0;

  status.streaming = true;
  status.stream_id++;
  status.bits = 0;
  status.dop = false;
  status.lost = 0;
  status.overflows = 0;
  status_changed();
  log_line("stream start id=%lu rate=%lu alt=%u slot=%u res=%u allow_silence=%s", (unsigned long) status.stream_id,
           (unsigned long) status.rate, status.alt, status.slot, status.resolution,
           status.allow_silence ? "true" : "false");

  bpc1_checker_init(&ck, status.rate, (uint8_t) (status.slot * 8u));
  bpc1_checker_allow_silence(&ck, status.allow_silence);
  s.window_cycles = board_cycles();
  (void) load_take();
}

static void stop(const char *reason) {
  if (!s.active) {
    return;
  }
  memset(&s.window, 0, sizeof(s.window));
  const counters_t *c = &s.last;
  uint32_t fb = frame_bytes();
  uint64_t ms = (c->uframes + 1u) / 8u;
  uint32_t r = rate_x100(c);

  // The last file and the unknown signal after it. The stream stop line
  // comes after them: the stream's last line.
  figures_take();
  tell_overflows();
  bpc1_checker_end(&ck);
  pop_results();
  // The stream's last result line: the events no result took, those whose
  // frames were not read or that fell after the last result included
  s.held_lost += net_lost(c) - s.lost_charged;
  s.held_overflows += c->overflows - s.overflows_charged;
  log_held();

  log_line_reserved("stream stop id=%lu reason=%s dur=%lu.%03lu frames=%llu pkts=%llu rate=%lu.%02lu fpp=%u-%u "
           "lost=%ld missed=%lu holes=%lu maxhole=%lu doubled=%lu ovf=%lu outside=%llu silent=%llu results=%lu "
           "results_lost=%lu",
           (unsigned long) status.stream_id, reason, (unsigned long) (ms / 1000u),
           (unsigned long) (ms % 1000u), (unsigned long long) (c->bytes / fb), (unsigned long long) c->packets,
           (unsigned long) (r / 100u), (unsigned long) (r % 100u), (unsigned) (c->min_bytes / fb),
           (unsigned) (c->max_bytes / fb), (long) net_lost(c), (unsigned long) c->missed,
           (unsigned long) c->holes, (unsigned long) c->max_hole, (unsigned long) c->doubled,
           (unsigned long) c->overflows, (unsigned long long) bpc1_checker_outside(&ck),
           (unsigned long long) bpc1_checker_silent(&ck), (unsigned long) s.stream_results,
           (unsigned long) bpc1_checker_results_lost(&ck));

  s.active = false;
  // The interrupt may have begun the next segment already (a host gap just
  // over the idle time): a restart now would cut that segment short.
  board_irq_disable();
  if (isr.seq == s.seq) {
    isr_restart = true;
  }
  board_irq_enable();
  status.streaming = false;
  status_changed();
}

static void report(void) {
  const counters_t *c = &s.last;

  int bits = probe_bits(&s.window);
  if (bits != s.bits) {
    log_line("depth bits=%d", bits);
    s.bits = bits;
  }
  int dop = probe_dop(&s.window);
  if (dop != s.dop) {
    log_line("dop %s", dop ? "on" : "off");
    s.dop = dop;
  }

  int32_t lost = net_lost(c);
  if (lost > s.lost_logged) {
    log_line("lost packets=%ld total=%ld", (long) (lost - s.lost_logged), (long) lost);
    s.lost_logged = lost;
  }
  if (c->overflows > s.overflows_logged) {
    log_line("overflow packets=%lu total=%lu", (unsigned long) (c->overflows - s.overflows_logged),
             (unsigned long) c->overflows);
    s.overflows_logged = c->overflows;
  }

  figures_take();
  const figures_t *f = &s.figures;
  uint32_t r = rate_x100(c);
  log_line("stat id=%lu rate=%lu.%02lu frames=%lu pkts=%llu bits=%d dop=%d lost=%ld ovf=%lu isr_pct=%lu.%lu "
           "isr_max=%lu ck_cpf=%lu ck_pct=%lu.%lu loop_max=%lu fifo_max=%lu",
           (unsigned long) status.stream_id, (unsigned long) (r / 100u), (unsigned long) (r % 100u),
           (unsigned long) s.window.probe_frames, (unsigned long long) c->packets, bits, dop, (long) lost,
           (unsigned long) c->overflows, (unsigned long) (f->isr_pct10 / 10u),
           (unsigned long) (f->isr_pct10 % 10u), (unsigned long) f->isr_max, (unsigned long) f->ck_cpf,
           (unsigned long) (f->ck_pct10 / 10u), (unsigned long) (f->ck_pct10 % 10u),
           (unsigned long) f->loop_max, (unsigned long) f->fifo_max);

  if (status.bits != bits || status.dop != (dop != 0) || status.lost != (uint32_t) (lost > 0 ? lost : 0)
      || status.overflows != c->overflows) {
    status.bits = (uint8_t) bits;
    status.dop = dop != 0;
    status.lost = (uint32_t) (lost > 0 ? lost : 0);
    status.overflows = c->overflows;
    status_changed();
  }
  memset(&s.window, 0, sizeof(s.window));
}

void stream_task(void) {
  // A bus reset closes the audio function and clears its receive FIFO, but
  // TinyUSB does not call tud_umount_cb() for it: the stream ends here.
  if (!tud_mounted() && (s.active || status.alt != 0)) {
    stream_reset();
    status.usb = USB_NOT_MOUNTED;
    status_changed();
    log_line("usb reset");
  }

  counters_t c = snapshot();
  uint32_t now = board_millis();

  if (c.packets != 0 && c.seq != s.seq) {
    stop("pause");  // a new stream began without the old one being stopped
    if (status.slot != 0) {
      start(&c, now);
    } else {
      s.seq = c.seq;  // packets outside an alt setting: not a stream
    }
  }
  if (!s.active) {
    drain(false);  // whatever arrives outside a stream is not probed
    memset(&s.window, 0, sizeof(s.window));
    return;
  }

  if (c.packets != s.idle_packets) {
    s.idle_packets = c.packets;
    s.last_packet_ms = now;
  }
  (void) take(NULL);
  drain(true);
  pop_results();
  release();

  if (now - s.last_packet_ms >= STREAM_IDLE_MS) {
    stop("idle");
  } else if (now - s.window_ms >= WINDOW_MS) {
    s.window_ms += WINDOW_MS;
    report();
  } else if (now - s.watch_ms >= WATCH_MS) {
    s.watch_ms = now;
    watch();
  }
}

void stream_set_rate(uint32_t rate) {
  if (rate != status.rate) {
    // A stream's checker keeps the rate the stream started with. The frames
    // still in the receive FIFO came at that rate: they go to the checker
    // first, a FIFO's worth at most.
    if (s.active) {
      (void) take(NULL);
      for (uint32_t i = 0; i <= CFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ / (4u * sizeof(buf)); i++) {
        drain(true);
      }
    }
    stop("rate");
    isr_restart = true;
  }
  status.rate = rate;
  status_changed();
  log_line("rate %lu", (unsigned long) rate);
}

void stream_set_alt(uint8_t alt) {
  stop("alt");
  status.alt = alt;
  status.slot = uac2_alts[alt].slot;
  status.resolution = uac2_alts[alt].resolution;
  status_changed();
  isr_restart = true;
  if (alt == 0) {
    log_line("alt 0");
  } else {
    log_line("alt %u slot=%u res=%u", alt, status.slot, status.resolution);
  }
}

void stream_reset(void) {
  stop("reset");
  status.alt = 0;
  status.slot = 0;
  status.resolution = 0;
  status_changed();
  isr_restart = true;
}

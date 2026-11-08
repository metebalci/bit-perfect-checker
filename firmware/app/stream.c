// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Stream probe and BPC1 checker.
//
// The USB interrupt counts packets, bytes and microframes. Each packet is
// counted in the microframe it came in, so a microframe without a packet
// shows up as a gap. The controller receives an isochronous OUT packet only
// in a microframe whose parity is the endpoint's EONUM bit (RM0456 Rev 7
// 73.15.6, RM0431 Rev 4 32.16.6), and the interrupt that serves the packet
// enters in that microframe or the next one, so the microframe number and
// EONUM read at the interrupt's entry give the packet's own microframe
// (packet_uframe()). An entry two or more microframes late comes with a late
// re-arm, so packets were lost there anyway; the net loss stays right and
// its hole is counted one packet early. Two packets in the same microframe
// ("doubled", which takes back the hole before it) cannot happen this way;
// the count stays for the log line.
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
//
// A stream's frames end where the interrupt began the next segment: the
// interrupt notes that place in the bytes written to the receive FIFO, and
// the main loop feeds the old stream's checker up to it before it starts the
// new one. The interrupt counts the FIFO's overflows from the bytes written
// and read, not from TinyUSB's indices.

#include "stream.h"

#include <stdbool.h>
#include <string.h>

#include "board.h"
#include "bpc1_checker.h"
#include "family.h"
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
  uint64_t start;      // bytes written to the receive FIFO before the segment's first packet
  uint64_t packets;
  uint64_t bytes;
  uint64_t uframes;    // microframes from the first packet to the last
  uint32_t missed;     // microframes without a packet
  uint32_t holes;      // runs of microframes without a packet
  uint32_t max_hole;   // the longest run
  uint32_t doubled;    // packets counted in the same microframe as the one before: 0 with packet_uframe()
  uint32_t overflows;  // packets that overflowed the receive FIFO
  uint16_t min_bytes;
  uint16_t max_bytes;
} counters_t;

static counters_t isr;
static counters_t isr_prev;  // the segment before, as it ended: the main loop may not have read all of it
static uint32_t isr_last_uframe;
static volatile bool isr_restart = true;

// The receive FIFO's bytes, counted here and not from its indices, which
// TinyUSB sets back to a full FIFO when the bytes not read would reach twice
// its depth (tusb_fifo.c, "Double overflowed"). The interrupt counts the
// bytes written and those it overwrote before they were read; the main loop
// counts those it read, and brings that in line with the FIFO before each
// read (fifo_state()).
#define FIFO_BYTES CFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ

static uint64_t fifo_written;
static uint64_t fifo_skipped;
static volatile uint32_t fifo_read;  // the low word of fifo_read_all, for the interrupt
static uint64_t fifo_read_all;       // main loop only

// A hole or a doubled microframe, where it fell
typedef struct {
  uint64_t at;      // bytes of its segment received before it
  int32_t lost;     // net lost packets: missed microframes, less doubled ones
  uint32_t seq;     // its segment
  uint16_t uframe;  // the microframe of the packet that noted it
} isr_event_t;

// Events not yet taken by the main loop, which takes them with the counters
// at every pass (take()); those of the segment before stay until it has.
// When full, an event goes into the newest one of its segment, or only into
// the counters if there is none.
#define ISR_EVENTS 64u

static isr_event_t isr_events[ISR_EVENTS];
static uint32_t isr_n_events;  // the main loop changes it with interrupts off

static FAMILY_ISR_CODE void isr_event(int32_t lost, uint32_t uframe) {
  isr_event_t *newest = isr_n_events ? &isr_events[isr_n_events - 1u] : NULL;
  bool ours = newest != NULL && newest->seq == isr.seq;
  // A doubled microframe takes back the hole before it: the packet was late,
  // not lost. The main loop leaves that hole here for its microframe.
  if (ours && ((lost < 0 && newest->lost > 0) || isr_n_events == ISR_EVENTS)) {
    newest->lost += lost;
  } else if (isr_n_events < ISR_EVENTS) {
    isr_events[isr_n_events] = (isr_event_t) {.at = isr.bytes, .lost = lost, .seq = isr.seq, .uframe = (uint16_t) uframe};
    isr_n_events++;
  }
}

// The microframe P of the packet the interrupt serves, from the microframe
// number fn and the endpoint's EONUM bit read at the interrupt's entry: fn is
// P or P + 1, and P has EONUM's parity, so P = fn - ((fn ^ EONUM) & 1). If
// the interrupt entered before the packet came (it entered for something
// else and served the packet's completion in the same pass), this gives
// P - 2, before the packet before; a fresh read of the microframe number
// then gives P.
static FAMILY_ISR_CODE uint32_t packet_uframe(void) {
  uint32_t fn;
  bool odd;
  board_usb_entry(&fn, &odd);
  uint32_t uframe = (fn - ((fn ^ (odd ? 1u : 0u)) & 1u)) & UFRAME_MASK;
  if (!isr_restart && ((uframe - isr_last_uframe) & UFRAME_MASK) > UFRAME_MASK / 2u) {
    fn = board_usb_microframe();
    uframe = (fn - ((fn ^ (odd ? 1u : 0u)) & 1u)) & UFRAME_MASK;
  }
  return uframe;
}

FAMILY_ISR_CODE void stream_packet_isr(uint16_t n_bytes) {
  // A zero-length packet carries no frames (UAC2 calls it a transfer
  // delimiter): it counts like a microframe without a packet.
  if (n_bytes == 0) {
    return;
  }
  uint32_t uframe = packet_uframe();
  uint32_t gap = (uframe - isr_last_uframe) & UFRAME_MASK;
  isr_last_uframe = uframe;

  if (isr_restart || gap > IDLE_UFRAMES) {
    // Events older than the segment that ends: the main loop did not follow
    // that one, and they go
    if (isr_n_events > 0 && isr_events[0].seq != isr.seq) {
      isr_n_events = 0;
    }
    isr_prev = isr;
    uint32_t seq = isr.seq + 1;
    memset(&isr, 0, sizeof(isr));
    isr.seq = seq;
    isr.start = fifo_written;
    isr.min_bytes = n_bytes;
    isr.max_bytes = n_bytes;
    isr_restart = false;
  } else {
    isr.uframes += gap;
    if (gap == 0) {
      isr.doubled++;
      isr_event(-1, uframe);
    } else if (gap > 1) {
      isr.missed += gap - 1;
      isr.holes++;
      if (gap - 1 > isr.max_hole) {
        isr.max_hole = gap - 1;
      }
      isr_event((int32_t) (gap - 1), uframe);
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
  // The FIFO overwrites its oldest bytes when full: more than its depth
  // written and not read means those are lost.
  fifo_written += n_bytes;
  uint32_t waiting = (uint32_t) (fifo_written - fifo_skipped) - fifo_read;
  if (waiting > FIFO_BYTES) {
    isr.overflows++;
    fifo_skipped += waiting - FIFO_BYTES;
    waiting = FIFO_BYTES;
  }
  load_fifo(waiting);
}

static counters_t snapshot(void) {
  board_irq_disable();
  counters_t c = isr;
  board_irq_enable();
  return c;
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
#define EVENTS 1024u

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
  uint64_t start;           // the segment's start in the bytes written to the receive FIFO
  uint64_t fed;             // frames given to the checker
  uint64_t read_end;        // bytes of the segment the FIFO has handed out to the checker
  uint64_t map_at;          // from this byte of the segment on, frames run on from map_frame
  uint64_t map_frame;
  bool kept;                // the interrupt keeps a hole that a doubled microframe may take back
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
  uint32_t results_lost;    // results the checker dropped, as far as the run counts know
  uint64_t diff_file;       // first frame of the file status.file_diff names
} s;

static bpc1_checker_t ck;
static uint32_t buf[1024];
static uint32_t words[2 * 1024];  // buf's frames as the checker takes them
static uint32_t results;          // result lines since boot
static char result_text[LOG_LINE_MAX];
static uint32_t run_starts;       // "run start" lines that wait for the result line before them

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

// The checker's frame of byte `at` of the segment, once the FIFO has handed
// it out. Bytes an overflow skipped go to the first frame after them, where
// the checker is told of the overflow.
static uint64_t frame_of(uint64_t at, uint32_t fb) {
  return at > s.map_at ? s.map_frame + (at - s.map_at) / fb : s.map_frame;
}

static void event_add(uint64_t at, bool placed, int32_t lost, uint32_t overflows) {
  event_t *newest = s.n_events ? &s.events[s.n_events - 1u] : NULL;
  // Full, or a doubled microframe that takes back the hole before it
  if (s.n_events == EVENTS || (lost < 0 && newest && newest->lost > 0)) {
    newest->lost += lost;
    newest->overflows += overflows;
    return;
  }
  // An event the interrupt kept until after its bytes were read
  if (!placed && at < s.read_end) {
    at = frame_of(at, frame_bytes());
    placed = true;
  }
  s.events[s.n_events++] = (event_t) {.at = at, .placed = placed, .lost = lost, .overflows = overflows};
}

// The counters of this stream's segment into s.last and its events into
// s.events, both as they were at one moment; false if the interrupt has
// left that segment and the one after. A hole noted in the microframe that
// runs stays with the interrupt unless `all`, since a doubled microframe
// can still take it back (s.kept).
static bool take(bool all) {
  isr_event_t e[ISR_EVENTS];
  counters_t c;
  uint32_t n = 0;
  uint16_t uframe = (uint16_t) (board_usb_microframe() & UFRAME_MASK);
  board_irq_disable();
  bool cur = isr.seq == s.seq;
  bool same = cur || isr_prev.seq == s.seq;
  bool kept = false;
  if (same) {
    c = cur ? isr : isr_prev;
    // Older segments' events first, then this one's, then the next one's
    uint32_t i = 0;
    while (i < isr_n_events && (int32_t) (isr_events[i].seq - s.seq) < 0) {
      i++;
    }
    uint32_t j = i;
    while (j < isr_n_events && isr_events[j].seq == s.seq) {
      j++;
    }
    if (!all && cur && j > i && j == isr_n_events && isr_events[j - 1u].lost > 0
        && isr_events[j - 1u].uframe == uframe) {
      j--;
      kept = true;
    }
    n = j - i;
    memcpy(e, &isr_events[i], n * sizeof(e[0]));
    memmove(isr_events, &isr_events[j], (isr_n_events - j) * sizeof(e[0]));
    isr_n_events -= j;
  }
  board_irq_enable();
  s.kept = kept;
  if (!same) {
    return false;
  }
  s.last = c;
  for (uint32_t k = 0; k < n; k++) {
    event_add(e[k].at, false, e[k].lost, 0);
  }
  return true;
}

// The chunk of `frames` just read starts at byte chunk_at of the segment and
// goes to the checker as its frames s.fed on: the events before its end get
// their frame.
static void place(uint64_t chunk_at, uint32_t frames, uint32_t fb) {
  if (chunk_at != s.read_end) {
    // An overflow skipped the bytes in between
    s.map_at = chunk_at;
    s.map_frame = s.fed;
  }
  s.read_end = chunk_at + (uint64_t) frames * fb;
  for (uint32_t i = 0; i < s.n_events; i++) {
    event_t *e = &s.events[i];
    if (!e->placed && e->at < s.read_end) {
      e->at = frame_of(e->at, fb);
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

//--------------------------------------------------------------------+
// The receive FIFO
//--------------------------------------------------------------------+

typedef struct {
  uint32_t count;  // bytes to read
  uint64_t at;     // the first one's place in the bytes written
  uint64_t next;   // where the segment after this stream's began; UINT64_MAX not yet
} fifo_state_t;

// Brings the bytes read in line with the FIFO, with interrupts off, and
// returns the bytes it holds. An overflowed FIFO drops its oldest bytes
// here, as the interrupt counted, rather than in the next read. No FIFO
// (after a bus reset) is an empty one.
static uint16_t fifo_sync(tu_fifo_t *ff) {
  uint16_t count = 0;
  if (ff != NULL) {
    count = tu_ff_overflow_count(ff->depth, ff->wr_idx, ff->rd_idx);
    if (count > ff->depth) {
      tu_fifo_correct_read_pointer(ff);
      count = ff->depth;
    }
  }
  fifo_read_all = fifo_written - fifo_skipped - count;
  fifo_read = (uint32_t) fifo_read_all;
  return count;
}

// TinyUSB cleared the FIFO (a new alt setting, a bus reset): the bytes not
// read so far are gone, and must not count against the next packets.
static void fifo_cleared(void) {
  board_irq_disable();
  (void) fifo_sync(tud_audio_get_ep_out_ff());
  board_irq_enable();
}

// The FIFO before a read
static void fifo_state(tu_fifo_t *ff, fifo_state_t *f) {
  board_irq_disable();
  uint16_t count = fifo_sync(ff);
  f->count = count;
  f->at = fifo_written - count;
  f->next = isr.seq == s.seq ? UINT64_MAX : isr_prev.seq == s.seq ? isr.start : isr_prev.start;
  board_irq_enable();
}

// Reads n bytes into buf. The FIFO's read index and the bytes read move
// together, so the interrupt never sees one without the other.
static uint32_t fifo_read_n(tu_fifo_t *ff, uint32_t n) {
  n = tu_fifo_peek_n(ff, buf, (uint16_t) n);
  board_irq_disable();
  tu_fifo_advance_read_pointer(ff, (uint16_t) n);
  fifo_read_all += n;
  fifo_read = (uint32_t) fifo_read_all;
  board_irq_enable();
  return n;
}

// Reads the receive FIFO, up to where the interrupt began a segment after
// the stream's; in a stream, the frames go to the checker too. True if it
// read anything.
static bool drain(bool check) {
  uint32_t fb = frame_bytes();
  bool any = false;
  if (fb == 0) {
    return any;
  }
  // Bounded, so that tud_task() runs between bursts
  for (int i = 0; i < 4; i++) {
    tu_fifo_t *ff = tud_audio_get_ep_out_ff();
    if (ff == NULL) {
      return any;  // a bus reset closed the audio function
    }
    fifo_state_t f;
    fifo_state(ff, &f);
    if (check && f.at < s.start) {
      // Bytes before the stream's segment, not read in time: they go
      uint64_t skip = s.start - f.at;
      skip = skip < f.count ? skip : f.count;
      (void) fifo_read_n(ff, (uint32_t) (skip < sizeof(buf) ? skip : sizeof(buf)));
      any = true;
      continue;
    }
    uint64_t limit = f.count;
    if (f.next != UINT64_MAX) {
      // The interrupt began the next segment: this stream's bytes end there.
      // Outside a stream too, the next one's first packet stays for it.
      limit = f.next > f.at ? f.next - f.at : 0;
    }
    uint32_t n = f.count < limit ? f.count : (uint32_t) limit;
    if (n > sizeof(buf)) {
      n = sizeof(buf);
    }
    n -= n % fb;
    if (n == 0) {
      return any;
    }
    n = fifo_read_n(ff, n);
    any = true;
    uint32_t frames = n / fb;
    const uint32_t *w = probe((const uint8_t *) buf, frames);
    if (check) {
      // The counters and events after the read cover every frame read
      (void) take(false);
      place(f.at - s.start, frames, fb);
      // An overflow since the last chunk, during this read included,
      // overwrote frames of this chunk: the checker hears of it before it
      // sees them.
      tell_overflows();
      uint32_t t0 = board_cycles();
      bpc1_checker_feed(&ck, w, frames);
      s.ck_cycles += board_cycles() - t0;
      s.ck_frames += frames;
      s.fed += frames;
    }
  }
  return any;
}

// The stream's frames still in the FIFO, a FIFO's worth at most
static void drain_all(void) {
  for (uint32_t i = 0; i <= FIFO_BYTES / (4u * sizeof(buf)) && drain(true); i++) {
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
               : verdict == BPC1_OVERRUN         ? &status.run_overrun
                                                 : &status.run_unknown;
  if (!*flag) {
    *flag = true;
    status_changed();
  }
}

// The run's results are cleared at once. A result taken before now whose
// line still waits belongs to the run before: "run start" follows its line.
void stream_run_start(void) {
  status.run_pass = status.run_fail = status.run_unknown = status.run_overrun = false;
  memset(status.run_count, 0, sizeof(status.run_count));
  status.run_results_lost = 0;
  status_changed();
  status.last_in_run = false;
  if (s.held) {
    run_starts++;
  } else {
    log_line_reserved("run start");
  }
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

  // The last result, as the line gives it; a "run start" that waited for
  // this line puts it in the run before
  status.last_known = true;
  status.last_in_run = run_starts == 0;
  status.last_verdict = s.held_r.verdict;
  status.last_end = s.held_r.end;
  status.last_lost = s.held_lost;
  status.last_overflows = s.held_overflows;
  bpc1_diff_text(&s.held_r, status.last_diff, sizeof(status.last_diff));
  status.last_kind = s.held_r.kind;
  status.last_depth = s.held_r.depth;
  status.last_rate = s.held_r.rate;
  if (status.file_diff_known && s.held_r.first_frame == s.diff_file) {
    status.file_diff_known = false;
  }
  status_changed();

  for (; run_starts > 0; run_starts--) {
    log_line_reserved("run start");
  }
}

// Each finished result takes the events before its end frame. Its line
// waits for the next result, for the checker to read past the extents
// finished so far (release()) or to open a newer file (watch()), or for the
// stream's stop, which gives the last line the stream's events after it.
// While the interrupt keeps a hole (take()), the results wait for it,
// unless `all`.
static void pop_results(bool all) {
  bpc1_report_t r;
  while ((all || !s.kept) && bpc1_checker_pop(&ck, &r)) {
    log_held();
    s.held_r = r;
    charge(r.first_frame + r.frames, &s.held_lost, &s.held_overflows);
    s.held = true;
    run_note(r.verdict);
    if (r.verdict < TU_ARRAY_SIZE(status.run_count)) {
      status.run_count[r.verdict]++;
      status_changed();
    }
    // A result for a later file than the one with the difference: that
    // file's result was dropped, or is this one
    if (status.file_diff_known && r.first_frame > s.diff_file) {
      status.file_diff_known = false;
      status_changed();
    }
  }
  uint32_t dropped = bpc1_checker_results_lost(&ck);
  if (dropped != s.results_lost) {
    status.run_results_lost += dropped - s.results_lost;
    s.results_lost = dropped;
    status_changed();
  }
}

// Frames past the extents finished so far belong to a result still to
// come, unless they are allowed silence: the waiting line can go.
static void release(void) {
  if (s.held && s.fed > bpc1_checker_covered(&ck)) {
    log_held();
  }
}

uint32_t stream_backlog_us(void) {
  uint32_t per_s = frame_bytes() * status.rate;
  if (!s.active || per_s == 0) {
    return 0;
  }
  board_irq_disable();
  uint64_t waiting = fifo_written - fifo_skipped - fifo_read_all;
  board_irq_enable();
  return (uint32_t) (waiting * 1000000u / per_s);
}

uint32_t stream_packets(void) {
  return (uint32_t) isr.packets;  // one word: no tear
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
    status.file_kind = r.kind;
    status.file_depth = r.depth;
    status.file_rate = r.rate;
    status_changed();
  }
  if (r.verdict == BPC1_NOT_BIT_PERFECT) {
    run_note(r.verdict);  // red from the file's first difference on
    if (!status.file_diff_known || s.diff_file != r.first_frame) {
      status.file_diff_known = true;
      s.diff_file = r.first_frame;
      bpc1_diff_text(&r, status.file_diff, sizeof(status.file_diff));
      status_changed();
    }
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
  s.start = c->start;
  s.fed = 0;
  s.read_end = 0;
  s.map_at = 0;
  s.map_frame = 0;
  s.kept = false;
  s.n_events = 0;
  s.lost_charged = 0;
  s.overflows_charged = 0;
  s.held = false;
  s.locked = false;
  s.stream_results = 0;
  s.results_lost = 0;
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
  (void) take(true);
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
  pop_results(true);
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
  status.lost = (uint32_t) (net_lost(c) > 0 ? net_lost(c) : 0);
  status.overflows = c->overflows;
  status.file_diff_known = false;
  status.file_kind = 0;
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
  // TinyUSB does not call tud_umount_cb() for it (usbd.c, DCD_EVENT_BUS_RESET):
  // the stream ends here, and the device is no longer set up, also when no
  // alt setting was open. tud_mount_cb() sets USB_MOUNTED only once
  // tud_mounted() holds, so an enumeration never looks like a reset.
  if (!tud_mounted() && (s.active || status.alt != 0 || status.usb == USB_MOUNTED)) {
    stream_reset();
    status.usb = USB_NOT_MOUNTED;
    status_changed();
    log_line("usb reset");
  }

  counters_t c = snapshot();
  uint32_t now = board_millis();

  if (c.packets != 0 && c.seq != s.seq) {
    // A new stream began without the old one being stopped: the old one's
    // frames still in the FIFO go to its checker first
    if (s.active) {
      drain_all();
    }
    stop("pause");
    if (status.slot != 0) {
      start(&c, now);
    } else {
      s.seq = c.seq;  // packets outside an alt setting: not a stream
    }
  }
  if (!s.active) {
    (void) drain(false);  // whatever arrives outside a stream is not probed
    memset(&s.window, 0, sizeof(s.window));
    return;
  }

  if (c.packets != s.idle_packets) {
    s.idle_packets = c.packets;
    s.last_packet_ms = now;
  }
  (void) take(false);
  (void) drain(true);
  pop_results(false);
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
      drain_all();
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
  fifo_cleared();
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
  fifo_cleared();
  status.alt = 0;
  status.slot = 0;
  status.resolution = 0;
  status_changed();
  isr_restart = true;
}

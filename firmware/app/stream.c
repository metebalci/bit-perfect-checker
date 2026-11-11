// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Stream probe and BPC1 checker.
//
// The USB interrupt counts packets, bytes and microframes. u2ps gives each
// packet's own microframe, and reports the microframes without a packet
// before the packet that ends them, with a reason (stream_hole_isr()):
// missed microframes (the host sent nothing, the device re-armed late, a
// corrupted packet; a zero-length packet counts as one too), and overflows:
// packets u2ps had no buffer for, because the main loop still held every
// one. No two packets share a microframe, so "doubled" stays 0.
//
// No copy: each packet stays in the buffer of class memory that u2ps lent
// it (app/u2ps_glue.c). The interrupt queues the packet with the holes
// before it, and the main loop reads the queue in order: it probes the
// samples for the bits in use and for DoP markers, feeds every frame to the
// BPC1 checker engine (bpc1/bpc1_checker.h), which starts over with each
// stream, and then gives the buffers back to u2ps in the same order
// (uac2_release()). The depth is the number of buffers, one packet each
// (125 us): when the main loop falls further behind, u2ps has no buffer for
// the packets that come, and reports them as an overflow before the next
// one. The checker hears of it before it sees that packet's frames, and a
// file it falls in is OVERRUN (docs/bpc1.md 12.6). The main loop logs a stat
// line every second, a lock line for each file found, and a result line for
// each file and each stretch of unknown signal the checker finished; the
// last ones come when the stream stops. The run's results go to the LEDs.
//
// Each result line counts the lost packets and overflows that fall in its
// frames, by their place in the stream: a packet's holes fall before its
// first frame. A result takes the events before its end frame. Its line
// waits until a later result is sure to come, so that the stream's last
// line can take what is left: the lines add up to the stream stop line.
//
// The interrupt begins a new segment of the stream at the first packet
// after a pause, after the main loop stopped the stream, or after a change
// of the rate; the queue notes each packet's segment, and the main loop
// feeds the old stream's checker up to the new segment, over as many
// passes as that takes, before it starts the new one. When the host stops
// the stream (an alt change, alt 0, a bus reset, a detach, a VBUS loss, a
// formats change), the packets not read yet are checked too, over the next
// passes, since the glue lends their buffers again only at the next
// stream's open; if that open must come first, the glue cuts the stream
// there (stream_cut()): the packets still unread count as overflows at
// their place, and each segment still queued stops as a stream of its own.
// When the main loop is so far behind that the interrupt has no segment
// left to begin, the packets until it has one are no segment's: they go
// unread, as overflows (stream_packet_isr()).

#include "stream.h"

#include <stdatomic.h>
#include <stdbool.h>
#include <string.h>

#include "board.h"
#include "bpc1_checker.h"
#include "family.h"
#include "load.h"
#include "log.h"
#include "status.h"
#include "uac2.h"
#include "usb_descriptors.h"

#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

#define UFRAME_MASK   0x3FFFu
#define WINDOW_MS     1000u

// Counters of one stream, written by the USB interrupt.
typedef struct {
  uint32_t seq;        // segment number
  uint64_t packets;
  uint64_t bytes;
  uint64_t uframes;    // microframes from the first packet to the last
  uint64_t missed;     // microframes without a packet, overflows not included
  uint64_t holes;      // runs of such microframes
  uint64_t max_hole;   // the longest run
  uint64_t run;        // the run that the next packet ends, 0 for none
  uint64_t late;       // of the missed microframes: the device re-armed too late (u2ps LATE)
  uint64_t crc;        // of the missed microframes: corrupted packets (u2ps CORRUPTED)
  uint64_t overflows;  // packets u2ps had no buffer for (u2ps NO_BUFFER), and those of no segment
  uint32_t rate;       // the rate the host had set when the segment began
  uint8_t alt, slot, resolution;  // the alt setting it began at
  uint16_t min_bytes;
  uint16_t max_bytes;
} counters_t;

// The counters of the last SEGMENTS segments, each in slot seq % SEGMENTS:
// the main loop may be behind by several segments, and reads each one's
// counters until it has finished it. The interrupt begins a new segment
// only in a slot that is not the main loop's (main_seq); else the packets
// after the boundary are no segment's (isr_orphaned) until it can.
#define SEGMENTS 8u

static counters_t segments[SEGMENTS];
static counters_t *isr = &segments[0];  // the segment the interrupt counts in
static volatile uint32_t main_seq;      // the segment the main loop reads
static uint32_t isr_last_uframe;
static volatile bool isr_restart = true;
static volatile uint32_t isr_rate;        // the rate the next segment begins at (stream_set_rate())
static volatile uint8_t isr_alt;          // and the alt setting (stream_set_alt())
static volatile uint8_t isr_slot;
static volatile uint8_t isr_resolution;
static volatile uint32_t isr_lost_starts; // segment starts dropped: the main loop was SEGMENTS behind
static bool isr_orphaned;                 // since a dropped start: the packets are no segment's
static uint32_t isr_orphans;              // those packets, overflows of the segment they end up in

// The holes u2ps reported since the last packet, which go with the next
// packet's entry, for their place in the stream (32 bits, as there: a hole
// of more than 2^32 microframes, 6.2 days, counts in full in its
// segment's counters, but not in its place)
typedef struct {
  uint32_t missed;
  uint32_t overflows;
} pending_t;

static pending_t pending;

// The packets u2ps holds for the main loop, in arrival order: one entry per
// packet, so the queue needs no more entries than a stream has buffers
// (USB_BUFFERS_MAX). The main loop drops the entries of a stream u2ps
// stopped before the glue lends the class memory again (stream_set_alt()
// comes first, u2ps_glue.c), so the entries never outnumber the buffers,
// and every entry is of the stream the glue has open, if any.
typedef struct {
  const uint8_t *data;
  uint32_t seq;        // its segment
  uint32_t lost;       // microframes without a packet before it
  uint32_t overflows;  // packets u2ps had no buffer for before it
  uint16_t len;
  bool orphan;         // no segment's: not read, an overflow (isr_orphans)
} packet_t;

#define PACKETS USB_BUFFERS_MAX

static packet_t packets[PACKETS] FAMILY_USB_DATA;  // no zero start needed: written before read
static uint32_t pk_wi;               // the interrupt's write index
static volatile uint32_t pk_in;      // packets queued, counted since boot
static uint32_t pk_ri;               // the main loop's read index
static uint32_t pk_out;              // packets read and given back (or dropped)
static uint32_t pk_read;             // packets read since then, not given back yet

// The bytes of the packets queued, and of those the main loop has read, for
// the backlog and the load figures
static uint64_t bytes_in;
static volatile uint32_t bytes_out;  // the low word of bytes_out_all, for the interrupt
static uint64_t bytes_out_all;       // main loop only

// Missed microframes, in the run the next packet ends: the run is one
// hole, however many reports u2ps splits it into
static FAMILY_ISR_CODE void missed(uint64_t uframes) {
  isr->missed += uframes;
  if (isr->run == 0u) {
    isr->holes++;
  }
  isr->run += uframes;
  if (isr->run > isr->max_hole) {
    isr->max_hole = isr->run;
  }
  pending.missed += uframes;
}

// A hole counts at once, in the segment that runs: its microframes came
// after that segment's last packet, also when no packet of it follows (a
// pause, an alt change, a reset), and u2ps may report it in parts, at its
// tick's long gap. The next packet carries it as well, for its place in the
// stream. Called from the USB interrupt, and in the main loop with the USB
// vector masked, from u2ps_dev_tick() (uac2_task()) and from a detach, a
// re-init or a VBUS loss (u2ps_glue.c): never from inside this file, which
// reads what it writes only with interrupts off.
FAMILY_ISR_CODE void stream_hole_isr(uint32_t uframes, unsigned why) {
  if (why == U2PS_MISS_NO_BUFFER) {
    isr->overflows += uframes;
    pending.overflows += uframes;
    return;
  }
  missed(uframes);
  if (why == U2PS_MISS_LATE) {
    isr->late += uframes;
  } else if (why == U2PS_MISS_CORRUPTED) {
    isr->crc += uframes;
  }
}

// The next packet begins a new segment. The packets since a segment start
// the interrupt dropped came before this boundary: overflows of the newest
// segment, at its end, where the main loop stopped reading. Also in the
// main loop, with interrupts off.
static FAMILY_ISR_CODE void boundary(void) {
  isr->overflows += isr_orphans;
  isr_orphans = 0;
  isr_restart = true;
}

FAMILY_ISR_CODE void stream_restart_isr(void) {
  boundary();
}

FAMILY_ISR_CODE void stream_packet_isr(const uint8_t *data, uint16_t n_bytes, uint32_t uframe) {
  packet_t *p = &packets[pk_wi];
  pk_wi = pk_wi + 1u < PACKETS ? pk_wi + 1u : 0u;
  p->data = data;
  p->len = n_bytes;
  p->lost = 0;
  p->overflows = 0;
  p->orphan = false;
  uint32_t gap = (uframe - isr_last_uframe) & UFRAME_MASK;
  isr_last_uframe = uframe;
  if (n_bytes == 0) {
    // A zero-length packet carries no frames (UAC2 calls it a transfer
    // delimiter): it counts like a microframe without a packet, in the hole
    // before the next packet, and its microframe in the segment's, so that
    // a run of them longer than the microframe numbers wrap still counts.
    // Queued all the same: u2ps holds it.
    if (!isr_restart && !isr_orphaned) {
      isr->uframes += gap;
    }
    missed(1u);
    p->seq = isr->seq;
    atomic_signal_fence(memory_order_release);  // the entry before the count
    pk_in++;
    return;
  }

  if (isr_restart || isr_orphaned) {
    // The holes before a segment's first packet counted in the segment
    // they ended
    uint32_t seq = isr->seq + 1u;
    if (seq - main_seq < SEGMENTS) {
      isr = &segments[seq % SEGMENTS];
      memset(isr, 0, sizeof(*isr));
      isr->seq = seq;
      isr->rate = isr_rate;
      isr->alt = isr_alt;
      isr->slot = isr_slot;
      isr->resolution = isr_resolution;
      isr->min_bytes = n_bytes;
      isr->max_bytes = n_bytes;
      // The packets of no segment just before it, with no boundary
      // between (boundary()): overflows before its first frame
      isr->overflows = isr_orphans;
      p->overflows = isr_orphans;
      isr_orphans = 0;
      isr_orphaned = false;
    } else {
      // A pause, rate or alt boundary lost: the glue counts it as a lost
      // event (stream_lost_starts()), a fault. The packets are no
      // segment's until a segment can begin, tried at each packet.
      if (isr_restart) {
        isr_lost_starts++;
      }
      isr_orphaned = true;
    }
    isr_restart = false;
  } else {
    isr->uframes += gap;
    isr->run = 0;
    p->lost = pending.missed;
    p->overflows = pending.overflows;
  }
  memset(&pending, 0, sizeof(pending));
  p->seq = isr->seq;

  if (isr_orphaned) {
    // Queued in the newest segment's place, where the main loop stops
    // reading it; counted in none
    p->orphan = true;
    isr->run = 0;
    isr_orphans++;
  } else {
    isr->packets++;
    isr->bytes += n_bytes;
    if (n_bytes < isr->min_bytes) {
      isr->min_bytes = n_bytes;
    }
    if (n_bytes > isr->max_bytes) {
      isr->max_bytes = n_bytes;
    }
  }
  bytes_in += n_bytes;
  atomic_signal_fence(memory_order_release);  // the entry before the count
  pk_in++;
  load_fifo((uint32_t) bytes_in - bytes_out);
}

// The counters of the newest segment
static counters_t snapshot(void) {
  board_irq_disable();
  counters_t c = *isr;
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
  uint32_t fifo_max;    // us of audio, the most received and not read yet
} figures_t;

// Lost packets or overflows of the stream, before the checker's frame `at`
typedef struct {
  uint64_t at;
  int64_t lost;
  uint64_t overflows;
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
  int64_t lost;
  uint64_t overflows;
} result_t;

static struct {
  bool active;
  uint32_t seq;
  uint32_t rate;        // the rate the stream started with, its segment's
  uint8_t alt, slot, resolution;  // the alt setting it started at, its segment's
  uint64_t cut;         // packets not read when the stream was cut, counted as overflows
  counters_t last;      // latest counters of the active stream
  uint64_t idle_packets;  // packets when stream_task() last saw them change
  uint32_t last_packet_ms;
  uint32_t window_ms;
  int64_t lost_logged;  // net lost packets already logged
  uint64_t overflows_logged;
  uint64_t overflows_fed;  // overflows the checker was told of
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
  // a frame that a packet ended in the middle of, waiting for its rest
  _Alignas(4) uint8_t part[8];
  uint32_t part_n;
  // lost packets and overflows, by where they fell
  uint64_t fed;             // frames given to the checker
  event_t events[EVENTS];
  uint32_t n_events;
  int64_t lost_charged;     // charged to results so far
  uint64_t overflows_charged;
  // results
  bool held;                // a result whose line waits (pop_results())
  bpc1_report_t held_r;
  int64_t held_lost;
  uint64_t held_overflows;
  uint32_t stream_results;  // result lines of this stream
  uint32_t results_lost;    // results the checker dropped, as far as the run counts know
  uint64_t diff_file;       // first frame of the file status.file_diff names
} s;

// The most frames one call of probe() takes: a packet of the largest size
// in the smallest frames (16/2), or one frame
#define FEED_FRAMES (U2PS_ISO_OUT_MPS_MAX / (2u * UAC2_CHANNELS))

static bpc1_checker_t ck;
static uint32_t words[2 * FEED_FRAMES];  // frames as the checker takes them
static uint32_t results;          // result lines since boot
static char result_text[LOG_LINE_MAX];
static uint32_t run_starts;       // "run start" lines that wait for the result line before them

// The stream's frames, or outside a stream the alt setting's
static uint32_t frame_bytes(void) {
  return (uint32_t) (s.active ? s.slot : status.slot) * UAC2_CHANNELS;
}

// Net lost packets: u2ps gives each packet its own microframe, so no
// doubled microframe takes a missed one back
static int64_t net_lost(const counters_t *c) {
  return (int64_t) c->missed;
}

// The display's counts (status.h) are 32 bits: a count beyond them stays at
// their largest value
static uint32_t count32(uint64_t n) {
  return n > UINT32_MAX ? UINT32_MAX : (uint32_t) n;
}

static uint32_t lost32(int64_t lost) {
  return count32(lost > 0 ? (uint64_t) lost : 0u);
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
  return (s.slot >= 3 && p->dop_frames * 2 > p->probe_frames) ? 1 : 0;
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
// channels interleaved: b itself for 4-byte slots on a word boundary, else
// in words[].
static const uint32_t *probe(const uint8_t *b, uint32_t frames) {
  uint32_t or_bits = 0;
  uint32_t dop = 0;
  uint32_t prev = s.last_marker;
  const uint32_t *out = words;

  switch (s.slot) {
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
    default:
      if (((uintptr_t) b & 3u) != 0) {
        // Only after a frame that a packet ended in the middle of
        for (uint32_t i = 0; i < frames; i++, b += 8) {
          uint32_t l = (uint32_t) b[0] | ((uint32_t) b[1] << 8) | ((uint32_t) b[2] << 16) | ((uint32_t) b[3] << 24);
          uint32_t r = (uint32_t) b[4] | ((uint32_t) b[5] << 8) | ((uint32_t) b[6] << 16) | ((uint32_t) b[7] << 24);
          probe_frame(l, r, &or_bits, &dop, &prev);
          words[2 * i] = l;
          words[2 * i + 1] = r;
        }
      } else {
        // Class memory and its stride are word-aligned (U2PS_DPORT_ALIGN)
        const uint32_t *w = (const uint32_t *) (const void *) b;
        for (uint32_t i = 0; i < frames; i++) {
          probe_frame(w[2 * i], w[2 * i + 1], &or_bits, &dop, &prev);
        }
        out = w;
      }
      break;
  }

  s.last_marker = prev;
  probe_add(&s.window, frames, or_bits, dop);
  return out;
}

//--------------------------------------------------------------------+
// Lost packets and overflows, by where they fell
//--------------------------------------------------------------------+

static void event_add(uint64_t at, int64_t lost, uint64_t overflows) {
  if (s.n_events == EVENTS) {
    event_t *newest = &s.events[EVENTS - 1u];
    newest->lost += lost;
    newest->overflows += overflows;
    return;
  }
  s.events[s.n_events++] = (event_t) {.at = at, .lost = lost, .overflows = overflows};
}

// The counters of segment seq into *c; false if the interrupt has used its
// slot for a later segment.
static bool take_segment(uint32_t seq, counters_t *c) {
  board_irq_disable();
  const counters_t *k = &segments[seq % SEGMENTS];
  bool same = k->seq == seq;
  if (same) {
    *c = *k;
  }
  board_irq_enable();
  return same;
}

// The counters of this stream's segment into s.last
static bool take(void) {
  return take_segment(s.seq, &s.last);
}

// The events that fall before the checker's frame `end`, out of the queue;
// with `at_end`, those at `end` too: a cut file's (12.6)
static void charge(uint64_t end, bool at_end, int64_t *lost, uint64_t *overflows) {
  uint32_t k = 0;
  *lost = 0;
  *overflows = 0;
  for (uint32_t i = 0; i < s.n_events; i++) {
    const event_t *e = &s.events[i];
    if (e->at < end || (at_end && e->at == end)) {
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

// Overflows the checker has not been told of: told at frame s.fed, where
// they count
static void tell_overflows(uint64_t overflows) {
  event_add(s.fed, 0, overflows);
  s.overflows_fed += overflows;
  bpc1_checker_overrun(&ck);
}

//--------------------------------------------------------------------+
// The packets in u2ps's buffers
//--------------------------------------------------------------------+

// The next packet not read yet, or NULL
static const packet_t *packet_next(void) {
  if (pk_in - pk_out == pk_read) {
    return NULL;
  }
  atomic_signal_fence(memory_order_acquire);  // the count before the entry
  return &packets[pk_ri];
}

// The packet packet_next() gave is read: its buffer goes back with
// give_back()
static void packet_done(const packet_t *p) {
  pk_ri = pk_ri + 1u < PACKETS ? pk_ri + 1u : 0u;
  pk_read++;
  bytes_out_all += p->len;
}

// The packets read since the last call go back to u2ps in order, with
// interrupts off, so that u2ps cannot stop their stream in between: if it
// stopped it before, it has their buffers back already, and the glue gives
// back nothing (uac2_release()).
static void give_back(void) {
  if (pk_read == 0) {
    return;
  }
  board_irq_disable();
  pk_out += pk_read;
  bytes_out = (uint32_t) bytes_out_all;
  uac2_release(pk_read);
  board_irq_enable();
  pk_read = 0;
}

// Frames to the checker, after the probe
static void feed_frames(const uint8_t *b, uint32_t frames) {
  const uint32_t *w = probe(b, frames);
  uint32_t t0 = board_cycles();
  bpc1_checker_feed(&ck, w, frames);
  s.ck_cycles += board_cycles() - t0;
  s.ck_frames += frames;
  s.fed += frames;
}

// A packet's bytes to the checker in whole frames. A frame that the packet
// ends in the middle of waits in s.part for its rest from the next packet,
// as in a byte stream: a host that sends whole frames, as UAC2 requires,
// never leaves one.
static void feed(const uint8_t *b, uint32_t n, uint32_t fb) {
  if (s.part_n > 0) {
    uint32_t k = fb - s.part_n < n ? fb - s.part_n : n;
    memcpy(&s.part[s.part_n], b, k);
    s.part_n += k;
    b += k;
    n -= k;
    if (s.part_n < fb) {
      return;
    }
    feed_frames(s.part, 1);
    s.part_n = 0;
  }
  uint32_t frames = n / fb;
  if (frames > 0) {
    feed_frames(b, frames);
  }
  s.part_n = n - frames * fb;
  memcpy(s.part, b + frames * fb, s.part_n);
}

// At most this much per call, so that the USB stack's task runs between
#define DRAIN_BYTES   16384u
#define DRAIN_PACKETS 256u

// Reads the packets in order, up to the first of a segment after the
// stream's; in a stream, their frames go to the checker. Returns the bytes
// read.
static uint32_t drain(bool check) {
  uint32_t fb = frame_bytes();
  uint32_t bytes = 0;
  for (uint32_t i = 0; i < DRAIN_PACKETS && bytes < DRAIN_BYTES; i++) {
    const packet_t *p = packet_next();
    if (p == NULL) {
      break;
    }
    if (p->seq != s.seq && (int32_t) (p->seq - s.seq) > 0) {
      break;  // the next segment's first packet stays for it, outside a stream too
    }
    // A packet of an older segment, not read in time, goes unread, and so
    // does one of no segment (an overflow, counted by the interrupt), where
    // the stream's segment was cut
    if (check && fb != 0 && p->seq == s.seq && !p->orphan) {
      if (p->lost > 0) {
        event_add(s.fed, p->lost, 0);
      }
      if (p->overflows > 0) {
        tell_overflows(p->overflows);
      }
      feed(p->data, p->len, fb);
    }
    bytes += p->len;
    packet_done(p);
  }
  give_back();
  return bytes;
}

// True while a packet of the stream waits to be read
static bool unread(void) {
  const packet_t *p = packet_next();
  return p != NULL && p->seq == s.seq;
}

// The segment after s.seq whose packets come next, or 0 for none yet
static uint32_t next_segment(void) {
  const packet_t *p = packet_next();
  return p != NULL && (int32_t) (p->seq - s.seq) > 0 ? p->seq : 0u;
}

// The packets of the stream's segment s.seq, and of older ones, go unread,
// up to the one counted `end` (pk_in); returns how many of them carried
// audio and were some segment's
static uint64_t skip(uint32_t end) {
  uint64_t n = 0;
  const packet_t *p;
  while ((int32_t) (end - (pk_out + pk_read)) > 0 && (p = packet_next()) != NULL && (int32_t) (p->seq - s.seq) <= 0) {
    if (p->len > 0u && !p->orphan) {
      n++;
    }
    packet_done(p);
  }
  give_back();
  return n;
}

// The packets queued by now go unread and uncounted (stream_drop())
static void drop_all(void) {
  uint32_t end = pk_in;
  const packet_t *p;
  while ((int32_t) (end - (pk_out + pk_read)) > 0 && (p = packet_next()) != NULL) {
    packet_done(p);
  }
  give_back();
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
  f->fifo_max = (fb && s.rate) ? (uint32_t) ((uint64_t) l.fifo_max * 1000000u / (fb * s.rate)) : 0;
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
  log_append(out, size, &n, " lost=%lld ovf=%llu dur=%lu.%03lu", (long long) x->lost, (unsigned long long) x->overflows,
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
    .rate = s.rate,
    .alt = s.alt,
    .slot = s.slot,
    .resolution = s.resolution,
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
  status.last_lost = s.held_lost > INT32_MAX ? INT32_MAX : (int32_t) s.held_lost;
  status.last_overflows = count32(s.held_overflows);
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

// Each finished result takes the events before its end frame, a cut file
// those at that frame too, before the next file's first X-sync. Its line
// waits for the next result, for the checker to read past the extents
// finished so far (release()) or to open a newer file (watch()), or for the
// stream's stop, which gives the last line the stream's events after it.
static void pop_results(void) {
  bpc1_report_t r;
  while (bpc1_checker_pop(&ck, &r)) {
    log_held();
    s.held_r = r;
    charge(r.first_frame + r.frames, r.cut, &s.held_lost, &s.held_overflows);
    s.held = true;
    run_note(r.verdict);
    if (r.verdict < ARRAY_SIZE(status.run_count)) {
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
  uint32_t per_s = frame_bytes() * s.rate;
  if (!s.active || per_s == 0) {
    return 0;
  }
  board_irq_disable();
  uint64_t waiting = bytes_in - bytes_out_all;
  board_irq_enable();
  return (uint32_t) (waiting * 1000000u / per_s);
}

uint32_t stream_packets(void) {
  return (uint32_t) isr->packets;  // the low word, one load: no tear; the screen looks only for a change
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
  s.rate = c->rate;
  s.alt = c->alt;
  s.slot = c->slot;
  s.resolution = c->resolution;
  s.cut = 0;
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
  s.part_n = 0;
  s.fed = 0;
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
           (unsigned long) s.rate, s.alt, s.slot, s.resolution,
           status.allow_silence ? "true" : "false");

  bpc1_checker_init(&ck, s.rate, (uint8_t) (s.slot * 8u));
  bpc1_checker_allow_silence(&ck, status.allow_silence);
  s.window_cycles = board_cycles();
  (void) load_take();
}

static void stop(const char *reason) {
  if (!s.active) {
    return;
  }
  // Packets of no segment that no segment followed: this stream's
  // overflows, at the frame where it was cut
  board_irq_disable();
  if (isr->seq == s.seq) {
    isr->overflows += isr_orphans;
    isr_orphans = 0;
  }
  board_irq_enable();
  (void) take();
  memset(&s.window, 0, sizeof(s.window));
  counters_t last = s.last;
  last.overflows += s.cut;  // the packets a cut left unread
  const counters_t *c = &last;
  uint32_t fb = frame_bytes();
  uint64_t ms = (c->uframes + 1u) / 8u;
  uint32_t r = rate_x100(c);

  // The last file and the unknown signal after it. The stream stop line
  // comes after them: the stream's last line.
  figures_take();
  // Overflows in packets not read, which went unread, still make the file
  // that was open OVERRUN
  if (c->overflows != s.overflows_fed) {
    tell_overflows(c->overflows - s.overflows_fed);
  }
  bpc1_checker_end(&ck);
  pop_results();
  // The stream's last result line: the events no result took, those whose
  // frames were not read or that fell after the last result included, if
  // that result holds the stream's end. Losses and overflows after a
  // complete file, where the stream ended inside its extent, or in allowed
  // silence that gave no result, touched no result (12.6): on the stream
  // stop line only
  if (bpc1_checker_end_held(&ck)) {
    s.held_lost += net_lost(c) - s.lost_charged;
    s.held_overflows += c->overflows - s.overflows_charged;
  }
  log_held();

  log_line_reserved("stream stop id=%lu reason=%s dur=%lu.%03lu frames=%llu pkts=%llu rate=%lu.%02lu fpp=%u-%u "
           "lost=%lld missed=%llu holes=%llu maxhole=%llu doubled=0 late=%llu crc=%llu ovf=%llu outside=%llu "
           "silent=%llu results=%lu results_lost=%lu",
           (unsigned long) status.stream_id, reason, (unsigned long) (ms / 1000u),
           (unsigned long) (ms % 1000u), (unsigned long long) (c->bytes / fb), (unsigned long long) c->packets,
           (unsigned long) (r / 100u), (unsigned long) (r % 100u), (unsigned) (c->min_bytes / fb),
           (unsigned) (c->max_bytes / fb), (long long) net_lost(c), (unsigned long long) c->missed,
           (unsigned long long) c->holes, (unsigned long long) c->max_hole, (unsigned long long) c->late,
           (unsigned long long) c->crc, (unsigned long long) c->overflows,
           (unsigned long long) bpc1_checker_outside(&ck),
           (unsigned long long) bpc1_checker_silent(&ck), (unsigned long) s.stream_results,
           (unsigned long) bpc1_checker_results_lost(&ck));

  s.active = false;
  s.part_n = 0;
  status.lost = lost32(net_lost(c));
  status.overflows = count32(c->overflows);
  status.file_diff_known = false;
  status.file_kind = 0;
  // The interrupt may have begun the next segment already (a host gap just
  // over the idle time): a restart now would cut that segment short.
  board_irq_disable();
  if (isr->seq == s.seq) {
    boundary();
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

  int64_t lost = net_lost(c);
  if (lost > s.lost_logged) {
    log_line("lost packets=%lld total=%lld", (long long) (lost - s.lost_logged), (long long) lost);
    s.lost_logged = lost;
  }
  if (c->overflows > s.overflows_logged) {
    log_line("overflow packets=%llu total=%llu", (unsigned long long) (c->overflows - s.overflows_logged),
             (unsigned long long) c->overflows);
    s.overflows_logged = c->overflows;
  }

  figures_take();
  const figures_t *f = &s.figures;
  uint32_t r = rate_x100(c);
  log_line("stat id=%lu rate=%lu.%02lu frames=%lu pkts=%llu bits=%d dop=%d lost=%lld ovf=%llu isr_pct=%lu.%lu "
           "isr_max=%lu ck_cpf=%lu ck_pct=%lu.%lu loop_max=%lu fifo_max=%lu",
           (unsigned long) status.stream_id, (unsigned long) (r / 100u), (unsigned long) (r % 100u),
           (unsigned long) s.window.probe_frames, (unsigned long long) c->packets, bits, dop, (long long) lost,
           (unsigned long long) c->overflows, (unsigned long) (f->isr_pct10 / 10u),
           (unsigned long) (f->isr_pct10 % 10u), (unsigned long) f->isr_max, (unsigned long) f->ck_cpf,
           (unsigned long) (f->ck_pct10 / 10u), (unsigned long) (f->ck_pct10 % 10u),
           (unsigned long) f->loop_max, (unsigned long) f->fifo_max);

  if (status.bits != bits || status.dop != (dop != 0) || status.lost != lost32(lost)
      || status.overflows != count32(c->overflows)) {
    status.bits = (uint8_t) bits;
    status.dop = dop != 0;
    status.lost = lost32(lost);
    status.overflows = count32(c->overflows);
    status_changed();
  }
  memset(&s.window, 0, sizeof(s.window));
}

// The segment that ran when the rate last changed: its stream ends there,
// once its packets are read
static bool rate_ends;
static uint32_t rate_end_seq;

// The segment that ran when the host last ended the stream: an alt
// setting ("alt"), a bus reset, a detach, a VBUS loss or a formats change
// ("reset"). It and the segments before it end once their packets are
// read, the last one for that, each one before it at the pause that
// followed it. The first end of a segment names it: a reset after alt 0,
// or an alt setting after a reset, ends no more.
static bool host_ends;
static uint32_t host_end_seq;
static const char *host_end_reason;

// With interrupts off: the newest segment ends for reason
static void host_end(const char *reason) {
  if (!host_ends || host_end_seq != isr->seq) {
    host_end_seq = isr->seq;
    host_end_reason = reason;
  }
  host_ends = true;
  boundary();
}

// Why the stream ends, when no packet of it comes any more
static const char *end_reason(bool rate_end) {
  return host_ends && host_end_seq == s.seq ? host_end_reason : rate_end ? "rate" : "pause";
}

// The host ended the stream's segment or a later one
static bool host_ended(void) {
  return host_ends && (int32_t) (host_end_seq - s.seq) >= 0;
}

// The stream stops for why it ends
static void end_stream(void) {
  bool rate_end = rate_ends && rate_end_seq == s.seq;
  stop(end_reason(rate_end));
  rate_ends = rate_ends && !rate_end;
}

// The next segment after a stopped stream, once the packets before it are
// read: a stream, or packets outside an alt setting. False if there is
// none yet.
static bool begin(uint32_t now) {
  uint32_t next = next_segment();
  counters_t c;
  if (next == 0u || !take_segment(next, &c)) {
    return false;
  }
  main_seq = next;
  if (c.slot != 0) {
    start(&c, now);
  } else {
    s.seq = next;  // packets outside an alt setting: not a stream
  }
  return true;
}

void stream_task(void) {
  uint32_t now = board_millis();
  if (!s.active) {
    (void) drain(false);  // whatever arrives outside a stream is not checked
    memset(&s.window, 0, sizeof(s.window));
    (void) begin(now);
    if (!s.active) {
      return;
    }
  }

  // A pass that read audio is no idle time, also when the packets came long
  // before; zero-length packets carry none
  uint32_t read = drain(true);
  (void) take();
  if (read > 0 || s.last.packets != s.idle_packets) {
    s.idle_packets = s.last.packets;
    s.last_packet_ms = now;
  }
  pop_results();
  release();

  // The interrupt began a later segment, or the rate changed: no packet of
  // the stream comes any more, and it ends once those that came are read
  counters_t c = snapshot();
  bool rate_end = rate_ends && rate_end_seq == s.seq;
  if ((c.seq != s.seq || rate_end || host_ended()) && !unread()) {
    end_stream();
    (void) begin(now);
  } else if (now - s.last_packet_ms >= STREAM_IDLE_MS && uac2_quiet()) {
    // u2ps saw its long gap at the last tick, and reported every hole of
    // the stream up to then (uac2_quiet())
    stop("idle");
  } else if (now - s.window_ms >= WINDOW_MS) {
    s.window_ms += WINDOW_MS;
    report();
  } else if (now - s.watch_ms >= WATCH_MS) {
    s.watch_ms = now;
    watch();
  }
}

// A stream's checker keeps the rate the stream started with: the packets
// that came at the old rate are read over the next passes, as before a
// pause, and the next packet begins a new segment at the new rate. So the
// glue can answer the host at once (u2ps_glue.c, rate()).
void stream_set_rate(uint32_t rate) {
  if (rate != status.rate) {
    board_irq_disable();
    isr_rate = rate;
    boundary();
    rate_end_seq = isr->seq;
    board_irq_enable();
    rate_ends = true;
  }
  status.rate = rate;
  status_changed();
  log_line("rate %lu", (unsigned long) rate);
}

uint32_t stream_lost_starts(void) {
  return isr_lost_starts;
}

bool stream_ending(void) {
  return packet_next() != NULL;
}

bool stream_end_held(void) {
  return bpc1_checker_end_held(&ck);
}

bool stream_queue_empty(void) {
  return pk_in == pk_out + pk_read;
}

// Every packet that waits goes unread: the stream's count as its overflows,
// which stop() tells the checker of at the frame it has read up to; each
// segment queued after it stops as a stream of its own, with its lines,
// all its packets overflows; packets outside an alt setting are no
// stream's. Also with no stream active (display_wait() runs no
// stream_task()). Only the packets queued when it starts: one queued after
// is a fault the glue catches (stream_queue_empty()).
void stream_cut(void) {
  uint32_t now = board_millis();
  uint32_t end = pk_in;
  for (;;) {
    uint64_t n = skip(end);
    if (s.active) {
      s.cut += n;
      end_stream();
    }
    if (pk_out == end || !begin(now)) {
      break;
    }
  }
}

uint32_t stream_drop(void) {
  uint32_t n = pk_in - (pk_out + pk_read);
  drop_all();
  return n;
}

void stream_set_alt(uint8_t alt) {
  status.alt = alt;
  status.slot = uac2_alts[alt].slot;
  status.resolution = uac2_alts[alt].resolution;
  status_changed();
  board_irq_disable();
  isr_alt = alt;
  isr_slot = status.slot;
  isr_resolution = status.resolution;
  host_end("alt");
  board_irq_enable();
  if (alt == 0) {
    log_line("alt 0");
  } else {
    log_line("alt %u slot=%u res=%u", alt, status.slot, status.resolution);
  }
}

// The stream ends as at alt 0: u2ps has stopped it, and the glue lends its
// memory again only at the next open, so the packets not read yet are
// checked over the next passes, unless that open cuts it first
void stream_reset(void) {
  status.alt = 0;
  status.slot = 0;
  status.resolution = 0;
  status_changed();
  board_irq_disable();
  isr_alt = 0;
  isr_slot = 0;
  isr_resolution = 0;
  host_end("reset");
  board_irq_enable();
}

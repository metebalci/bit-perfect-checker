// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// BPC1 v1 checker that proves a file arrived bit-perfect (docs/bpc1.md
// section 12.6, verdict rules a to d), fed incrementally with the received
// frames. Portable C99, static memory only: the caller owns one
// bpc1_checker_t (about 9 KB, in .bss).
//
// Input: frames of BPC1_MAX_CH 32-bit left-justified words, channels
// interleaved, as the device receives them. The transport (PCM or DoP) is
// found from the stream (12.1). Results do not depend on how the frames are
// split across calls.
//
// The X-sync search runs on every frame as it arrives; the comparison trails
// the newest frame by 16 frames. A file's body starts at the X-sync of the
// block after its last lead-in X-sync (or at block 0), and from there every
// unit is compared with the expected one, in every channel, X-syncs and
// R-header units included. The chips of the block count (r5) and its CRC (r6)
// are checked against their two possible values, and settled when the file
// ends. At a file's first difference the comparison of that file stops; only
// its end and the next file are looked for. Each file of the stream gets its
// own report: hosts join same-rate files into one stream. A file ends at the
// end of its LAST block, at an X-sync of another identity, where the block
// index goes back, and at an X-sync that is not TAIL after a TAIL one (end
// BACK).
//
// A file's extent runs from its block 0 to the end of its last block, at the
// positions its X-syncs imply, plus one block at each end; the count of
// blocks, its declared size, comes from its first R-header that passes
// section 6, decoded at its chip centres, else from its X-sync flagged LAST.
// A file that completes its LAST block holds the units to that block's end;
// one cut before (the next file's first X-sync, its extent's end or the
// stream's end) holds them to the cut, and an overrun there. Every received
// frame outside every extent is unknown signal: each stretch of them gets an
// UNKNOWN_SIGNAL report (OVERRUN if the FIFO overflowed in it). Only
// positions are kept for this, no frames. With allow_silence, a stretch of silence only
// (PCM words 0; DoP frames with valid markers, DSD bytes 0x69 or 0x96, zero
// bits below the 24) gives no report; frames outside the extents known so
// far are checked for it as they arrive.

#ifndef BPC1_CHECKER_H
#define BPC1_CHECKER_H

#include "bpc1.h"

#define BPC1_RING_FRAMES  1024u  // received frames kept; a power of two
#define BPC1_MAX_ANCHORS  8u     // X-syncs found, not yet reached by the comparison
#define BPC1_MAX_REPORTS  16u    // finished files not yet taken by the caller
#define BPC1_MAX_OVERRUNS 4u
#define BPC1_MAX_CAND     8u     // X-sync candidates waiting for their last frame

// Verdicts, in their order of precedence; only BIT_PERFECT is a pass.
typedef enum {
  BPC1_UNKNOWN_SIGNAL,   // frames outside every recognised file
  BPC1_BIT_PERFECT,      // rate, limits, body and words as the file's (a to d)
  BPC1_NOT_BIT_PERFECT,  // the file was found and a to d do not all hold
  BPC1_OVERRUN,          // the device's own receive FIFO overflowed before the
                         // file's first difference: not judged
} bpc1_verdict_t;

// How a file ended
typedef enum {
  BPC1_END_OPEN,  // not yet: bpc1_checker_peek()
  BPC1_END_LAST,  // the end of its block flagged LAST
  BPC1_END_NEXT,  // another file followed (an identity change)
  BPC1_END_BACK,  // the block index went back
  BPC1_END_STOP,  // the stream stopped first
} bpc1_end_t;

// The check that failed first: a diagnostic, not part of the result line
typedef enum {
  BPC1_WHY_NONE,
  BPC1_WHY_RATE,    // the received rate or channel count is not the file's (a)
  BPC1_WHY_START,   // no lead-in X-sync before the body, and the body not from block 0 (b)
  BPC1_WHY_FIRST,   // the first body block's X-sync did not arrive (b)
  BPC1_WHY_UNIT,    // a body unit differs (c, d)
  BPC1_WHY_MARKER,  // a DoP body frame's marker or padding differs (d)
  BPC1_WHY_CHIP,    // an r5 or r6 chip is neither of its two values
  BPC1_WHY_CRC,     // the r5 and r6 the chips spell fail the CRC
  BPC1_WHY_COUNT,   // r5 differs across channels or R-headers, from the file's size, or the
                    // first R-header's count disagrees with the file's blocks
  BPC1_WHY_LEFT,    // the stream left the body before its end
  BPC1_WHY_END,     // no intact tail X-sync after the body, and no LAST (b)
  BPC1_N_WHY,
} bpc1_why_t;

// One file's result. Numbers are 0 when unknown; an UNKNOWN_SIGNAL report
// has the stream's rate and slot, its stretch in first_frame and frames, and
// 0 for the file's fields.
typedef struct {
  uint8_t verdict;       // bpc1_verdict_t
  uint8_t end;           // bpc1_end_t
  uint8_t kind;          // the file's kind (BPC1_KIND_*), 0 without a lock
  bool dop;              // transport DoP, else PCM
  bpc1_fields_t id;      // identity, from the file's first X-sync
  uint32_t rate;         // the file's rate (DSD bit rate for DSD kinds)
  uint32_t rx_rate;      // received rate in the same unit: frame rate, or 16 x for DoP
  uint32_t host_rate;    // received frame rate
  uint8_t depth;         // file depth D (1 for DSD)
  uint8_t bits;          // bits in use of the received words over the body
  uint8_t slot_bits;
  bool diff_known;       // a first difference: NOT_BIT_PERFECT
  uint32_t diff_ms;      // its place in the file: source unit / file rate, in ms
  // diagnostics
  uint8_t why;           // bpc1_why_t
  uint64_t diff_unit;    // the source unit of the first difference
  int32_t body_k;        // the first body block, -1 if not placed
  uint64_t compared;     // body units compared
  uint64_t first_frame;  // received frame of the file's first X-sync
  uint64_t frames;       // received frames from there to the file's end
  uint32_t anchors;      // X-syncs of the file found
  bool cut;              // a file that did not complete its LAST block: it holds
                         // what stands before the frame its frames end at
} bpc1_report_t;

// Internal state below; callers use the functions only.

typedef struct {
  uint64_t h;     // X-sync start, in half-frames (PCM frame f: 2f; DoP byte u: u)
  uint8_t mode;
  uint8_t chans;  // bit c: channel c's X-sync is valid
  uint8_t flags;  // ORed over the valid channels
  bpc1_fields_t f;  // of the first valid channel
} bpc1_anchor_t;

// The r5 and r6 bits one R-header block's chips spelled, most significant
// first: the first chip of each Manchester pair, per channel
typedef struct {
  uint32_t k;
  uint8_t flags;
  uint8_t n;       // bits recorded
  bool crc_done;
  bool ok;         // the words judged and passed: the CRC, r5 the same throughout
  uint32_t w[BPC1_MAX_CH];
} bpc1_rrec_t;

typedef struct {
  bool active;
  bool done;
  uint8_t mode;
  uint8_t chans;
  uint8_t ok[BPC1_MAX_CH];
  int8_t prev[BPC1_MAX_CH];
  uint16_t q;
  uint32_t step;
  uint64_t next;   // unit index of the next chip centre
  uint64_t h;      // its block's X-sync, in half-frames
  uint64_t seen;   // half-frames received when it was decoded
  uint16_t r[BPC1_MAX_CH][7];
  bpc1_fields_t x[BPC1_MAX_CH];  // the X-sync's fields of each channel
  bpc1_fields_t f; // decoded: the R-header's fields and count
  uint16_t total;
} bpc1_rdec_t;

typedef struct {
  // received frames
  uint32_t ring[BPC1_RING_FRAMES][BPC1_MAX_CH];
  uint64_t in_frames;
  uint32_t host_rate;
  uint8_t slot_bits;
  // X-sync search on the newest frames
  struct {
    uint64_t start, due;
    uint8_t mode;
  } cand[BPC1_MAX_CAND];
  uint8_t ncand;
  uint64_t slow_at;  // the next frame with a candidate due or a chip centre
  bpc1_anchor_t q[BPC1_MAX_ANCHORS];
  uint8_t nq;
  bpc1_rdec_t rdec;
  uint64_t over[BPC1_MAX_OVERRUNS];  // in half-frames
  uint8_t nover;
  // the stream outside every file
  uint64_t cov_end;  // the frame where the extents seen so far end
  uint64_t outside;  // frames outside every extent
  bool end_held;     // the stream's last result holds its end
  bool fin_complete; // the file finished last completed its LAST block
  bool allow_silence;
  uint64_t silent;   // of those, frames in stretches of silence allowed
  uint64_t sil_from; // frames from here on are watched for silence
  uint64_t sil_scan; // the next frame to look at
  uint64_t sil_ns;   // the first frame from sil_from on that is not silence; UINT64_MAX none
  // the file
  bool active;
  uint8_t mode;
  bool dsd_f;
  uint8_t shift;   // half-frames per unit: 2 (PCM) or 1 (DoP), as a shift
  uint8_t state;
  bpc1_signal_t sig;
  bpc1_fields_t id;
  uint32_t U;      // units per block
  bool rate_ok;
  uint64_t p;      // comparison position, in units
  uint64_t first_h;
  uint32_t anchors;
  // its X-syncs so far
  uint32_t last_k;   // the highest block index
  uint32_t lastx_k;  // the latest X-sync: block, flags and frame
  uint8_t lastx_fl;
  uint64_t lastx_f;
  uint64_t last_u;   // and unit
  bool last_last;    // the latest X-sync is flagged LAST
  bool lead_seen;
  uint32_t lead_k;   // the last lead-in block, and where it was received
  uint64_t lead_u;
  bool end_known;    // where the LAST block ends
  uint64_t end_u;
  bool last_known;   // the block count from the LAST block
  uint16_t total;
  bool rcount_known; // the block count from the file's first decoded R-header
  uint16_t rcount;
  uint32_t rcount_k; // that R-header's block
  bool tail_seen;
  bool E_known;      // the last body block
  uint32_t E;
  // the body
  bool placed;
  uint32_t L;        // first body block
  uint64_t body_u;   // where it was received
  int64_t base;      // source unit = p + base
  bool blk_valid;
  bpc1_block_t blk;  // the block being compared
  uint32_t mk_even;  // DoP: the marker expected at even received frames, << 24
  uint32_t pend_k;   // WAIT: the block whose X-sync is neither body nor tail,
  uint32_t pend_t;   // and its first unit that differs from a body X-sync
  bool r5_known;     // the count the body's R-headers agree on
  uint16_t r5;
  bpc1_rrec_t rec_cur, rec_first, rec_fail;
  bool have_first, have_fail, rec_is_first;
  bool body_done;
  uint64_t compared;
  // the verdict
  bool failed;
  uint64_t diff_s;
  uint64_t diff_rx;  // where its unit was received (or the body was looked for)
  uint8_t why;
  bool overrun;
  uint64_t over_rx;  // where the first overrun was, in units
  // bits in use over the body, by received block from or_from on
  bool or_on;
  uint64_t or_from;
  uint32_t or_j, or_cur, or_prev;
  uint32_t or_hist[8];
  // finished files
  bpc1_report_t out[BPC1_MAX_REPORTS];
  uint8_t nout, out_head;
  uint32_t out_lost;
} bpc1_checker_t;

// slot_bits: bits per sample of the USB format (16, 24 or 32); DoP needs 24 or more.
void bpc1_checker_init(bpc1_checker_t *ck, uint32_t host_rate, uint8_t slot_bits);
// words: n_frames x BPC1_MAX_CH, left-justified.
void bpc1_checker_feed(bpc1_checker_t *ck, const uint32_t *words, uint32_t n_frames);
// The device's own receive FIFO overflowed before the next frame fed: the
// file there becomes OVERRUN.
void bpc1_checker_overrun(bpc1_checker_t *ck);
// Takes the oldest finished file's report; false if none.
bool bpc1_checker_pop(bpc1_checker_t *ck, bpc1_report_t *rep);
// End of stream: finishes the last file, and reports the stretch of unknown
// signal after it, if any. A stream without frames gives no report.
void bpc1_checker_end(bpc1_checker_t *ck);
// Frames of the stream outside every file's extent so far, and of those the
// frames in stretches of silence allowed
uint64_t bpc1_checker_outside(const bpc1_checker_t *ck);
uint64_t bpc1_checker_silent(const bpc1_checker_t *ck);
// The frame where the extents of the files finished so far, and the
// stretches of unknown signal reported so far, end: a frame from here on
// belongs to a report still to come, if to any
uint64_t bpc1_checker_covered(const bpc1_checker_t *ck);

// After bpc1_checker_end(): the stream's last result holds the stream's
// end, so what came after the units it was told of (an overflow, lost
// packets) is that result's. Not so after a complete file, where the stream
// ended inside that file's extent, nor where a stretch of allowed silence
// gave no result (12.6).
bool bpc1_checker_end_held(const bpc1_checker_t *ck);
// Reports of the stream dropped because the caller had not taken them
// before the queue was full
uint32_t bpc1_checker_results_lost(const bpc1_checker_t *ck);
// Silence outside every file gives no report (default: false). Before the
// first frame.
void bpc1_checker_allow_silence(bpc1_checker_t *ck, bool allow);

// The file being checked so far, with end OPEN: NOT_BIT_PERFECT from a first
// difference received before any overrun, OVERRUN from an overrun
// before any difference, else BIT_PERFECT (so far). False while no file is
// being checked.
bool bpc1_checker_peek(const bpc1_checker_t *ck, bpc1_report_t *rep);

// Names as the result line prints them
const char *bpc1_verdict_name(uint8_t verdict);
const char *bpc1_kind_name(uint8_t kind);  // "NONE" for 0
const char *bpc1_end_name(uint8_t end);
const char *bpc1_why_name(uint8_t why);
// diff as the result line prints it: seconds with 3 decimals, or "-"
void bpc1_diff_text(const bpc1_report_t *r, char *buf, uint32_t size);

#endif

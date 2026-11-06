// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The frame walker of the BPC1 generator: the frames of a BPC1 file as a
// USB audio host sends them, generated on the fly. Portable C99, no
// hardware: the BPC1 generator firmware builds on it, and tests compile it
// on the host (tests/test_bpc1_walk.py).
//
// A frame is one sample per channel, each in a slot of 2, 3 or 4 bytes,
// little-endian: the PCM word W >> (32 - 8 * slot), so a depth narrower
// than the slot has zero low bytes. DSD plays as DoP at DSD rate / 16
// (docs/bpc1.md 9.5): the 24-bit DoP sample, its marker by the frame's
// index in the regular file, in a slot of 3 or 4 bytes. The walker sends
// exactly the data chunk of the file the BPC1 file generator writes
// (generator/fileset.py), re-slotted.

#ifndef BPC1_WALK_H
#define BPC1_WALK_H

#include <stdbool.h>
#include <stdint.h>

#include "bpc1.h"

#define BPC1_WALK_SEED 0x42504331u  // "BPC1": the seed of every published file

// A negative test file's fault (docs/bpc1.md 11.5 to 11.7)
#define BPC1_FAULT_NONE       0u
#define BPC1_FAULT_ALTERED    1u  // PCM: the low 8 bits of the body cleared; sweep: code 2^(D-1)-2
                                  // of channel 0 in the body raised by one; DoP: frames at..at+len-1
                                  // with their 16 DSD bits inverted
#define BPC1_FAULT_INCOMPLETE 2u  // frames at..at+len-1 removed
#define BPC1_FAULT_GAP        3u  // part 2 of a gapless pair: len zero frames before its first frame

// A file: its signal and layout, and where it differs from the regular file
// of that layout. Frames count DoP frames for DSD.
typedef struct {
  uint8_t kind;        // BPC1_KIND_*
  uint8_t depth_code;
  uint8_t rate_code;
  uint8_t fault;       // BPC1_FAULT_*
  uint16_t lead;       // blocks
  uint16_t body;
  uint16_t tail;
  uint8_t part;        // 0 a whole file; 1 or 2: a part of a gapless pair
  uint32_t cut;        // part: the first frame of part 2
  uint32_t at;         // fault: the first frame
  uint32_t len;        // fault: frames
} bpc1_file_t;

// The number table: the file with five-digit number TOFPP (docs/bpc1.md
// 11.2), in any format F it is published in. False for any other number.
bool bpc1_walk_lookup(uint32_t number, bpc1_file_t *f);

typedef struct {
  bpc1_signal_t sig;
  bpc1_block_t blk;    // the block of pos, valid when blk_ok
  bpc1_file_t file;
  bool blk_ok;
  bool dop;
  uint8_t slot;        // bytes per sample
  uint8_t nch;
  uint32_t total;      // blocks
  uint32_t fpb;        // frames per block
  uint32_t pos;        // the next frame of the regular file to send
  uint32_t end;        // the frame after the last one to send
  uint32_t zeros;      // zero frames to send before pos
  uint32_t frames;     // frames of the file
  uint32_t walk[BPC1_MAX_CH][64];  // the walking-bit burst, as W
} bpc1_walk_t;

// Starts a file with its samples in `slot` bytes. False, and nothing to
// send, if the file is not valid BPC1 or its samples do not fit the slot
// (DoP needs 3 or 4 bytes).
bool bpc1_walk_init(bpc1_walk_t *w, const bpc1_file_t *f, uint8_t slot);
// The same for a number of the table.
bool bpc1_walk_init_number(bpc1_walk_t *w, uint32_t number, uint8_t slot);

// The frame rate: the PCM rate, or the DoP rate (DSD rate / 16)
static inline uint32_t bpc1_walk_rate(const bpc1_walk_t *w) {
  return w->dop ? w->sig.rate / 16u : w->sig.rate;
}
// Frames in the file, and bytes per frame
static inline uint32_t bpc1_walk_frames(const bpc1_walk_t *w) {
  return w->frames;
}
static inline uint32_t bpc1_walk_frame_bytes(const bpc1_walk_t *w) {
  return (uint32_t) w->nch * w->slot;
}

// Writes the next n frames, or as many as are left, to out (n x frame
// bytes, any alignment); returns the frames written, 0 at the end.
uint32_t bpc1_walk_fill(bpc1_walk_t *w, uint8_t *out, uint32_t n);

#endif

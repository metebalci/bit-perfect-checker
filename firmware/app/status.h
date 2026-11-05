// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The device's state as the LEDs and a display show it. The USB callbacks
// and the stream probe write it, then call status_changed().

#ifndef STATUS_H
#define STATUS_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
  USB_NOT_MOUNTED,
  USB_MOUNTED,
  USB_SUSPENDED
} usb_state_t;

typedef struct {
  uint32_t version;     // advances at every change
  usb_state_t usb;
  bool high_speed;
  uint32_t rate;        // Hz, as set by the host
  uint8_t alt;          // streaming alt setting, 0 when closed
  uint8_t slot;         // bytes per sample
  uint8_t resolution;   // bits, as the alt setting declares
  bool streaming;
  uint32_t stream_id;   // counts streams since boot, from 1
  uint8_t bits;         // bits in use, over the last second
  bool dop;
  uint32_t lost;        // packets lost in this stream, the last one once it stopped
  uint32_t overflows;   // receive FIFO overflows in this stream, likewise
  bool allow_silence;   // silence outside every file gives no result
  // The results of the run: since the user button, the mark command or reset
  bool run_pass;        // a BIT_PERFECT result
  bool run_fail;        // a NOT_BIT_PERFECT result, or a file's first difference
  bool run_unknown;     // an UNKNOWN_SIGNAL result
  bool run_overrun;     // a DEVICE_OVERRUN result
  uint32_t run_count[4];      // the run's results by verdict, indexed by bpc1_verdict_t
  uint32_t run_results_lost;  // the run's results the checker dropped (its queue was full)
  // The last result line, as the "result" command repeats it
  bool last_known;
  bool last_in_run;           // it belongs to the run that runs now
  uint8_t last_verdict;       // bpc1_verdict_t
  uint8_t last_end;           // bpc1_end_t
  int32_t last_lost;
  uint32_t last_overflows;
  char last_diff[12];         // seconds with 3 decimals, or "-"
  uint8_t last_kind;          // its file as the header gives it: BPC1_KIND_*, 0 for none
  uint8_t last_depth;         // bits, 1 for DSD
  uint32_t last_rate;         // Hz, the DSD bit rate for DSD
  // The file the checker locked onto in the open stream, from its header,
  // until the stream stops
  uint8_t file_kind;          // BPC1_KIND_*, 0 for none
  uint8_t file_depth;
  uint32_t file_rate;
  // The file being checked, from its first difference until its result line
  bool file_diff_known;
  char file_diff[12];
} status_t;

extern status_t status;

static inline void status_changed(void) {
  status.version++;
}

#endif

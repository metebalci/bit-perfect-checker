// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_stream_holes.py: runs app/stream.c, with the BPC1
// checker and TinyUSB's own tu_fifo, on audio packets given on stdin, with
// stand-ins for the board, the load figures and the log. Prints the log
// lines. Not part of the firmware.
//
//   <p> [<entry> [<now>]]  a packet received in microframe <p>: the USB
//                          interrupt entered in microframe <entry> (default
//                          <p>), and a fresh read of the microframe in it
//                          gives <now> (default the later of the two)
//   idle <n>               <n> microframes without a packet
//
// Microframe numbers are absolute here and only grow; the board's functions
// give them modulo 0x4000, as the controller does, and the endpoint's EONUM
// bit is the parity of <p>. The main loop runs once in each microframe up to
// the interrupt's entry, and once after each packet. Each packet carries 6
// frames of 48 kHz 16-bit stereo silence (alt 1); with allow_silence false,
// silence gives result lines. At the end of the input no packet comes for
// 60 ms, so the stream stops.

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "board.h"
#include "load.h"
#include "log.h"
#include "status.h"
#include "stream.h"
#include "tusb.h"
#include "usb_descriptors.h"

#define UFRAME_MASK 0x3FFFu
#define PACKET_BYTES 24u  // 6 frames of 2 x 16 bits

status_t status;
uac2_alt_t uac2_alts[UAC2_ALT_MAX + 1] = {{0, 0}, {2, 16}, {4, 24}, {4, 32}, {3, 24}};

static uint8_t ffbuf[CFG_TUD_AUDIO_FUNC_1_EP_OUT_SW_BUF_SZ];
static tu_fifo_t ff;
static uint64_t now;        // the microframe the main loop runs in
static bool in_isr;
static uint64_t isr_now;    // the microframe a fresh read in the interrupt gives
static uint64_t isr_entry;  // the microframe at the interrupt's entry
static bool isr_odd;        // the endpoint's EONUM bit
static uint32_t cycles;

tu_fifo_t *tud_audio_get_ep_out_ff(void) { return &ff; }
bool tud_mounted(void) { return true; }

bool board_button(void) { return false; }
uint32_t board_millis(void) { return (uint32_t) (now / 8u); }
uint32_t board_cycles(void) { return cycles += 100u; }
uint32_t board_cpu_hz(void) { return 160000000u; }
uint32_t board_usb_microframe(void) { return (uint32_t) (in_isr ? isr_now : now) & UFRAME_MASK; }
void board_usb_entry(uint32_t *uframe, bool *odd) {
  *uframe = (uint32_t) isr_entry & UFRAME_MASK;
  *odd = isr_odd;
}
void board_irq_disable(void) {}
void board_irq_enable(void) {}
void board_log_start(void) {}

void load_isr(uint32_t c) { (void) c; }
void load_fifo(uint32_t b) { (void) b; }
void load_loop(void) {}
load_t load_take(void) { return (load_t) {0}; }

static void vline(const char *fmt, va_list ap) {
  printf("%llu ", (unsigned long long) now);
  vprintf(fmt, ap);
  putchar('\n');
}

void log_line(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vline(fmt, ap);
  va_end(ap);
}

void log_line_reserved(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vline(fmt, ap);
  va_end(ap);
}

void log_append(char *buf, uint32_t size, uint32_t *len, const char *fmt, ...) {
  if (size == 0 || *len >= size) {
    return;
  }
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf + *len, size - *len, fmt, ap);
  va_end(ap);
  if (n > 0) {
    *len += (uint32_t) n < size - *len ? (uint32_t) n : size - *len - 1u;
  }
}

int log_tx_byte(void) { return -1; }

// The main loop runs in each microframe until `to`
static void advance(uint64_t to) {
  while (now < to) {
    now++;
    stream_task();
  }
}

static void packet(uint64_t p, uint64_t entry, uint64_t fresh) {
  static const uint8_t silence[PACKET_BYTES];
  advance(entry < now ? now : entry);
  tu_fifo_write_n(&ff, silence, PACKET_BYTES);
  in_isr = true;
  isr_entry = entry;
  isr_now = fresh;
  isr_odd = (p & 1u) != 0;
  stream_packet_isr(PACKET_BYTES);
  in_isr = false;
  if (fresh > now) {
    now = fresh;
  }
  stream_task();
}

int main(void) {
  tu_fifo_config(&ff, ffbuf, sizeof(ffbuf), true);
  now = 0;
  status.allow_silence = false;
  stream_set_rate(48000);
  stream_set_alt(1);

  char line[128];
  while (fgets(line, sizeof(line), stdin) != NULL) {
    unsigned long long a, b, c;
    if (sscanf(line, "idle %llu", &a) == 1) {
      advance(now + a);
      continue;
    }
    int n = sscanf(line, "%llu %llu %llu", &a, &b, &c);
    if (n < 1) {
      continue;
    }
    uint64_t entry = n >= 2 ? b : a;
    uint64_t fresh = n >= 3 ? c : (entry > a ? entry : a);
    packet(a, entry, fresh);
  }
  advance(now + 8u * 60u);
  return 0;
}

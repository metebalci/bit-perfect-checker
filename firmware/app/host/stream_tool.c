// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_stream_holes.py: runs app/stream.c and
// app/u2ps_glue.c, with the BPC1 checker, against u2ps's device stack
// through u2ps's PC test kit, which plays the device port and the host:
// each packet goes into a buffer of the class memory the glue lent, and the
// main loop reads it there and gives it back. Stand-ins for the board, the
// load figures and the log. Prints the log lines; the kit's verdict comes
// last, and the exit code is its. Not part of the firmware.
//
// After the enumeration at High-Speed the host sets alt setting <alt>
// (default 1, 16/2) at 48 kHz. Then, one command per line on stdin:
//   <p>              a packet received in microframe <p>: microframes
//                    since the last packet without a hole command are
//                    NO_PACKET (a pause past U2PS_GAP_PAUSE)
//   zlp <p>          a zero-length packet received in microframe <p>
//   late <n>         a hole of <n> microframes before the next packet, for
//   crc <n>          the reason LATE, CORRUPTED or NO_BUFFER (u2ps_miss_t)
//   nobuf <n>
//   parts <n> <k> <late|crc|nobuf>  <k> hole reports of <n> microframes,
//                    straight to the glue's on_hole, as u2ps splits one
//                    hole at the ticks of a long gap
//   skip <n>         <n> packets' bytes of the data file go by: packets the
//                    host sent that did not arrive
//   formats <list>   the formats command: the device detaches
//                    (u2ps_dev_connect(false)), takes the formats and
//                    attaches again; the host enumerates it and sets the
//                    alt setting and rate as before
//   unplug           VBUS goes, as main.c handles it (uac2_unplug(), the
//                    pull-up off)
//   pull             VBUS goes, as main.c handles a pull: vbus_act() with
//                    main.c's actions (uac2_unplug(), then "usb
//                    disconnected" and a new run, then the pull-up off)
//   plug             VBUS comes back; the host enumerates as above
//   slow <n>         each pass of the main loop from now on takes <n>
//                    microframes
//   slowonce <n>     the next pass of the main loop starts <n> microframes
//                    late, before its USB task
//   altinpass <a>    SET_INTERFACE alt setting <a> as an interrupt in the
//                    next pass, just after the USB task read the clock, a
//                    millisecond later than that read
//   stray <n>        after the next `stream stop` line, <n> packets the
//                    stack never lent come as interrupts (a fault the glue
//                    must catch before it opens a stream)
//   silence on|off   allow_silence for the streams that start after it
//   setconfig [<v>]  SET_CONFIGURATION <v> (default 1)
//   openfault        u2ps reports that the port refused the class memory
//   len <n>          the packets after it carry <n> bytes (at most
//                    PACKET_MAX), not 6 frames
//   hold <n>         the next <n> packets come before the main loop runs
//                    again, which then reads them; packets that find
//                    every buffer held are dropped by the kit's port and
//                    come as a NO_BUFFER hole before the next
//   idle <n>         <n> microframes without a packet
//   alt <a>          SET_INTERFACE alt setting <a> (0 stops the stream)
//   altnowait <a>    the same, and the host goes on without waiting for
//                    the answer of a start the glue defers
//   rate <hz>        SET CUR of the sampling frequency
//   reset            a bus reset, then the enumeration and the alt setting
//                    and rate as before
//   closeslow <n>    the next alt 0 or setconfig of a stream leaves u2ps's close pending
//                    for <n> passes of the main loop (DWC2 ends it over
//                    several), then the port ends it: the stream's tail
//                    holes, then on_stream_stop
//   closepacket <p>  in the next slow close, a packet received in
//                    microframe <p> as the close begins, before its passes
//   closealt <a>     in the next slow close, the host's SET_INTERFACE
//                    alt setting <a> as the close begins: u2ps holds it
//                    until the close ends, and the host waits for it then
//   ratenowait <hz>  SET CUR of the sampling frequency, and the host goes on
//                    without waiting for the answer the glue defers
//   irqpacket <p>    a pass of the main loop, in which a packet received in
//                    microframe <p> comes as an interrupt at the first unmask
//                    of its USB task (board_irq_enable()): one that came while
//                    the vector was masked
//   getcur           GET CUR of the sampling frequency: prints
//                    "get_cur <hz>", the rate u2ps reports to the host
//   evflood <n>      <n> SET_INTERFACE alt setting 0 requests before the main
//                    loop runs: each queues an event for it
//   waitonly <n>     the next <n> passes of the main loop run its USB task
//                    only: a stream task too slow to read the packets
//                    before a start's wait runs out (START_CHECK_MS,
//                    app/u2ps_glue.c)
//
// Microframe numbers are absolute here and only grow; u2ps gets them modulo
// 0x4000. The main loop runs once in each microframe up to the packet's,
// and once after each packet, unless a hold runs. Each packet carries 6
// frames of 48 kHz stereo silence in the alt setting's slots; with
// allow_silence false, silence gives result lines. With a file as the first
// argument, the packets carry its bytes instead (raw stereo in the alt
// setting's slots, little-endian), then silence; a second argument names
// the alt setting. At the end of the input no packet comes for 60 ms, so
// the stream stops; then the lines "held <n>" (packets u2ps counts as held
// by the app), "fifo_max <n>" (the most bytes received and not read yet,
// load_fifo()) and the kit's verdict.

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "board.h"
#include "family.h"
#include "load.h"
#include "log.h"
#include "status.h"
#include "stream.h"
#include "fake_dport.h"
#include "u2ps_testkit_dev.h"
#include "uac2.h"
#include "usb_descriptors.h"
#include "vbus.h"

#define UFRAME_MASK 0x3FFFu
#define PACKET_FRAMES 6u
#define PACKET_MAX 1024u

const uint32_t host_uid[3] = {0x00600015u, 0x39365006u, 0x20313148u};

status_t status;

static uint8_t alt = 1;           // alt 1: 16 bits in 2-byte slots (UAC2_FORMATS_ALL)
static uint32_t rate = 48000;
static uint64_t now;              // the microframe the main loop runs in
static uint32_t cycles;
static FILE *data;                // the packets' bytes, NULL for silence
static uint32_t hold;             // packets to come before the main loop runs again
static uint32_t slow;             // microframes one pass of the main loop takes
static uint32_t slow_once;        // microframes before the next pass's USB task
static uint32_t close_slow;       // passes before the next alt 0's or setconfig's close ends
static bool close_packet_due;     // "closepacket": its microframe
static uint64_t close_packet;
static int close_alt = -1;        // "closealt": the alt setting, or none
static uint32_t wait_only;        // passes to come without the stream task

bool board_button(void) { return false; }
// "altinpass": the SETUP comes inside the USB task, after its first read
// of the clock, and the tick has moved by then
static bool in_usb_task;
static int setup_in_pass = -1;    // the alt setting of that SETUP, or none
static u2ps_testkit_control_t setup_in_pass_result;
static uint32_t ms_ahead;
uint32_t board_millis(void) {
  uint32_t ms = (uint32_t) (now / 8u) + ms_ahead;
  if (in_usb_task && setup_in_pass >= 0) {
    uint8_t a = (uint8_t) setup_in_pass;
    setup_in_pass = -1;
    ms_ahead++;
    setup_in_pass_result = u2ps_testkit_dev_set_interface(a);
  }
  return ms;
}
uint32_t board_cycles(void) { return cycles += 100u; }
uint32_t board_cpu_hz(void) { return 160000000u; }

// "irqpacket": the packet that waits for the USB task's next unmask
static const uint8_t *payload(uint16_t bytes);
static uint16_t packet_bytes;
static bool irq_packet_due;
static uint64_t irq_packet;

void board_irq_disable(void) {}
void board_irq_enable(void) {
  if (irq_packet_due && in_usb_task) {
    irq_packet_due = false;
    u2ps_testkit_dev_packet_bytes(payload(packet_bytes), packet_bytes, (u2ps_uframe_t) (irq_packet & UFRAME_MASK));
  }
}
void board_log_start(void) {}
void family_usb_irq_start(void) {}

void load_isr(uint32_t c) { (void) c; }
static uint32_t fifo_max;
void load_fifo(uint32_t b) {
  fifo_max = b > fifo_max ? b : fifo_max;
}
void load_loop(void) {}
load_t load_take(void) { return (load_t) {0}; }

void vbus_hold_pullup(uint32_t ms) { (void) ms; }

// "pull": what main.c does at a pull (vbus_ops there)
static const vbus_ops_t pull_ops = {.unplug = uac2_unplug, .run_start = stream_run_start, .pullup = uac2_pullup};

void u2ps_app_on_contract(u2ps_contract_t violation) { (void) violation; }

static void vline(const char *fmt, va_list ap) {
  printf("%llu ", (unsigned long long) now);
  vprintf(fmt, ap);
  putchar('\n');
}

// "stray": the packets' buffer, which the kit never lent
static uint32_t stray;
static uint8_t stray_buf[PACKET_MAX];

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
  if (strncmp(fmt, "stream stop", 11) == 0) {
    printf("%llu end_held %d\n", (unsigned long long) now, stream_end_held() ? 1 : 0);
    printf("%llu lead_lost %lld\n", (unsigned long long) now, (long long) stream_lead_lost());
  }
  if (stray > 0 && strncmp(fmt, "stream stop", 11) == 0) {
    for (; stray > 0; stray--) {
      stream_packet_isr(stray_buf, 24u, (uint32_t) (now & UFRAME_MASK));
    }
  }
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

// One pass of the firmware's main loop, its USB and stream parts
static void loop(void) {
  now += slow_once;
  slow_once = 0;
  in_usb_task = true;
  uac2_task();
  in_usb_task = false;
  if (wait_only > 0) {
    wait_only--;
  } else {
    stream_task();
  }
  now += slow;
}

// The kit's poll: once per millisecond of its time, the main loop
static void poll(uint32_t now_ms) {
  (void) now_ms;
  loop();
}

// The main loop runs in each microframe until `to`; at each millisecond
// through the kit, which then starts its log of port calls again
static void advance(uint64_t to) {
  while (now < to) {
    now++;
    if ((now & 7u) == 0) {
      u2ps_testkit_dev_run_ms(1);
    } else {
      loop();
    }
  }
}

// A control transfer the glue answers from the main loop
// (U2PS_REQ_DEFER, U2PS_STREAM_DEFER): the main loop runs, one microframe
// at a time, until it has; at most 50 ms (USB 2.0 9.2.6.4)
static uint64_t answered_after;  // microframes the last control transfer waited

static void deferred(u2ps_testkit_control_t r) {
  uint64_t t0 = now;
  for (int i = 0; i < 400 && r == U2PS_TESTKIT_CONTROL_NAK; i++) {
    advance(now + 1u);
    r = u2ps_testkit_dev_control_resume(NULL, NULL);
  }
  answered_after = now - t0;
  if (answered_after > 0u) {
    printf("%llu answered after %llu\n", (unsigned long long) now, (unsigned long long) answered_after);
  }
  U2PS_TESTKIT_CHECK(r == U2PS_TESTKIT_CONTROL_OK);
  loop();
}

static void set_alt(uint8_t a) {
  deferred(u2ps_testkit_dev_set_interface(a));
}

// "closeslow": a no-data request that closes the stream, past the kit's
// own steps, which end a close at once; the main loop runs close_slow
// passes before the port's close ends
static void slow_close(uint8_t bm_request_type, uint8_t b_request, uint16_t w_value, uint16_t w_index) {
  U2PS_TESTKIT_CHECK(u2ps_fake_host_control(bm_request_type, b_request, w_value, w_index, 0, NULL, NULL)
                     == U2PS_FAKE_HOST_OK);
  if (close_packet_due) {
    close_packet_due = false;
    now = close_packet < now ? now : close_packet;
    // past the kit, which would end the close first
    u2ps_fake_packet_bytes(payload(packet_bytes), packet_bytes, (u2ps_uframe_t) (close_packet & UFRAME_MASK));
  }
  int start_alt = close_alt;
  close_alt = -1;
  if (start_alt >= 0) {
    alt = (uint8_t) start_alt;
    packet_bytes = (uint16_t) (PACKET_FRAMES * UAC2_CHANNELS * uac2_alts[alt].slot);
    U2PS_TESTKIT_CHECK(u2ps_fake_host_control(0x01, 11, alt, ITF_NUM_AUDIO_STREAMING, 0, NULL, NULL)
                       == U2PS_FAKE_HOST_NAK);
  }
  for (; close_slow > 0; close_slow--) {
    loop();
  }
  if (u2ps_fake_stream_state == U2PS_FAKE_ST_CLOSING) {
    u2ps_fake_closed();
  }
  loop();
  if (start_alt >= 0) {
    deferred(u2ps_testkit_dev_control_resume(NULL, NULL));
  }
}

static void set_rate(uint32_t hz) {
  deferred(u2ps_testkit_dev_set_sampling_frequency(hz));
}

static void enumerate(void) {
  U2PS_TESTKIT_CHECK(u2ps_testkit_dev_enumerate(U2PS_SPEED_HIGH) == U2PS_TESTKIT_CONTROL_OK);
  loop();
  if (rate != USB_START_RATE) {
    set_rate(rate);
  }
  set_alt(alt);
}

// The next packet's bytes: the data file's, then silence
static const uint8_t *payload(uint16_t bytes) {
  static uint8_t buf[PACKET_MAX];
  size_t n = data != NULL ? fread(buf, 1, bytes, data) : 0;
  memset(buf + n, 0, sizeof(buf) - n);
  return buf;
}

static void packet(uint64_t p, uint16_t bytes) {
  if (hold > 0) {
    now = p < now ? now : p;
  } else {
    advance(p < now ? now : p);
  }
  u2ps_testkit_dev_packet_bytes(payload(bytes), bytes, (u2ps_uframe_t) (p & UFRAME_MASK));
  if (hold > 0) {
    hold--;
  } else {
    loop();
  }
}

int main(int argc, char **argv) {
  if (argc > 1 && strcmp(argv[1], "-") != 0 && (data = fopen(argv[1], "rb")) == NULL) {
    perror(argv[1]);
    return 1;
  }
  if (argc > 2) {
    alt = (uint8_t) atoi(argv[2]);
  }
  status.allow_silence = false;
  u2ps_testkit_dev_reset();
  u2ps_testkit_set_poll(poll);
  uac2_init();
  uac2_start();
  uac2_vbus(true);
  u2ps_testkit_dev_vbus(true);
  if (alt < 1 || alt > uac2_alt_count) {
    fprintf(stderr, "alt %u: 1 to %u\n", alt, uac2_alt_count);
    return 1;
  }
  packet_bytes = (uint16_t) (PACKET_FRAMES * UAC2_CHANNELS * uac2_alts[alt].slot);
  enumerate();

  char line[128];
  while (fgets(line, sizeof(line), stdin) != NULL) {
    unsigned long long a;
    if (sscanf(line, "idle %llu", &a) == 1) {
      advance(now + a);
    } else if (sscanf(line, "hold %llu", &a) == 1) {
      hold = (uint32_t) a;
    } else if (sscanf(line, "len %llu", &a) == 1 && a <= PACKET_MAX) {
      packet_bytes = (uint16_t) a;
    } else if (sscanf(line, "zlp %llu", &a) == 1) {
      packet(a, 0);
    } else if (sscanf(line, "late %llu", &a) == 1) {
      u2ps_testkit_dev_hole((uint16_t) a, U2PS_MISS_LATE);
    } else if (sscanf(line, "crc %llu", &a) == 1) {
      u2ps_testkit_dev_hole((uint16_t) a, U2PS_MISS_CORRUPTED);
    } else if (strncmp(line, "parts ", 6) == 0) {
      unsigned n, k;
      char why[8];
      if (sscanf(line, "parts %u %u %7s", &n, &k, why) == 3) {
        u2ps_miss_t w = strcmp(why, "late") == 0 ? U2PS_MISS_LATE
                        : strcmp(why, "crc") == 0 ? U2PS_MISS_CORRUPTED : U2PS_MISS_NO_BUFFER;
        for (unsigned i = 0; i < k; i++) {
          u2ps_app_on_hole((uint16_t) n, w);
        }
      }
    } else if (sscanf(line, "skip %llu", &a) == 1) {
      for (unsigned long long i = 0; i < a; i++) {
        (void) payload(packet_bytes);
      }
    } else if (strncmp(line, "formats ", 8) == 0) {
      line[strcspn(line, "\n")] = '\0';
      U2PS_TESTKIT_CHECK(uac2_formats(line + 8));
      loop();
      uac2_pullup(true);  // vbus_hold_pullup()'s end
      enumerate();
    } else if (strncmp(line, "unplug", 6) == 0) {
      uac2_unplug();
      uac2_pullup(false);
      u2ps_testkit_dev_vbus(false);
      loop();
    } else if (strncmp(line, "pull", 4) == 0) {
      vbus_act(VBUS_GONE | VBUS_DISCONNECT, &pull_ops);
      u2ps_testkit_dev_vbus(false);
      loop();
    } else if (strncmp(line, "plug", 4) == 0) {
      u2ps_testkit_dev_vbus(true);
      uac2_vbus(true);
      uac2_pullup(true);
      enumerate();
    } else if (sscanf(line, "slowonce %llu", &a) == 1) {
      slow_once = (uint32_t) a;
    } else if (sscanf(line, "altinpass %llu", &a) == 1) {
      alt = (uint8_t) a;
      if (alt != 0) {
        packet_bytes = (uint16_t) (PACKET_FRAMES * UAC2_CHANNELS * uac2_alts[alt].slot);
      }
      setup_in_pass = alt;
      loop();
      U2PS_TESTKIT_CHECK(setup_in_pass < 0);
      deferred(setup_in_pass_result);
    } else if (sscanf(line, "slow %llu", &a) == 1) {
      slow = (uint32_t) a;
    } else if (strncmp(line, "silence on", 10) == 0) {
      status.allow_silence = true;
    } else if (strncmp(line, "silence off", 11) == 0) {
      status.allow_silence = false;
    } else if (sscanf(line, "stray %llu", &a) == 1) {
      stray = (uint32_t) a;
    } else if (strncmp(line, "setconfig", 9) == 0) {
      unsigned v = 1;
      (void) sscanf(line, "setconfig %u", &v);
      u2ps_setup_t setup = {.bmRequestType = 0x00, .bRequest = 9, .wValue = (uint16_t) v};
      if (close_slow > 0) {
        slow_close(setup.bmRequestType, setup.bRequest, setup.wValue, setup.wIndex);
      } else {
        U2PS_TESTKIT_CHECK(u2ps_testkit_dev_control(&setup, NULL, NULL) == U2PS_TESTKIT_CONTROL_OK);
        loop();
      }
    } else if (sscanf(line, "waitonly %llu", &a) == 1) {
      wait_only = (uint32_t) a;
    } else if (sscanf(line, "closepacket %llu", &a) == 1) {
      close_packet = a;
      close_packet_due = true;
    } else if (sscanf(line, "closealt %llu", &a) == 1) {
      close_alt = (int) a;
    } else if (sscanf(line, "closeslow %llu", &a) == 1) {
      close_slow = (uint32_t) a;
    } else if (sscanf(line, "ratenowait %llu", &a) == 1) {
      u2ps_testkit_control_t r = u2ps_testkit_dev_set_sampling_frequency((uint32_t) a);
      printf("%llu set_cur %s\n", (unsigned long long) now,
             r == U2PS_TESTKIT_CONTROL_OK ? "ok" : r == U2PS_TESTKIT_CONTROL_NAK ? "pending"
             : r == U2PS_TESTKIT_CONTROL_STALL ? "stall" : "error");
      U2PS_TESTKIT_CHECK(r != U2PS_TESTKIT_CONTROL_ERROR);
    } else if (sscanf(line, "irqpacket %llu", &a) == 1) {
      irq_packet = a;
      irq_packet_due = true;
      now = a < now ? now : a;
      loop();
      U2PS_TESTKIT_CHECK(!irq_packet_due);
    } else if (strncmp(line, "getcur", 6) == 0) {
      uint32_t hz = 0;
      U2PS_TESTKIT_CHECK(u2ps_testkit_dev_get_sampling_frequency(&hz) == U2PS_TESTKIT_CONTROL_OK);
      printf("%llu get_cur %lu\n", (unsigned long long) now, (unsigned long) hz);
    } else if (sscanf(line, "evflood %llu", &a) == 1) {
      for (unsigned long long i = 0; i < a; i++) {
        U2PS_TESTKIT_CHECK(u2ps_testkit_dev_set_interface(0) == U2PS_TESTKIT_CONTROL_OK);
      }
    } else if (strncmp(line, "openfault", 9) == 0) {
      u2ps_app_on_fault(U2PS_FAULT_OPEN);
      loop();
    } else if (sscanf(line, "nobuf %llu", &a) == 1) {
      u2ps_testkit_dev_hole((uint16_t) a, U2PS_MISS_NO_BUFFER);
    } else if (sscanf(line, "altnowait %llu", &a) == 1) {
      alt = (uint8_t) a;
      (void) u2ps_testkit_dev_set_interface(alt);
    } else if (sscanf(line, "alt %llu", &a) == 1) {
      alt = (uint8_t) a;
      if (alt != 0) {
        packet_bytes = (uint16_t) (PACKET_FRAMES * UAC2_CHANNELS * uac2_alts[alt].slot);
        set_alt(alt);
      } else if (close_slow > 0) {
        slow_close(0x01, 11, 0, ITF_NUM_AUDIO_STREAMING);
      } else {
        set_alt(alt);
      }
    } else if (sscanf(line, "rate %llu", &a) == 1) {
      rate = (uint32_t) a;
      set_rate(rate);
    } else if (strncmp(line, "reset", 5) == 0) {
      u2ps_testkit_dev_bus_reset(U2PS_SPEED_HIGH);
      loop();
      enumerate();
    } else if (sscanf(line, "%llu", &a) == 1) {
      packet(a, packet_bytes);
    }
  }
  advance(now + 8u * 1100u);  // the stream stops; u2ps's counters come within a second
  printf("%llu held %u\n", (unsigned long long) now, u2ps_testkit_dev_held());
  printf("%llu fifo_max %u\n", (unsigned long long) now, (unsigned) fifo_max);
  printf("%llu usb_fault %d\n", (unsigned long long) now, status.usb_fault ? 1 : 0);
  return u2ps_testkit_result();
}

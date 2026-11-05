// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_vbus.py: runs app/vbus.c on VBUS levels and USB
// events given on stdin, with stand-ins for what main.c and uac2.c do. Not
// part of the firmware.
//
//   <ms> <0|1>    VBUS level at a poll -> one line per action flag, in this
//                 order: "gone <ms>", "disconnect <ms>", "connect <ms>";
//                 then vbus_act() runs, its lines as "| <line>"
//   <ms> mount    a host set the device up        -> "| usb mounted"
//   <ms> stream   a stream opens
//   <ms> reset    the host reset the bus, as stream.c sees it: the stream
//                 stops, the device is no longer set up -> "| usb reset"
//   <ms> hold <n> a change of formats holds the pull-up off for n ms
//                 (vbus_hold()) -> its action flags and lines, as a level
//
// The first level line goes to vbus_start(), the others to vbus_poll().
// The stand-in for tud_umount_cb() logs the stream's stop and, like uac2.c,
// nothing else during an unplug. Numbers in decimal; <ms> may wrap.

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "log.h"
#include "vbus.h"

static bool mounted;
static bool streaming;

void log_line(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  printf("| ");
  vprintf(fmt, ap);
  printf("\n");
  va_end(ap);
}

// tud_umount_cb() during an unplug: the open stream stops
static void unplug(void) {
  if (streaming) {
    log_line("stream stop reason=reset");
    streaming = false;
  }
  mounted = false;
}

static void run_start(void) {
  log_line("run start");
}

static void pullup(bool on) {
  log_line("pull-up %s", on ? "on" : "off");
}

static const vbus_ops_t ops = {.unplug = unplug, .run_start = run_start, .pullup = pullup};

int main(void) {
  vbus_t v = {0};
  unsigned long ms;
  char word[16];
  bool first = true;
  while (scanf("%lu %15s", &ms, word) == 2) {
    if (strcmp(word, "mount") == 0) {
      mounted = true;
      log_line("usb mounted");
      continue;
    }
    if (strcmp(word, "stream") == 0) {
      streaming = true;
      continue;
    }
    if (strcmp(word, "reset") == 0) {
      if (streaming) {
        log_line("stream stop reason=reset");
        streaming = false;
      }
      mounted = false;
      log_line("usb reset");
      continue;
    }
    uint8_t act;
    if (strcmp(word, "hold") == 0) {
      unsigned long n;
      if (scanf("%lu", &n) != 1) {
        return 1;
      }
      act = vbus_hold(&v, (uint32_t) ms, (uint32_t) n);
    } else {
      bool level = strcmp(word, "0") != 0;
      act = first ? vbus_start(&v, level, (uint32_t) ms) : vbus_poll(&v, level, (uint32_t) ms);
      first = false;
    }
    if (act & VBUS_GONE) {
      printf("gone %lu\n", ms);
    }
    if (act & VBUS_DISCONNECT) {
      printf("disconnect %lu\n", ms);
    }
    if (act & VBUS_CONNECT) {
      printf("connect %lu\n", ms);
    }
    vbus_act(act, &ops);
  }
  return 0;
}

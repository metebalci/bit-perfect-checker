// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host commands on the log UART.

#include "console.h"

#include <stdbool.h>
#include <string.h>

#include "board.h"
#include "log.h"
#include "status.h"
#include "stream.h"
#include "uac2.h"
#include "usb_descriptors.h"
#include "version.h"

#define RX_SIZE   256u  // power of two
#define LINE_MAX  80u
#define STALL_MAX_MS 2000u  // the test command stall: 1 to this many ms

static uint8_t rx[RX_SIZE];
static volatile uint32_t rx_head;  // written by the interrupt
static volatile uint32_t rx_tail;  // written by console_task()

static char line[LINE_MAX + 1];
static uint32_t line_len;
static bool line_too_long;

void console_rx_isr(uint8_t c) {
  uint32_t head = rx_head;
  uint32_t next = (head + 1) & (RX_SIZE - 1);
  if (next != rx_tail) {  // when full, the byte is dropped
    rx[head] = c;
    rx_head = next;
  }
}

void console_version(const char *event) {
  log_line("%s fw=%s board=%s usb=%04x:%04x", event, FW_VERSION, BOARD_NAME, (unsigned) USB_VID,
           (unsigned) USB_PID);
}

void console_boot(void) {
  log_line("boot fw=%s board=%s usb=%04x:%04x allow_silence=%s", FW_VERSION, BOARD_NAME, (unsigned) USB_VID,
           (unsigned) USB_PID, status.allow_silence ? "true" : "false");
}

static const char *usb_name(usb_state_t usb) {
  switch (usb) {
    case USB_MOUNTED: return "mounted";
    case USB_SUSPENDED: return "suspended";
    default: return "not-connected";
  }
}

static void run(char *cmd) {
  char *arg = strchr(cmd, ' ');
  if (arg != NULL) {
    *arg++ = '\0';
  }

  if (strcmp(cmd, "status") == 0) {
    char formats[32];
    usb_formats_text(formats, sizeof(formats));
    log_line("status usb=%s speed=%s rate=%lu alt=%u slot=%u res=%u streaming=%d id=%lu lost=%lu ovf=%lu "
             "formats=%s allow_silence=%s",
             usb_name(status.usb), status.high_speed ? "high" : "full", (unsigned long) status.rate,
             status.alt, status.slot, status.resolution, status.streaming ? 1 : 0,
             (unsigned long) status.stream_id, (unsigned long) status.lost,
             (unsigned long) status.overflows, formats, status.allow_silence ? "true" : "false");
  } else if (strcmp(cmd, "result") == 0) {
    stream_log_result();
  } else if (strcmp(cmd, "mark") == 0) {
    log_line("mark %s", arg != NULL ? arg : "");
    stream_run_start();
  } else if (strcmp(cmd, "allow_silence") == 0) {
    // for the next stream; until reset
    if (arg != NULL && strcmp(arg, "true") == 0) {
      status.allow_silence = true;
    } else if (arg != NULL && strcmp(arg, "false") == 0) {
      status.allow_silence = false;
    } else if (arg != NULL) {
      log_line("error allow_silence=%s", arg);
      return;
    }
    status_changed();
    log_line("allow_silence %s", status.allow_silence ? "true" : "false");
  } else if (strcmp(cmd, "formats") == 0) {
    if (!uac2_formats(arg)) {
      log_line("error formats=%s", arg);
    }
  } else if (strcmp(cmd, "stall") == 0) {
    // Test command: busy-waits in the main loop with interrupts on, so the
    // USB interrupt keeps filling the receive FIFO while nothing drains it
    // (to show OVERRUN on purpose).
    uint32_t ms = 0;
    const char *d = arg;
    while (d != NULL && *d >= '0' && *d <= '9' && ms <= STALL_MAX_MS) {
      ms = ms * 10u + (uint32_t) (*d++ - '0');
    }
    if (d == NULL || d == arg || *d != '\0' || ms < 1u || ms > STALL_MAX_MS) {
      log_line("error stall=%s", arg != NULL ? arg : "");
      return;
    }
    log_line("stall %lu", (unsigned long) ms);
    uint32_t t0 = board_millis();
    while (board_millis() - t0 < ms) {
    }
  } else if (strcmp(cmd, "version") == 0) {
    console_version("version");
  } else if (strcmp(cmd, "help") == 0) {
    log_line("help commands=status,result,mark,formats,allow_silence,version,help test=stall");
  } else {
    log_line("error unknown-command=%s", cmd);
  }
}

void console_task(void) {
  while (rx_tail != rx_head) {
    uint32_t tail = rx_tail;
    char c = (char) rx[tail];
    rx_tail = (tail + 1) & (RX_SIZE - 1);

    if (c == '\r' || c == '\n') {
      if (line_too_long) {
        log_line("error line-too-long");
      } else if (line_len > 0) {
        line[line_len] = '\0';
        run(line);
      }
      line_len = 0;
      line_too_long = false;
    } else if (line_len < LINE_MAX) {
      // Keep the log printable
      line[line_len++] = (c >= ' ' && c <= '~') ? c : '?';
    } else {
      line_too_long = true;
    }
  }
}

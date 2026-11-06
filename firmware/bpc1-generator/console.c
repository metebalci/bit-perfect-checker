// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The BPC1 generator's commands on the log UART (console.h), one per line,
// with the checker's line rules: CR or LF ends a line, at most 80
// characters, other bytes than printable ASCII read as '?'. Each answer is
// one log line:
//
//   status   "status usb=<mounted/not-connected> vbus=<on/fault/off>"
//   version  "version fw=... board=... role=bpc1-generator"
//   help     "help commands=status,version,help"
//
// Anything else gets "error unknown-command=<word>" or "error line-too-long".

#include "console.h"

#include <stdbool.h>
#include <string.h>

#include "generator.h"
#include "log.h"
#include "version.h"

#define RX_SIZE  256u  // power of two
#define LINE_MAX 80u
#define ROLE     "bpc1-generator"

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
  log_line("%s fw=%s board=%s role=" ROLE, event, FW_VERSION, BOARD_NAME);
}

void console_boot(void) {
  console_version("boot");
}

static void run(char *cmd) {
  char *arg = strchr(cmd, ' ');
  if (arg != NULL) {
    *arg = '\0';
  }

  if (strcmp(cmd, "status") == 0) {
    log_line("status usb=%s vbus=%s", generator_usb_mounted() ? "mounted" : "not-connected", generator_vbus());
  } else if (strcmp(cmd, "version") == 0) {
    console_version("version");
  } else if (strcmp(cmd, "help") == 0) {
    log_line("help commands=status,version,help");
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

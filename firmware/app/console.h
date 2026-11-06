// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Commands from the host on the log UART, one per line (CR or LF ends it),
// for test automation. Each answer is one log line:
//
//   status        "status usb=... speed=... rate=... alt=... slot=... res=...
//                 streaming=0|1 id=... lost=... ovf=... formats=...
//                 allow_silence=true|false"
//   result        the last "result" line again, or "result none"
//   mark <text>   "mark <text>", to sync a script with the log; then a new
//                 run, "run start"
//   allow_silence [true|false]
//                 "allow_silence <value>": silence outside every file gives
//                 no result, from the next stream on, until reset
//   formats [<list>|default]
//                 "formats <list>"; with a list (e.g. 24/3,16/2) or
//                 "default", offer those formats from now on: "formats
//                 <list> changed=1", then the device disconnects and
//                 reconnects ("usb disconnected", "usb connecting", "usb
//                 mounted ..."); "changed=0" if they were offered already
//   version       "version fw=... board=... role=bit-perfect-checker
//                 usb=<vid>:<pid>"
//   help          "help commands=status,result,mark,formats,allow_silence,version,help
//                 test=stall"
// Test command, not for normal use:
//   stall <ms>    "stall <ms>", then the main loop busy-waits 1 to 2000 ms
//                 with interrupts on: the receive FIFO fills and overflows
//                 during a stream, which shows OVERRUN
//
// Anything else gets "error unknown-command=<word>" or "error line-too-long".

#ifndef CONSOLE_H
#define CONSOLE_H

#include <stdint.h>

// For the board's UART interrupt: one received byte.
void console_rx_isr(uint8_t c);

// From the main loop: run the commands received so far.
void console_task(void);

// The version line, and the boot line: the same, then allow_silence=...
void console_version(const char *event);
void console_boot(void);

#endif

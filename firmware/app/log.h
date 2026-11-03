// Event log on the board's debug UART (the ST-LINK virtual COM port on the
// Nucleo), 115200 8N1.
//
// One line per event: "<seconds>.<milliseconds> <event> [key=value ...]".
// Lines are queued and sent from the UART interrupt, so logging never blocks
// the main loop. A line that does not fit the queue is dropped and counted;
// the next line that fits is preceded by "log lost=<n>". Part of the queue is
// kept for log_line_reserved(), so that result lines are not dropped when
// other lines fill it.
//
// The log functions are for the main loop only, never for an interrupt.

#ifndef LOG_H
#define LOG_H

#include <stdint.h>

#define LOG_BAUD     115200u
#define LOG_LINE_MAX 766u  // characters of a line, its time stamp included

// printf-like: %s %c %d %u %x %X %%, flag 0, a width, and the l and ll
// length modifiers.
void log_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

// The same, for the lines that must arrive: may use the reserved room.
void log_line_reserved(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

// Formats like log_line() into buf at *len, keeping it NUL-terminated
// within size bytes, and advances *len.
void log_append(char *buf, uint32_t size, uint32_t *len, const char *fmt, ...)
    __attribute__((format(printf, 4, 5)));

// For the board's UART interrupt: the next byte to send, or -1 when the
// queue is empty.
int log_tx_byte(void);

#endif

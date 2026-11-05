// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Event log: a ring buffer of whole lines, drained by the board's UART
// interrupt through log_tx_byte().

#include "log.h"

#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>

#include "board.h"

#define RING_SIZE    8192u  // power of two
#define RESERVE      3072u  // room only log_line_reserved() may use: four result lines

static uint8_t ring[RING_SIZE];
static volatile uint32_t ring_head;  // written by log_line()
static volatile uint32_t ring_tail;  // written by the interrupt
static uint32_t lines_lost;

int log_tx_byte(void) {
  uint32_t tail = ring_tail;
  if (tail == ring_head) {
    return -1;
  }
  int c = ring[tail];
  ring_tail = (tail + 1) & (RING_SIZE - 1);
  return c;
}

// Queue a whole line or nothing. Only a reserved line may use the last
// RESERVE bytes.
static bool ring_put(const char *s, uint32_t n, bool reserved) {
  uint32_t head = ring_head;
  uint32_t used = (head - ring_tail) & (RING_SIZE - 1);
  uint32_t room = RING_SIZE - 1 - used;
  if (!reserved) {
    room = (room > RESERVE) ? room - RESERVE : 0;
  }
  if (n > room) {
    return false;
  }
  for (uint32_t i = 0; i < n; i++) {
    ring[head] = (uint8_t) s[i];
    head = (head + 1) & (RING_SIZE - 1);
  }
  __asm__ volatile("" ::: "memory");  // the line's bytes before the head that publishes them
  ring_head = head;
  board_log_start();
  return true;
}

//--------------------------------------------------------------------+
// Formatting
//--------------------------------------------------------------------+

typedef struct {
  char *buf;
  uint32_t len;
  uint32_t max;  // characters it may hold
} line_t;

static void put_char(line_t *l, char c) {
  if (l->len < l->max) {
    l->buf[l->len++] = c;
  }
}

static void put_str(line_t *l, const char *s) {
  while (*s != '\0') {
    put_char(l, *s++);
  }
}

static void put_uint(line_t *l, uint64_t v, unsigned base, bool upper, unsigned width, char pad) {
  const char *digits = upper ? "0123456789ABCDEF" : "0123456789abcdef";
  char tmp[24];
  unsigned n = 0;
  while (v > UINT32_MAX) {  // a 64-bit division per digit costs a library call
    tmp[n++] = digits[v % base];
    v /= base;
  }
  uint32_t w = (uint32_t) v;
  do {
    tmp[n++] = digits[w % base];
    w /= base;
  } while (w != 0);
  while (width > n) {
    put_char(l, pad);
    width--;
  }
  while (n > 0) {
    put_char(l, tmp[--n]);
  }
}

static void format(line_t *l, const char *fmt, va_list ap) {
  for (; *fmt != '\0'; fmt++) {
    if (*fmt != '%') {
      put_char(l, *fmt);
      continue;
    }
    fmt++;
    char pad = ' ';
    if (*fmt == '0') {
      pad = '0';
      fmt++;
    }
    unsigned width = 0;
    while (*fmt >= '0' && *fmt <= '9') {
      width = width * 10 + (unsigned) (*fmt++ - '0');
    }
    int longs = 0;
    while (*fmt == 'l') {
      longs++;
      fmt++;
    }

    switch (*fmt) {
      case 'd': {
        int64_t v = (longs >= 2) ? va_arg(ap, long long) : (longs == 1) ? va_arg(ap, long) : va_arg(ap, int);
        if (v < 0) {
          put_char(l, '-');
          put_uint(l, (uint64_t) (-(v + 1)) + 1, 10, false, width > 0 ? width - 1 : 0, pad);
        } else {
          put_uint(l, (uint64_t) v, 10, false, width, pad);
        }
        break;
      }
      case 'u':
      case 'x':
      case 'X': {
        uint64_t v = (longs >= 2) ? va_arg(ap, unsigned long long)
                     : (longs == 1) ? va_arg(ap, unsigned long) : va_arg(ap, unsigned);
        put_uint(l, v, (*fmt == 'u') ? 10 : 16, *fmt == 'X', width, pad);
        break;
      }
      case 's':
        put_str(l, va_arg(ap, const char *));
        break;
      case 'c':
        put_char(l, (char) va_arg(ap, int));
        break;
      case '%':
        put_char(l, '%');
        break;
      default:  // unknown conversion, or the format ended
        return;
    }
  }
}

static void prefix(line_t *l) {
  uint32_t ms = board_millis();
  put_uint(l, ms / 1000, 10, false, 5, ' ');
  put_char(l, '.');
  put_uint(l, ms % 1000, 10, false, 3, '0');
  put_char(l, ' ');
}

// Ends the line with CR LF, which its buffer has room for beyond max.
static bool send(line_t *l, bool reserved) {
  l->buf[l->len++] = '\r';
  l->buf[l->len++] = '\n';
  return ring_put(l->buf, l->len, reserved);
}

static void log_va(bool reserved, const char *fmt, va_list ap) {
  if (lines_lost != 0) {
    char buf[40];
    line_t lost = {.buf = buf, .len = 0, .max = sizeof(buf) - 2};
    prefix(&lost);
    put_str(&lost, "log lost=");
    put_uint(&lost, lines_lost, 10, false, 0, ' ');
    if (!send(&lost, reserved)) {
      lines_lost++;
      return;
    }
    lines_lost = 0;
  }

  char buf[LOG_LINE_MAX + 2];
  line_t l = {.buf = buf, .len = 0, .max = LOG_LINE_MAX};
  prefix(&l);
  format(&l, fmt, ap);
  if (!send(&l, reserved)) {
    lines_lost++;
  }
}

void log_line(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  log_va(false, fmt, ap);
  va_end(ap);
}

void log_line_reserved(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  log_va(true, fmt, ap);
  va_end(ap);
}

void log_append(char *buf, uint32_t size, uint32_t *len, const char *fmt, ...) {
  if (size == 0 || *len >= size) {
    return;
  }
  line_t l = {.buf = buf, .len = *len, .max = size - 1};
  va_list ap;
  va_start(ap, fmt);
  format(&l, fmt, ap);
  va_end(ap);
  buf[l.len] = '\0';
  *len = l.len;
}

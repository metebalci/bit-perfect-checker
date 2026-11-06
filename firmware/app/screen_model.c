// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The status screen's content (screen_model.h). Verdicts are written as on
// the band (NOT BIT-PERFECT), the log's names without underscores. The
// detail rows, 30 columns each, a right-hand part right-aligned when both
// parts fit, else cut at column 30:
//   0  stream <id> [DSD name]        usb high|full|suspended|not connected
//   1  with an alt setting open: the stream's <rate> kHz <res>/<slot>, and
//      once measured PCM|DoP bits <n> (the DSD name in row 0 for DoP at
//      BPC1's DSD rates); else allow_silence <setting>, <version>
//   2  the stream's lost <n>  ovf <n>                  the run's results_lost <n>
//   3-6 the run's results by verdict, BIT-PERFECT first
//   7-8 the file being checked once it has a difference: file NOT BIT-PERFECT,
//      diff <s>; else the last result line: last <verdict> end <end>,
//      lost <n>  ovf <n>  diff <s>

#include "screen_model.h"

#include <string.h>

#include "bpc1_checker.h"

typedef struct {
  char *buf;
  uint32_t size;
  uint32_t len;
} text_t;

static void put(text_t *t, const char *s) {
  while (*s != '\0' && t->len + 1u < t->size) {
    t->buf[t->len++] = *s++;
  }
  t->buf[t->len] = '\0';
}

static void put_u(text_t *t, uint32_t v) {
  char digits[11];
  uint32_t n = 0;
  do {
    digits[n++] = (char) ('0' + v % 10u);
    v /= 10u;
  } while (v != 0);
  char s[2] = {0, 0};
  while (n > 0) {
    s[0] = digits[--n];
    put(t, s);
  }
}

// kHz with the decimals the rate has, no unit: 44.1, 48, 705.6
static void put_khz_number(text_t *t, uint32_t rate) {
  put_u(t, rate / 1000u);
  uint32_t frac = rate % 1000u;
  if (frac != 0) {
    char s[5] = {'.', (char) ('0' + frac / 100u), (char) ('0' + frac / 10u % 10u), (char) ('0' + frac % 10u), 0};
    uint32_t end = 4;
    while (s[end - 1] == '0') {
      s[--end] = '\0';
    }
    put(t, s);
  }
}

static void put_khz(text_t *t, uint32_t rate) {
  put_khz_number(t, rate);
  put(t, " kHz");
}

// The DSD name of a DoP stream at the frame rates of BPC1's DSD rates
// (docs/bpc1.md 3.3: 16 DSD bits per DoP frame), else NULL
static const char *dsd_name(uint32_t rate) {
  switch (rate) {
    case 176400: return "DSD64";
    case 352800: return "DSD128";
    case 705600: return "DSD256";
    default: return NULL;
  }
}

// left, then right right-aligned if both fit with a space between, else
// right after one space; cut at width and padded with spaces
static void row_in(char *out, size_t width, const char *left, const char *right) {
  size_t l = strlen(left);
  size_t r = strlen(right);
  memset(out, ' ', width);
  out[width] = '\0';
  memcpy(out, left, l < width ? l : width);
  if (r == 0) {
    return;
  }
  size_t at = (l + 1u + r <= width) ? width - r : l + 1u;
  for (size_t i = 0; i < r && at + i < width; i++) {
    out[at + i] = right[i];
  }
}

static void row(char *out, const char *left, const char *right) {
  row_in(out, SCREEN_COLS, left, right);
}

// text centred in width columns, padded with spaces, cut at width
static void centre(char *out, uint32_t width, const char *text) {
  size_t n = strlen(text);
  n = n < width ? n : width;
  memset(out, ' ', width);
  out[width] = '\0';
  memcpy(out + (width - n) / 2u, text, n);
}

#define TEXT(name, n) \
  char name##_buf[n];  \
  text_t name = {name##_buf, n, 0}; \
  name##_buf[0] = '\0'

// A verdict as the screen writes it: without the underscores of the log and
// the spec, BIT-PERFECT as in the project's name
static const char *verdict_word(uint8_t verdict) {
  static const char *const words[] = {
    [BPC1_UNKNOWN_SIGNAL] = "UNKNOWN SIGNAL",
    [BPC1_BIT_PERFECT] = "BIT-PERFECT",
    [BPC1_NOT_BIT_PERFECT] = "NOT BIT-PERFECT",
    [BPC1_OVERRUN] = "OVERRUN",
  };
  return verdict < sizeof(words) / sizeof(words[0]) ? words[verdict] : "?";
}

static void band(const status_t *s, screen_band_t *b) {
  static const char *const words[] = {
    [SCREEN_NOT_CONNECTED] = "NOT CONNECTED",
    [SCREEN_WAITING] = "WAITING",
    [SCREEN_CHECKING] = "CHECKING",
    [SCREEN_FULL_SPEED] = "NEEDS\nHIGH-SPEED\nUSB PORT",
  };
  if (s->streaming) {
    b->state = SCREEN_CHECKING;
  } else if (s->usb == USB_NOT_MOUNTED) {
    b->state = SCREEN_NOT_CONNECTED;
  } else {
    b->state = s->high_speed ? SCREEN_WAITING : SCREEN_FULL_SPEED;
  }
  b->hourglass = b->state == SCREEN_WAITING || b->state == SCREEN_CHECKING;
  b->tall = b->state == SCREEN_FULL_SPEED;
  // The run's state, as the LEDs show it (leds.c): red for any failure,
  // green for BIT_PERFECT only; red at Full-Speed
  bool failed = s->run_fail || s->run_unknown || s->run_overrun;
  b->colour = failed || b->tall ? SCREEN_RED : s->run_pass ? SCREEN_GREEN : SCREEN_BLUE;
  text_t word = {b->word, sizeof(b->word), 0};
  put(&word, words[b->state]);
}

bool screen_blink_lit(uint32_t ms) {
  return ((ms / 500u) & 1u) != 0;  // as leds_task()'s slow blink
}

bool screen_hourglass_lit(uint8_t hourglass, uint32_t ms) {
  if (hourglass == SCREEN_HOURGLASS_BLINK) {
    return screen_blink_lit(ms);
  }
  return hourglass == SCREEN_HOURGLASS_STEADY;
}

uint8_t screen_hourglass(const screen_band_t *b, bool moving) {
  if (!b->hourglass) {
    return SCREEN_HOURGLASS_NONE;
  }
  return b->state != SCREEN_CHECKING || moving ? SCREEN_HOURGLASS_BLINK : SCREEN_HOURGLASS_STEADY;
}

// The file as its header gives it, on two lines: the kind as it arrives
// (PCM, PCM SWEEP, or DoP: this device receives DSD only as DoP), then the
// depth and the rate in kHz (24/96, 16/44.1, at most 8 characters) or the
// DSD rate (DSD64, DSD128, DSD256: docs/bpc1.md 3.3)
static void file_lines(uint8_t kind, uint32_t rate, uint8_t depth, char out[2][SCREEN_BAND_COLS + 1]) {
  TEXT(a, 32);
  TEXT(b, 32);
  if (kind == BPC1_KIND_DSD) {
    put(&a, "DoP");
    put(&b, "DSD");
    put_u(&b, rate / 44100u);
  } else if (kind == BPC1_KIND_PCM || kind == BPC1_KIND_SWEEP) {
    put(&a, kind == BPC1_KIND_PCM ? "PCM" : "PCM SWEEP");
    put_u(&b, depth);
    put(&b, "/");
    put_khz_number(&b, rate);
  }
  centre(out[0], SCREEN_BAND_COLS, a.buf);
  centre(out[1], SCREEN_BAND_COLS, b.buf);
}

void screen_model(const status_t *s, const char *fw, screen_t *out) {
  memset(out, 0, sizeof(*out));
  band(s, &out->band);

  // The run's errors
  row_in(out->labels, SCREEN_LABEL_COLS, s->run_unknown ? " UNKNOWN" : "", s->run_overrun ? "OVERRUN " : "");

  // The file: the open stream's, else the last result's while it belongs to
  // the run that runs now, so that any run start (B1, a tap, mark, a pulled
  // cable) clears it
  if (s->streaming) {
    file_lines(s->file_kind, s->file_rate, s->file_depth, out->file);
  } else if (s->last_known && s->last_in_run) {
    file_lines(s->last_kind, s->last_rate, s->last_depth, out->file);
  } else {
    file_lines(0, 0, 0, out->file);
  }
  {
    centre(out->fw, SCREEN_COLS, fw);
  }

  // 0: the stream (the band says whether it is open), the USB device
  {
    TEXT(left, 48);
    TEXT(right, 32);
    if (s->stream_id != 0) {
      put(&left, "stream ");
      put_u(&left, s->stream_id);
    }
    const char *dsd = dsd_name(s->rate);
    if (s->alt != 0 && s->bits != 0 && s->dop && dsd != NULL) {
      put(&left, left.len != 0 ? " " : "");
      put(&left, dsd);
    }
    if (s->usb == USB_SUSPENDED) {
      put(&right, "usb suspended");
    } else if (s->usb == USB_MOUNTED) {
      put(&right, s->high_speed ? "usb high" : "usb full");
    } else {
      put(&right, "usb not connected");
    }
    row(out->rows[0], left.buf, right.buf);
  }

  // 1: the stream's format, or the settings while no alt setting is open
  {
    TEXT(left, 48);
    TEXT(right, 48);
    if (s->alt != 0) {
      put_khz(&left, s->rate);
      put(&left, " ");
      put_u(&left, s->resolution);
      put(&left, "/");
      put_u(&left, s->slot);
      if (s->bits != 0) {
        put(&left, s->dop ? " DoP bits " : " PCM bits ");
        put_u(&left, s->bits);
      }
    } else {
      put(&left, "allow_silence ");
      put(&left, s->allow_silence ? "true" : "false");
      put(&right, fw);
    }
    row(out->rows[1], left.buf, right.buf);
  }

  // 2: the stream's losses, the run's dropped results
  {
    TEXT(left, 48);
    TEXT(right, 32);
    put(&left, "lost ");
    put_u(&left, s->lost);
    put(&left, "  ovf ");
    put_u(&left, s->overflows);
    put(&right, "results_lost ");
    put_u(&right, s->run_results_lost);
    row(out->rows[2], left.buf, right.buf);
  }

  // 3-6: the run's results by verdict
  static const uint8_t order[] = {BPC1_BIT_PERFECT, BPC1_NOT_BIT_PERFECT, BPC1_UNKNOWN_SIGNAL, BPC1_OVERRUN};
  for (uint32_t i = 0; i < sizeof(order); i++) {
    TEXT(count, 16);
    put_u(&count, s->run_count[order[i]]);
    row(out->rows[3 + i], verdict_word(order[i]), count.buf);
  }

  // 7-8: the file with a difference, else the last result line while it
  // belongs to the run that runs now (as the file lines)
  {
    TEXT(r7, 48);
    TEXT(r8, 48);
    if (s->file_diff_known) {
      put(&r7, "file ");
      put(&r7, verdict_word(BPC1_NOT_BIT_PERFECT));
      put(&r8, "diff ");
      put(&r8, s->file_diff);
    } else if (s->last_known && s->last_in_run) {
      put(&r7, "last ");
      put(&r7, verdict_word(s->last_verdict));
      put(&r7, " end ");
      put(&r7, bpc1_end_name(s->last_end));
      put(&r8, "lost ");
      if (s->last_lost < 0) {
        put(&r8, "-");
        put_u(&r8, 0u - (uint32_t) s->last_lost);
      } else {
        put_u(&r8, (uint32_t) s->last_lost);
      }
      put(&r8, "  ovf ");
      put_u(&r8, s->last_overflows);
      put(&r8, "  diff ");
      put(&r8, s->last_diff);
    }
    row(out->rows[7], r7.buf, "");
    row(out->rows[8], r8.buf, "");
  }
}

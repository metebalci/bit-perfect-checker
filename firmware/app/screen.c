// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The status screen on a panel (screen.h). The work comes in units small
// enough for a slice: a strip of the whole panel (the clear after the
// splash), a strip of the band, a glyph of the band's word, a glyph of a
// placed text. Each unit's time is measured once at start-up; a slice
// starts a unit only if it still fits the slice's time, and always draws at
// least one.

#include "screen.h"

#include <stdbool.h>
#include <string.h>

#include "board.h"
#include "load.h"
#include "log.h"
#include "panel.h"
#include "screen_model.h"
#include "stream.h"
#include "version.h"

#define STRIP_LINES 4u  // band lines per unit

typedef enum {
  UNIT_CLEAR,  // a strip of the whole panel, after the splash
  UNIT_STRIP,
  UNIT_BAND_GLYPH,
  UNIT_HOURGLASS,
  UNIT_TEXT_GLYPH,
} unit_kind_t;

typedef struct {
  uint8_t kind;
  uint16_t index;  // strip, character of the band's word, or cell (place-major)
} unit_t;

static const screen_layout_t *lay;  // NULL: no panel
static uint32_t cycles_per_us;
static uint32_t clear_cycles, strip_cycles, band_glyph_cycles;  // each unit's time, measured
static uint32_t place_cycles[SCREEN_PLACES_MAX];   // a glyph of each place

static screen_t want;                       // what the panel is to show
static screen_band_t band_shown;            // the band on the panel, if band_valid
static bool band_valid;
static screen_band_t band_target;           // the band being drawn, if band_busy
static bool band_busy;
static uint16_t band_step;                  // its strips, then its word's characters
static uint16_t band_cover;                 // the lines its strips cover: its height, more to clear a
                                            // taller band
static uint16_t band_painted;               // the lines a band may take on the panel
static bool splash;                         // the splash shows, until splash_end_ms
static uint32_t splash_end_ms;
static uint16_t clear_line;                 // the screen cleared above this line after the splash
static bool hourglass_shown;                // on the panel, beside the band's word
static uint32_t hourglass_packets;          // stream_packets() when last seen
static uint32_t hourglass_moved_ms;         // when it last moved
static char cells[SCREEN_PLACES_MAX][SCREEN_COLS];  // the placed texts on the panel, DARK_BIT
                                                    // set in a blinking text's dark glyphs
static bool blink_lit;                      // the blinking texts are lit
static uint32_t cursor;                     // the next cell to compare

// The redraw under way: from the first change the panel does not show yet
// until the panel shows everything
static bool redrawing;
static uint32_t since_ms;
static uint32_t px, busy_cycles, slices, max_slice_cycles, max_slice_isr;
#if defined(DISPLAY_LOG) && defined(DISPLAY_STRESS)
static uint32_t redraws, logged_ms, lag_max_ms;
#endif

static bool band_equal(const screen_band_t *a, const screen_band_t *b) {
  return a->state == b->state && a->colour == b->colour && a->hourglass == b->hourglass && a->tall == b->tall
         && strcmp(a->word, b->word) == 0;
}

// A placed text in want, and its width in characters
static const char *text_of(uint8_t text, uint32_t *width) {
  if (text == SCREEN_TEXT_FILE0 || text == SCREEN_TEXT_FILE1) {
    *width = SCREEN_BAND_COLS;
    return want.file[text - SCREEN_TEXT_FILE0];
  }
  if (text == SCREEN_TEXT_LABELS) {
    *width = SCREEN_LABEL_COLS;
    return want.labels;
  }
  *width = SCREEN_COLS;
  return text == SCREEN_TEXT_FW ? want.fw : want.rows[text - SCREEN_TEXT_ROW0];
}

static uint16_t band_height(const screen_band_t *b) {
  return b->tall ? lay->band_tall_h : lay->band_h;
}

// A placed text that the tall band covers while it is to show
static bool covered(uint32_t place) {
  return want.band.tall && lay->places[place].y < lay->band_tall_h;
}

static uint16_t band_strips(void) {
  return (uint16_t) ((band_cover + STRIP_LINES - 1u) / STRIP_LINES);
}

// The lines of a strip: STRIP_LINES, fewer in the last
static uint16_t strip_lines(uint16_t strip) {
  uint32_t left = (uint32_t) band_cover - (uint32_t) strip * STRIP_LINES;
  return (uint16_t) (left < STRIP_LINES ? left : STRIP_LINES);
}

// The layout's area
static uint16_t area_x(void) {
  return lay->x;
}

static uint16_t area_w(void) {
  return lay->width != 0u ? lay->width : panel_width();
}

//--------------------------------------------------------------------+
// Units
//--------------------------------------------------------------------+

// Below the band, the lines a taller band took are cleared
static void draw_strip(const screen_band_t *b, uint16_t strip) {
  uint16_t w = area_w();
  uint16_t y0 = (uint16_t) (strip * STRIP_LINES);
  uint16_t n = strip_lines(strip);
  uint16_t f = lay->band_frame;
  uint16_t h = band_height(b);
  panel_window(area_x(), y0, w, n);
  for (uint16_t y = y0; y < y0 + n; y++) {
    if (y >= h) {
      panel_fill(SCREEN_BLACK, w);
    } else if (y < f || y >= h - f) {
      panel_fill(b->colour, w);
    } else {
      panel_fill(b->colour, f);
      panel_fill(SCREEN_BLACK, (uint32_t) (w - 2u * f));
      panel_fill(b->colour, f);
    }
  }
}

#define BAND_LINE_GAP 4u  // between the two lines of a broken word
#define HOURGLASS_W   16u  // at 1x
#define HOURGLASS_H   32u
#define STALLED_MS    20u  // CHECKING without a packet this long: the hourglass stops blinking
                            // (the stream stops after STREAM_IDLE_MS, 50 ms)

// An hourglass, 16x32, rows top to bottom, most significant bit left: the
// outline only, no sand
static const uint8_t hourglass[HOURGLASS_H * 2u] = {
  0xFF, 0xFF, 0xFF, 0xFF, 0x60, 0x06, 0x60, 0x06, 0x30, 0x0C, 0x30, 0x0C, 0x18, 0x18, 0x18, 0x18,
  0x0C, 0x30, 0x0C, 0x30, 0x06, 0x60, 0x06, 0x60, 0x03, 0xC0, 0x03, 0xC0, 0x01, 0x80, 0x01, 0x80,
  0x01, 0x80, 0x01, 0x80, 0x03, 0xC0, 0x03, 0xC0, 0x06, 0x60, 0x06, 0x60, 0x0C, 0x30, 0x0C, 0x30,
  0x18, 0x18, 0x18, 0x18, 0x30, 0x0C, 0x30, 0x0C, 0x60, 0x06, 0x60, 0x06, 0xFF, 0xFF, 0xFF, 0xFF,
};

// The hourglass as drawn: at the layout's scale, made by screen_init()
static uint8_t hourglass_2x[HOURGLASS_H * 2u * 2u * 2u];
static const uint8_t *hourglass_bits;

static uint32_t hourglass_scale(void) {
  return lay->hourglass_scale == 2u ? 2u : 1u;
}

static uint16_t hourglass_w(void) {
  return (uint16_t) (HOURGLASS_W * hourglass_scale());
}

static uint16_t hourglass_h(void) {
  return (uint16_t) (HOURGLASS_H * hourglass_scale());
}

// A bitmap of w x h pixels at twice its size, each pixel two wide and two
// high; rows of both padded to whole bytes, the most significant bit first
// (as fonts.h lays out glyphs)
static void double_bits(const uint8_t *src, uint32_t w, uint32_t h, uint8_t *dst) {
  uint32_t src_stride = (w + 7u) / 8u;
  uint32_t dst_stride = (2u * w + 7u) / 8u;
  for (uint32_t r = 0; r < h; r++) {
    uint8_t *out = &dst[2u * r * dst_stride];
    memset(out, 0, dst_stride);
    for (uint32_t i = 0; i < w; i++) {
      if ((src[r * src_stride + i / 8u] & (0x80u >> (i % 8u))) != 0u) {
        out[(2u * i) / 8u] |= (uint8_t) (0xC0u >> ((2u * i) % 8u));
      }
    }
    memcpy(out + dst_stride, out, dst_stride);
  }
}

void screen_font_double(const font_t *src, font_t *dst, uint8_t *bits) {
  uint32_t src_bytes = src->glyph_bytes;
  uint32_t dst_bytes = (2u * src->width + 7u) / 8u * 2u * src->height;
  for (uint32_t g = 0; g < src->count; g++) {
    double_bits(src->bits + g * src_bytes, src->width, src->height, bits + g * dst_bytes);
  }
  *dst = (font_t) {.width = (uint8_t) (2u * src->width), .height = (uint8_t) (2u * src->height),
                   .first = src->first, .count = src->count, .glyph_bytes = (uint16_t) dst_bytes, .bits = bits};
}

// A glyph into the window just set. A font pads each row to whole bytes
// (fonts.h), panel_bits() reads one run of bits: a width that is not a
// multiple of 8 goes row by row.
static void glyph_bits(const font_t *font, char c, uint32_t fg, uint32_t bg) {
  const uint8_t *g = font_glyph(font, c);
  uint32_t stride = (font->width + 7u) / 8u;
  if (stride * 8u == font->width) {
    panel_bits(g, (uint32_t) font->width * font->height, fg, bg);
    return;
  }
  for (uint32_t r = 0; r < font->height; r++) {
    panel_bits(g + r * stride, font->width, fg, bg);
  }
}

// A one-line word with the hourglass right of it as the next character after
// a space: the three centred together if they fit inside the frame and its
// margins, else the word at the left margin and the hourglass at the right one
static uint32_t inside_left(void) {
  return lay->band_frame + lay->band_margin;
}

static uint32_t group_width(const screen_band_t *b) {
  return (strlen(b->word) + 1u) * lay->band_font->width + hourglass_w();  // the word, a space, the hourglass
}

static uint32_t hourglass_word_x(const screen_band_t *b) {
  uint32_t room = area_w() - 2u * inside_left();
  uint32_t g = group_width(b);
  return area_x() + (g <= room ? (area_w() - g) / 2u : inside_left());
}

static uint16_t hourglass_x(const screen_band_t *b) {
  uint32_t room = area_w() - 2u * inside_left();
  if (group_width(b) <= room) {
    return (uint16_t) (hourglass_word_x(b) + group_width(b) - hourglass_w());
  }
  return (uint16_t) (area_x() + area_w() - inside_left() - hourglass_w());
}

// Where character i of the band's word goes: false for a space, which the
// strips drew, and for a line break. The word's lines end at each '\n';
// a word without one that is too wide breaks at its first space.
static bool band_glyph_at(const screen_band_t *b, uint16_t i, uint16_t *x, uint16_t *y) {
  const font_t *font = lay->band_font;
  uint32_t n = strlen(b->word);
  uint32_t fits = (area_w() - 2u * (lay->band_frame + lay->band_margin)) / font->width;
  const char *space = strchr(b->word, ' ');
  bool newlines = strchr(b->word, '\n') != NULL;
  uint32_t space_break = (!newlines && n > fits && space != NULL) ? (uint32_t) (space - b->word) : n;
  if (b->word[i] == ' ' || b->word[i] == '\n') {
    return false;
  }
  uint32_t lines = 1;
  uint32_t line = 0;
  uint32_t start = 0;  // of character i's line
  for (uint32_t k = 0; k < n; k++) {
    if (b->word[k] == '\n' || k == space_break) {
      lines++;
      if (k < i) {
        line++;
        start = k + 1u;
      }
    }
  }
  uint32_t end = start;
  while (end < n && b->word[end] != '\n' && end != space_break) {
    end++;
  }
  uint32_t len = end - start;
  uint32_t col = i - start;
  uint32_t top = (band_height(b) - lines * font->height - (lines - 1u) * BAND_LINE_GAP) / 2u;
  uint32_t left = b->hourglass ? hourglass_word_x(b) : area_x() + (area_w() - len * font->width) / 2u;
  *x = (uint16_t) (left + col * font->width);
  *y = (uint16_t) (top + line * (font->height + BAND_LINE_GAP));
  return true;
}

// The hourglass, right of the band's one-line word, in white or black
static void draw_hourglass(const screen_band_t *b, bool on) {
  uint16_t x = hourglass_x(b);
  uint16_t y = (uint16_t) ((lay->band_h - hourglass_h()) / 2u);
  panel_window(x, y, hourglass_w(), hourglass_h());
  panel_bits(hourglass_bits, (uint32_t) hourglass_w() * hourglass_h(), on ? SCREEN_WHITE : SCREEN_BLACK, SCREEN_BLACK);
}

// Whether the hourglass is to be on now: blinking, but during CHECKING only
// while packets arrive (the interrupt's packet count moved within
// STALLED_MS); steady on once they stop
static bool hourglass_due(void) {
  uint32_t now = board_millis();
  uint32_t p = stream_packets();
  if (p != hourglass_packets) {
    hourglass_packets = p;
    hourglass_moved_ms = now;
  }
  return screen_hourglass_lit(screen_hourglass(&want.band, now - hourglass_moved_ms < STALLED_MS), now);
}

static void draw_band_glyph(const screen_band_t *b, uint16_t i) {
  const font_t *font = lay->band_font;
  uint16_t x;
  uint16_t y;
  if (!band_glyph_at(b, i, &x, &y)) {
    return;
  }
  panel_window(x, y, font->width, font->height);
  glyph_bits(font, b->word[i], SCREEN_WHITE, SCREEN_BLACK);
}

#define DARK_BIT 0x80u  // above the printable ASCII the fonts hold

// A cell as it is to be on the panel: the character, with DARK_BIT while a
// blinking text is dark (not for spaces, which look the same)
static char cell_want(uint32_t place, uint32_t col) {
  uint32_t width;
  char c = text_of(lay->places[place].text, &width)[col];
  bool dark = lay->places[place].blink && !blink_lit && c != ' ';
  return (char) ((uint8_t) c | (dark ? DARK_BIT : 0u));
}

static void draw_cell(uint8_t place, uint32_t col, char c, uint32_t fg) {
  const screen_place_t *p = &lay->places[place];
  uint32_t width;
  (void) text_of(p->text, &width);
  uint16_t w = p->font->width;
  uint16_t ax = p->width != 0u ? p->x : area_x();
  uint16_t aw = p->width != 0u ? p->width : area_w();
  uint16_t x0 = (uint16_t) (ax + (aw - width * w) / 2u);
  panel_window((uint16_t) (x0 + col * w), p->y, w, p->font->height);
  glyph_bits(p->font, c, fg, SCREEN_BLACK);
}

static void draw_text_glyph(uint8_t place, uint32_t col, char cell) {
  bool dark = ((uint8_t) cell & DARK_BIT) != 0;
  draw_cell(place, col, (char) ((uint8_t) cell & ~DARK_BIT), dark ? SCREEN_BLACK : lay->places[place].colour);
}

// The band's next unit, if the band is not shown yet; starts it over when
// the band to show changed
static bool band_next(unit_t *u) {
  if (band_valid && band_equal(&band_shown, &want.band)) {
    return false;
  }
  if (!band_busy || !band_equal(&band_target, &want.band)) {
    band_target = want.band;
    band_busy = true;
    band_valid = false;
    band_step = 0;
    hourglass_shown = false;  // the strips clear it
    uint16_t h = band_height(&band_target);
    band_cover = h > band_painted ? h : band_painted;
    band_painted = band_cover;
    // The texts the strips draw over: drawn again once the band is done and
    // does not cover them
    for (uint32_t i = 0; i < lay->n_places; i++) {
      if (lay->places[i].y < band_cover) {
        memset(cells[i], 0, sizeof(cells[i]));
      }
    }
  }
  uint16_t strips = band_strips();
  if (band_step < strips) {
    *u = (unit_t) {UNIT_STRIP, band_step};
    return true;
  }
  uint16_t n = (uint16_t) strlen(band_target.word);
  uint16_t x;
  uint16_t y;
  while (band_step - strips < n && !band_glyph_at(&band_target, (uint16_t) (band_step - strips), &x, &y)) {
    band_step++;  // the strips drew the background
  }
  if (band_step - strips < n) {
    *u = (unit_t) {UNIT_BAND_GLYPH, (uint16_t) (band_step - strips)};
    return true;
  }
  band_shown = band_target;
  band_valid = true;
  band_busy = false;
  band_painted = band_height(&band_target);
  return false;
}

// The next character of the placed texts that differs from the panel
static bool texts_next(unit_t *u) {
  for (; cursor < (uint32_t) lay->n_places * SCREEN_COLS; cursor++) {
    uint32_t place = cursor / SCREEN_COLS;
    uint32_t width;
    (void) text_of(lay->places[place].text, &width);
    uint32_t c = cursor % SCREEN_COLS;
    if (c < width && !covered(place) && cells[place][c] != cell_want(place, c)) {
      *u = (unit_t) {UNIT_TEXT_GLYPH, (uint16_t) cursor};
      return true;
    }
  }
  return false;
}

// A strip of the whole panel in black from line y: STRIP_LINES, fewer at the
// bottom
static uint16_t clear_strip(uint16_t y) {
  uint32_t left = (uint32_t) panel_height() - y;
  uint16_t n = (uint16_t) (left < STRIP_LINES ? left : STRIP_LINES);
  panel_window(0, y, panel_width(), n);
  panel_fill(SCREEN_BLACK, (uint32_t) panel_width() * n);
  return n;
}

// The screen's next strip to clear after the splash
static bool clear_next(unit_t *u) {
  if (clear_line >= panel_height()) {
    return false;
  }
  *u = (unit_t) {UNIT_CLEAR, clear_line};
  return true;
}

static uint32_t draw(const unit_t *u) {
  if (u->kind == UNIT_CLEAR) {
    uint16_t n = clear_strip(u->index);
    clear_line = (uint16_t) (u->index + n);
    return (uint32_t) panel_width() * n;
  }
  if (u->kind == UNIT_STRIP) {
    draw_strip(&band_target, u->index);
    band_step++;
    return (uint32_t) area_w() * strip_lines(u->index);
  }
  if (u->kind == UNIT_HOURGLASS) {
    hourglass_shown = !hourglass_shown;
    draw_hourglass(&band_shown, hourglass_shown);
    return (uint32_t) hourglass_w() * hourglass_h();
  }
  if (u->kind == UNIT_BAND_GLYPH) {
    draw_band_glyph(&band_target, u->index);
    band_step++;
    return (uint32_t) lay->band_font->width * lay->band_font->height;
  }
  uint32_t place = u->index / SCREEN_COLS;
  uint32_t col = u->index % SCREEN_COLS;
  char c = cell_want(place, col);
  draw_text_glyph((uint8_t) place, col, c);
  cells[place][col] = c;
  cursor = u->index + 1u;
  const screen_place_t *p = &lay->places[place];
  return (uint32_t) p->font->width * p->font->height;
}

//--------------------------------------------------------------------+
// The redraw
//--------------------------------------------------------------------+

static bool panel_differs(void) {
  if (!band_valid || !band_equal(&band_shown, &want.band)) {
    return true;
  }
  for (uint32_t i = 0; i < lay->n_places; i++) {
    uint32_t width;
    (void) text_of(lay->places[i].text, &width);
    for (uint32_t c = 0; c < width && !covered(i); c++) {
      if (cells[i][c] != cell_want(i, c)) {
        return true;
      }
    }
  }
  return false;
}

// The cycles of the slowest unit kind that might come next
static uint32_t unit_estimate(const unit_t *u) {
  if (u->kind == UNIT_CLEAR) {
    return clear_cycles;  // the whole panel's width, the band's strip only the area's
  }
  if (u->kind == UNIT_STRIP) {
    return strip_cycles;
  }
  if (u->kind == UNIT_BAND_GLYPH || u->kind == UNIT_HOURGLASS) {
    return band_glyph_cycles;  // the same size
  }
  return place_cycles[u->index / SCREEN_COLS];
}

static void redraw_start(void) {
  redrawing = true;
  since_ms = board_millis();
  px = busy_cycles = slices = max_slice_cycles = max_slice_isr = 0;
}

static void redraw_done(void) {
  uint32_t lag_ms = board_millis() - since_ms;
  redrawing = false;
#if defined(DISPLAY_LOG) && defined(DISPLAY_STRESS)
  // A line a second at most, for every redraw since the last line
  static uint32_t sum_px, sum_busy, sum_slices, max_slice, max_isr;
  redraws++;
  sum_px += px;
  sum_busy += busy_cycles / cycles_per_us;
  sum_slices += slices;
  if (max_slice_cycles > max_slice) {
    max_slice = max_slice_cycles;
    max_isr = max_slice_isr;
  }
  lag_max_ms = lag_ms > lag_max_ms ? lag_ms : lag_max_ms;
  if (board_millis() - logged_ms >= 1000u) {
    log_line("display drawn redraws=%lu px=%lu busy_us=%lu slices=%lu max_slice_us=%lu lag_ms=%lu "
             "max_slice_isr_us=%lu",
             (unsigned long) redraws, (unsigned long) sum_px, (unsigned long) sum_busy,
             (unsigned long) sum_slices, (unsigned long) (max_slice / cycles_per_us), (unsigned long) lag_max_ms,
             (unsigned long) (max_isr / cycles_per_us));
    logged_ms = board_millis();
    redraws = sum_px = sum_busy = sum_slices = max_slice = max_isr = lag_max_ms = 0;
  }
#elif defined(DISPLAY_LOG)
  log_line("display drawn px=%lu busy_us=%lu slices=%lu max_slice_us=%lu lag_ms=%lu max_slice_isr_us=%lu",
           (unsigned long) px, (unsigned long) (busy_cycles / cycles_per_us), (unsigned long) slices,
           (unsigned long) (max_slice_cycles / cycles_per_us), (unsigned long) lag_ms,
           (unsigned long) (max_slice_isr / cycles_per_us));
#else
  (void) lag_ms;
#endif
#ifdef DISPLAY_STRESS
  // Everything again: the band and every character
  band_valid = false;
  memset(cells, 0, sizeof(cells));
  cursor = 0;
  redraw_start();
#endif
}

//--------------------------------------------------------------------+
// API
//--------------------------------------------------------------------+

// A line of text at y, white on black, centred in cols columns centred
// in the area (as the status screen centres its texts)
static void splash_text(const font_t *font, uint16_t y, const char *t, uint32_t cols) {
  uint32_t n = strlen(t);
  n = n < cols ? n : cols;
  uint16_t x = (uint16_t) (area_x() + (area_w() - cols * font->width) / 2u + (cols - n) / 2u * font->width);
  for (uint32_t i = 0; i < n; i++, x = (uint16_t) (x + font->width)) {
    panel_window(x, y, font->width, font->height);
    glyph_bits(font, t[i], SCREEN_WHITE, SCREEN_BLACK);
  }
}

static void splash_draw(void) {
  static const char *const name[] = {"BIT-PERFECT", "CHECKER"};
  static const char url[] = "bpc.metebalci.com";
  for (uint32_t i = 0; i < 2u; i++) {
    splash_text(lay->band_font, (uint16_t) (lay->splash_y + i * (lay->band_font->height + 4u)), name[i],
                (uint32_t) strlen(name[i]));
  }
  splash_text(lay->splash_font, lay->splash_url_y, url, sizeof(url) - 1u);
  for (uint32_t i = 0; i < lay->n_places; i++) {
    if (lay->places[i].text == SCREEN_TEXT_FW) {
      splash_text(lay->places[i].font, lay->places[i].y, FW_VERSION, SCREEN_COLS);
    }
  }
}

void screen_splash_led(uint8_t led) {
  // red, green, blue from the left, a square apart
  static const uint32_t colours[LED_COUNT] = {[LED_RED] = SCREEN_RED, [LED_GREEN] = SCREEN_GREEN,
                                              [LED_BLUE] = SCREEN_BLUE};
  static const uint8_t slot[LED_COUNT] = {[LED_RED] = 0, [LED_GREEN] = 1, [LED_BLUE] = 2};
  if (lay == NULL || !splash || led >= LED_COUNT) {
    return;
  }
  uint16_t side = lay->splash_square;
  uint16_t x0 = (uint16_t) (area_x() + (area_w() - 5u * side) / 2u);
  panel_window((uint16_t) (x0 + 2u * side * slot[led]), lay->splash_squares_y, side, side);
  panel_fill(colours[led], (uint32_t) side * side);
}

void screen_init(const screen_layout_t *layout) {
  lay = layout;
  if (hourglass_scale() == 2u) {
    double_bits(hourglass, HOURGLASS_W, HOURGLASS_H, hourglass_2x);
    hourglass_bits = hourglass_2x;
  } else {
    hourglass_bits = hourglass;
  }
  cycles_per_us = board_cpu_hz() / 1000000u;
  memset(cells, ' ', sizeof(cells));  // the panel is black
  band_valid = false;
  band_cover = lay->band_h;  // for the strip's measurement
  band_painted = 0;
  clear_line = panel_height();

  // Each unit's time, drawn in black on the black panel: the longest of a
  // few, and an eighth more
  static const screen_band_t black = {.state = SCREEN_WAITING, .colour = SCREEN_BLACK, .word = "W"};
  for (uint32_t k = 0; k < 3u + lay->n_places; k++) {
    uint32_t longest = 0;
    for (int i = 0; i < 4; i++) {
      uint32_t t0 = board_cycles();
      if (k == 0) {
        draw_strip(&black, 1);
      } else if (k == 1) {
        draw_band_glyph(&black, 0);
      } else if (k == 2) {
        (void) clear_strip(0);
      } else {
        draw_cell((uint8_t) (k - 3u), 0, 'W', SCREEN_BLACK);
      }
      uint32_t t = board_cycles() - t0;
      longest = t > longest ? t : longest;
    }
    longest += longest / 8u;
    if (k == 0) {
      strip_cycles = longest;
    } else if (k == 1) {
      band_glyph_cycles = longest;
    } else if (k == 2) {
      clear_cycles = longest;
    } else {
      place_cycles[k - 3u] = longest;
    }
  }
#ifdef DISPLAY_LOG
  log_line("display units strip_us=%lu band_glyph_us=%lu text_glyph_us=%lu clear_us=%lu",
           (unsigned long) (strip_cycles / cycles_per_us), (unsigned long) (band_glyph_cycles / cycles_per_us),
           (unsigned long) (lay->n_places != 0 ? place_cycles[0] / cycles_per_us : 0u),
           (unsigned long) (clear_cycles / cycles_per_us));
#endif
  splash_draw();
  splash = true;
  splash_end_ms = board_millis() + SCREEN_SPLASH_MS;
}

void screen_update(const status_t *s) {
  if (lay == NULL) {
    return;
  }
  screen_model(s, FW_VERSION, &want);
  cursor = 0;
  if (!splash && !redrawing && panel_differs()) {
    redraw_start();
  }
}

// The hourglass's next unit: the band is shown and has one, and it is to change
static bool hourglass_next(bool due, unit_t *u) {
  if (!band_valid || !band_shown.hourglass || !band_equal(&band_shown, &want.band) || due == hourglass_shown) {
    return false;
  }
  *u = (unit_t) {UNIT_HOURGLASS, 0};
  return true;
}

#ifdef DISPLAY_LOG
// The blinks drawn outside a redraw: a line every 10 s while there are any
static void hourglass_log(uint32_t cycles) {
  static uint32_t blinks, max_cycles, first_ms;
  if (blinks == 0) {
    first_ms = board_millis();
  }
  blinks++;
  max_cycles = cycles > max_cycles ? cycles : max_cycles;
  if (board_millis() - first_ms >= 10000u) {
    log_line("display hourglass blinks=%lu max_us=%lu", (unsigned long) blinks,
             (unsigned long) (max_cycles / cycles_per_us));
    blinks = max_cycles = 0;
  }
}
#endif

void screen_task(void) {
  if (lay == NULL) {
    return;
  }
  if (splash) {
    if ((int32_t) (board_millis() - splash_end_ms) < 0) {
      return;
    }
    // The status, from a cleared screen
    splash = false;
    clear_line = 0;
    band_valid = false;
    band_painted = 0;
    memset(cells, ' ', sizeof(cells));
    cursor = 0;
    redraw_start();
  }
  bool due = want.band.hourglass && hourglass_due();
  bool lit = screen_blink_lit(board_millis());
  if (lit != blink_lit) {
    // The blinking texts' turn: a redraw of their glyphs, if they have any
    blink_lit = lit;
    cursor = 0;
    if (!redrawing && panel_differs()) {
      redraw_start();
    }
  }
  unit_t u;
  if (!redrawing) {
    // Only the hourglass's blink: one unit, not a redraw
    if (hourglass_next(due, &u) && stream_backlog_us() < SCREEN_BACKLOG_US) {
      uint32_t t0 = board_cycles();
      (void) draw(&u);
#ifdef DISPLAY_LOG
      hourglass_log(board_cycles() - t0);
#else
      (void) t0;
#endif
    }
    return;
  }
  if (stream_backlog_us() >= SCREEN_BACKLOG_US) {
    return;
  }
  uint32_t isr0 = load_isr_cycles();
  uint32_t t0 = board_cycles();
  uint32_t budget = SCREEN_DRAW_US * cycles_per_us;
  bool drew = false;
  bool more;
  while ((more = clear_next(&u) || band_next(&u) || hourglass_next(due, &u) || texts_next(&u))) {
    if (drew && board_cycles() - t0 + unit_estimate(&u) > budget) {
      break;
    }
    px += draw(&u);
    drew = true;
  }
  if (drew) {
    uint32_t t = board_cycles() - t0;
    busy_cycles += t;
    slices++;
    if (t > max_slice_cycles) {
      max_slice_cycles = t;
      max_slice_isr = load_isr_cycles() - isr0;
    }
  }
  if (!more) {
    redraw_done();
  }
}

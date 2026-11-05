// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Host tool for tests/test_screen_model.py: runs app/screen_model.c and
// app/leds.c on a status given on stdin, one command per line. Not part of
// the firmware.
//
//   status [<field>=<value> ...]    -> nothing; a zeroed status with these fields
//   model <fw>                      -> "band <state> <colour> <hourglass with packets
//                                      moving> <hourglass without> <word>"
//                                      (screen_hourglass_t), "labels|<20>|",
//                                      "file|<15 characters>|" twice, "fw|<30>|",
//                                      then one "row|<30 characters>|" per row
//   leds                            -> for green, blue and red in turn:
//                                      "led <name> off|on|blink <ms on>"; then
//                                      "hourglass lit=<ms> apart_from_blue=<ms>
//                                      blink_apart_from_red=<ms>" over 2 s,
//                                      packets moving
//
// Fields: usb (0 not mounted, 1 mounted, 2 suspended), hs, rate, alt, slot,
// res, streaming, id, bits, dop, lost, ovf, allow_silence, pass, fail,
// unknown, overrun, count0-count3, results_lost, last, last_in_run,
// last_verdict, last_end, last_lost, last_ovf, last_diff, last_kind,
// last_rate, last_depth, file_kind, file_rate, file_depth, file_diff (a
// text: sets file_diff_known). Numbers in decimal.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "board.h"
#include "leds.h"
#include "log.h"
#include "screen_model.h"
#include "status.h"

status_t status;

static uint32_t now_ms;
static bool led_on[LED_COUNT];

uint32_t board_millis(void) {
  return now_ms;
}

void board_led(led_t led, bool on) {
  led_on[led] = on;
}

const char *board_led_name(led_t led) {
  static const char *const names[LED_COUNT] = {"green", "blue", "red"};
  return names[led];
}

void log_line(const char *fmt, ...) {
  (void) fmt;
}

void display_led_test(led_t led) {
  (void) led;
}

static void text_field(char *dst, size_t size, const char *v) {
  snprintf(dst, size, "%s", v);
}

static int set_field(const char *key, const char *v) {
  long n = strtol(v, NULL, 10);
  status_t *s = &status;
  if (strcmp(key, "usb") == 0) {
    s->usb = (usb_state_t) n;
  } else if (strcmp(key, "hs") == 0) {
    s->high_speed = n != 0;
  } else if (strcmp(key, "rate") == 0) {
    s->rate = (uint32_t) n;
  } else if (strcmp(key, "alt") == 0) {
    s->alt = (uint8_t) n;
  } else if (strcmp(key, "slot") == 0) {
    s->slot = (uint8_t) n;
  } else if (strcmp(key, "res") == 0) {
    s->resolution = (uint8_t) n;
  } else if (strcmp(key, "streaming") == 0) {
    s->streaming = n != 0;
  } else if (strcmp(key, "id") == 0) {
    s->stream_id = (uint32_t) n;
  } else if (strcmp(key, "bits") == 0) {
    s->bits = (uint8_t) n;
  } else if (strcmp(key, "dop") == 0) {
    s->dop = n != 0;
  } else if (strcmp(key, "lost") == 0) {
    s->lost = (uint32_t) strtoul(v, NULL, 10);
  } else if (strcmp(key, "ovf") == 0) {
    s->overflows = (uint32_t) strtoul(v, NULL, 10);
  } else if (strcmp(key, "allow_silence") == 0) {
    s->allow_silence = n != 0;
  } else if (strcmp(key, "pass") == 0) {
    s->run_pass = n != 0;
  } else if (strcmp(key, "fail") == 0) {
    s->run_fail = n != 0;
  } else if (strcmp(key, "unknown") == 0) {
    s->run_unknown = n != 0;
  } else if (strncmp(key, "count", 5) == 0 && key[5] >= '0' && key[5] <= '3' && key[6] == '\0') {
    s->run_count[key[5] - '0'] = (uint32_t) strtoul(v, NULL, 10);
  } else if (strcmp(key, "overrun") == 0) {
    s->run_overrun = n != 0;
  } else if (strcmp(key, "results_lost") == 0) {
    s->run_results_lost = (uint32_t) strtoul(v, NULL, 10);
  } else if (strcmp(key, "last") == 0) {
    s->last_known = n != 0;
  } else if (strcmp(key, "last_in_run") == 0) {
    s->last_in_run = n != 0;
  } else if (strcmp(key, "last_verdict") == 0) {
    s->last_verdict = (uint8_t) n;
  } else if (strcmp(key, "last_end") == 0) {
    s->last_end = (uint8_t) n;
  } else if (strcmp(key, "last_lost") == 0) {
    s->last_lost = (int32_t) n;
  } else if (strcmp(key, "last_ovf") == 0) {
    s->last_overflows = (uint32_t) strtoul(v, NULL, 10);
  } else if (strcmp(key, "last_diff") == 0) {
    text_field(s->last_diff, sizeof(s->last_diff), v);
  } else if (strcmp(key, "last_kind") == 0) {
    s->last_kind = (uint8_t) n;
  } else if (strcmp(key, "last_rate") == 0) {
    s->last_rate = (uint32_t) strtoul(v, NULL, 10);
  } else if (strcmp(key, "last_depth") == 0) {
    s->last_depth = (uint8_t) n;
  } else if (strcmp(key, "file_kind") == 0) {
    s->file_kind = (uint8_t) n;
  } else if (strcmp(key, "file_rate") == 0) {
    s->file_rate = (uint32_t) strtoul(v, NULL, 10);
  } else if (strcmp(key, "file_depth") == 0) {
    s->file_depth = (uint8_t) n;
  } else if (strcmp(key, "file_diff") == 0) {
    s->file_diff_known = true;
    text_field(s->file_diff, sizeof(s->file_diff), v);
  } else {
    return -1;
  }
  return 0;
}

// One LED over two seconds of leds_task() calls, one per millisecond
static void led_pattern(led_t led) {
  uint32_t on = 0;
  uint32_t edges = 0;
  uint32_t run = 0;
  uint32_t run_max = 0;
  bool prev = false;
  for (now_ms = 0; now_ms < 2000u; now_ms++) {
    leds_task();
    if (now_ms > 0 && led_on[led] != prev) {
      edges++;
      run = 0;
    }
    prev = led_on[led];
    if (led_on[led]) {
      on++;
      run++;
      run_max = run > run_max ? run : run_max;
    }
  }
  if (on == 0) {
    printf("led %s off\n", board_led_name(led));
  } else if (edges == 0) {
    printf("led %s on\n", board_led_name(led));
  } else {
    printf("led %s blink %u\n", board_led_name(led), (unsigned) run_max);
  }
}

int main(void) {
  char line[1024];
  while (fgets(line, sizeof(line), stdin) != NULL) {
    line[strcspn(line, "\r\n")] = '\0';
    char *cmd = strtok(line, " ");
    if (cmd == NULL) {
      continue;
    }
    if (strcmp(cmd, "status") == 0) {
      memset(&status, 0, sizeof(status));
      for (char *kv = strtok(NULL, " "); kv != NULL; kv = strtok(NULL, " ")) {
        char *eq = strchr(kv, '=');
        if (eq == NULL) {
          fprintf(stderr, "bad field %s\n", kv);
          return 1;
        }
        *eq = '\0';
        if (set_field(kv, eq + 1) != 0) {
          fprintf(stderr, "unknown field %s\n", kv);
          return 1;
        }
      }
    } else if (strcmp(cmd, "model") == 0) {
      const char *fw = strtok(NULL, " ");
      screen_t sc;
      screen_model(&status, fw != NULL ? fw : "", &sc);
      printf("band %u %06x %u %u %s\n", sc.band.state, (unsigned) sc.band.colour,
             (unsigned) screen_hourglass(&sc.band, true), (unsigned) screen_hourglass(&sc.band, false),
             sc.band.word);
      printf("labels|%s|\nfile|%s|\nfile|%s|\nfw|%s|\n", sc.labels, sc.file[0], sc.file[1], sc.fw);
      for (uint32_t r = 0; r < SCREEN_ROWS; r++) {
        printf("row|%s|\n", sc.rows[r]);
      }
    } else if (strcmp(cmd, "leds") == 0) {
      led_pattern(LED_GREEN);
      led_pattern(LED_BLUE);
      led_pattern(LED_RED);
      // the hourglass with packets moving against the blue LED, over 2 s
      screen_t sc;
      screen_model(&status, "", &sc);
      uint32_t lit = 0;
      uint32_t apart = 0;
      uint32_t apart_red = 0;
      for (now_ms = 0; now_ms < 2000u; now_ms++) {
        leds_task();
        bool on = screen_hourglass_lit(screen_hourglass(&sc.band, true), now_ms);
        lit += on ? 1u : 0u;
        apart += on != led_on[LED_BLUE] ? 1u : 0u;
        apart_red += screen_blink_lit(now_ms) != led_on[LED_RED] ? 1u : 0u;
      }
      printf("hourglass lit=%u apart_from_blue=%u blink_apart_from_red=%u\n", (unsigned) lit, (unsigned) apart,
             (unsigned) apart_red);
    } else {
      fprintf(stderr, "unknown command %s\n", cmd);
      return 1;
    }
    fflush(stdout);
  }
  return 0;
}

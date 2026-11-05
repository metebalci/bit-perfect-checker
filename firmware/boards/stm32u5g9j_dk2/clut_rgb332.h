// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The STM32U5G9J-DK2's colour look-up table for its L8 layer: an RGB332
// index (R[7:5] G[7:5] B[7:6]) as 0xRRGGBB, each channel expanded to 8 bits,
// so black, white and the primaries are exact.
//
// PANEL_GREEN_MAX corrects the panel's green: full green looks light green on
// the RK050HR18 next to the STM32F723E-DISCO's panel. The pure greens (red
// and blue 0) scale so that full green is PANEL_GREEN_MAX; every other entry
// keeps full green, so that white, yellow and cyan stay as they are. 208 (the
// default, as board.cmake's) was chosen by eye against the F723; 255 is no
// correction. Pure C, also built by the host tests (tests/test_dk2_clut.py).

#ifndef CLUT_RGB332_H
#define CLUT_RGB332_H

#include <stdint.h>

#ifndef PANEL_GREEN_MAX
#define PANEL_GREEN_MAX 208u
#endif

// 3 bits to 8: 7 -> 255, 0 -> 0
static inline uint32_t clut_expand3(uint32_t v) {
  return (v << 5) | (v << 2) | (v >> 1);
}

static inline uint32_t clut_rgb332(uint32_t i) {
  uint32_t r = clut_expand3(i >> 5);
  uint32_t g = clut_expand3((i >> 2) & 7u);
  uint32_t b = (i & 3u) * 0x55u;
  if (r == 0u && b == 0u) {
    g = (g * PANEL_GREEN_MAX + 127u) / 255u;
  }
  return (r << 16) | (g << 8) | b;
}

#endif

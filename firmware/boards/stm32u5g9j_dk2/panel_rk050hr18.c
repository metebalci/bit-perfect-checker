// SPDX-License-Identifier: MIT AND BSD-3-Clause
// Copyright (c) 2026 Mete Balci
// The STM32U5G9J-DK2's 5" panel (RK050HR18, 800x480, parallel RGB888 on
// the CN3 connector) driven by the LTDC from a frame buffer in SRAM6.
//
// The panel timing, the LTDC's signal polarities, the PLL3 dividers and the
// pins follow ST's code; see the notice at ST_TIMING below. Unlike ST's
// code, PLL3's input range is RANGE_0: its input is 16 MHz / 4 = 4 MHz, and
// RANGE_1 is for 8 to 16 MHz (RM0456, RCC_PLL3CFGR.PLL3RGE).
//
// The panel has no command interface. The layer is L8: one byte per pixel,
// an index into the layer's colour look-up table, which holds a fixed RGB332
// palette (index = R[7:5] G[7:5] B[7:6], clut_rgb332.h, with the panel's
// green correction); 384,000 bytes instead of 768,000 for RGB565. 0xRRGGBB colours are truncated to an index, so black, white
// and the primaries are exact. The CPU draws; the LTDC reads the frame buffer
// on its own, with no interrupt. The frame buffer is alone in SRAM6, a bank
// that no other bus master uses, so the LTDC's reads never wait for USB DMA
// in SRAM1.

#include "panel.h"

#include <string.h>

#include "board.h"
#include "clut_rgb332.h"
#include "family.h"
#include "panel_rk050hr18.h"

#define WIDTH  800u
#define HEIGHT 480u

/*
 * ST_TIMING: the panel timing in pixel clocks and lines (RK050HR18_HSYNC to
 * RK050HR18_VFP in rk050hr18.h, STMicroelectronics/stm32-rk050hr18), and,
 * further down, the polarities, PLL3's M, N and R, the pins with their
 * alternate functions and DISP before the LTDC (MX_LTDC_Init,
 * MX_LTDC_ClockConfig, LTDC_MspInit and BSP_LCD_InitEx in
 * stm32u5g9j_discovery_lcd.c, STMicroelectronics/stm32u5g9j-dk2-bsp).
 *
 * Copyright (c) 2023 STMicroelectronics.
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * 3. Neither the name of the copyright holder nor the names of its
 *    contributors may be used to endorse or promote products derived from
 *    this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */
#define HSYNC 4u
#define HBP   8u
#define HFP   8u
#define VSYNC 4u
#define VBP   8u
#define VFP   8u

// LTDC_LxPFCR.PF: L8 (RM0456, LTDC_LxPFCR; LTDC_PIXEL_FORMAT_L8 in the HAL)
#define PF_L8 5u
// LTDC_LxBFCR: both blending factors constant alpha (the HAL's
// LTDC_BLENDING_FACTOR1_PAxCA and LTDC_BLENDING_FACTOR2_PAxCA, as ST's BSP)
#define BF1_PAXCA 0x600u
#define BF2_PAXCA 0x007u

// DISP (PE4) switches the panel on, BL_CTRL (PE6) the backlight driver;
// both have pull-downs on the LCD board (MB1860), so both stay off until
// driven high.
#define DISP_PORT GPIOE
#define DISP_PIN  GPIO_PIN_4
#define BL_PORT   GPIOE
#define BL_PIN    GPIO_PIN_6

#define FRAME_MS   17u  // 820 x 500 pixel clocks at 25 MHz: 16.4 ms
#define RUN_WAIT_MS 5u  // the LTDC's line counter must move within this

// The frame buffer, one byte per pixel, read and written through fb. It is
// declared as words because fill_bytes() stores words into it: word stores
// into a uint8_t array would break C's aliasing rule (C11 6.5p7), byte
// accesses to words do not.
_Static_assert((WIDTH * HEIGHT) % 4u == 0u, "the frame buffer is whole words");
static uint32_t fb_words[WIDTH * HEIGHT / 4u] __attribute__((section(".framebuffer"), aligned(8)));
static uint8_t *const fb = (uint8_t *) fb_words;

static bool sram6_was_off;
static uint32_t fifo_underruns;
static uint32_t transfer_errors;

// The window of panel_window() and the next pixel's place in it
static uint16_t win_x;
static uint16_t win_y;
static uint16_t win_w;
static uint16_t win_h;
static uint16_t cur_x;
static uint16_t cur_y;

static uint8_t rgb332(uint32_t rgb) {
  return (uint8_t) (((rgb >> 16) & 0xE0u) | ((rgb >> 11) & 0x1Cu) | ((rgb >> 6) & 0x03u));
}

static bool clock_init(void) {
  // PLL3: 16 MHz HSE / 4 = 4 MHz, * 125 = 500 MHz VCO, / 20 = 25 MHz pixel
  // clock on PLL3R (LTDCSEL = PLL3). P and Q are not enabled.
  RCC_PeriphCLKInitTypeDef periph = {0};
  periph.PeriphClockSelection = RCC_PERIPHCLK_LTDC;
  periph.LtdcClockSelection = RCC_LTDCCLKSOURCE_PLL3;
  periph.PLL3.PLL3Source = RCC_PLLSOURCE_HSE;
  periph.PLL3.PLL3M = 4;
  periph.PLL3.PLL3N = 125;
  periph.PLL3.PLL3P = 8;
  periph.PLL3.PLL3Q = 8;
  periph.PLL3.PLL3R = 20;
  periph.PLL3.PLL3RGE = RCC_PLLVCIRANGE_0;
  periph.PLL3.PLL3FRACN = 0;
  periph.PLL3.PLL3ClockOut = RCC_PLL3_DIVR;
  return HAL_RCCEx_PeriphCLKConfig(&periph) == HAL_OK;
}

static void pins_init(void) {
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();

  // Panel and backlight off until the LTDC sends frames
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = DISP_PIN | BL_PIN;
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_WritePin(GPIOE, DISP_PIN | BL_PIN, GPIO_PIN_RESET);
  HAL_GPIO_Init(GPIOE, &gpio);

  // RGB888 data, clock, HSYNC, VSYNC and DE, as ST's LTDC_MspInit
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Speed = GPIO_SPEED_FREQ_MEDIUM;
  gpio.Alternate = GPIO_AF7_LTDC;
  gpio.Pin = GPIO_PIN_9;
  HAL_GPIO_Init(GPIOB, &gpio);
  gpio.Pin = GPIO_PIN_6 | GPIO_PIN_7;
  HAL_GPIO_Init(GPIOC, &gpio);

  gpio.Alternate = GPIO_AF8_LTDC;
  gpio.Pin = GPIO_PIN_2;
  HAL_GPIO_Init(GPIOB, &gpio);
  gpio.Pin = GPIO_PIN_8 | GPIO_PIN_9;
  HAL_GPIO_Init(GPIOC, &gpio);
  // PD3 clock, PD6 DE, PD13 VSYNC, the rest data
  gpio.Pin = GPIO_PIN_0 | GPIO_PIN_1 | GPIO_PIN_3 | GPIO_PIN_6 | GPIO_PIN_8 | GPIO_PIN_9 | GPIO_PIN_10
             | GPIO_PIN_11 | GPIO_PIN_12 | GPIO_PIN_13 | GPIO_PIN_14 | GPIO_PIN_15;
  HAL_GPIO_Init(GPIOD, &gpio);
  // PE0 HSYNC, the rest data
  gpio.Pin = GPIO_PIN_0 | GPIO_PIN_7 | GPIO_PIN_8 | GPIO_PIN_9 | GPIO_PIN_10 | GPIO_PIN_11 | GPIO_PIN_12
             | GPIO_PIN_13 | GPIO_PIN_14 | GPIO_PIN_15;
  HAL_GPIO_Init(GPIOE, &gpio);
}

// n bytes of value v from p, in fb: word stores between the unaligned ends, eight
// at a time (a one-word loop measured 3.2 ms for the whole screen, about
// 5 cycles a word)
static void fill_bytes(uint8_t *p, uint8_t v, uint32_t n) {
  while (n > 0u && ((uint32_t) p & 3u) != 0u) {
    *p++ = v;
    n--;
  }
  uint32_t word = v * 0x01010101u;
  uint32_t *w = (uint32_t *) (void *) p;
  for (uint32_t i = n / 32u; i > 0u; i--) {
    w[0] = word;
    w[1] = word;
    w[2] = word;
    w[3] = word;
    w[4] = word;
    w[5] = word;
    w[6] = word;
    w[7] = word;
    w += 8;
  }
  for (uint32_t i = (n & 31u) / 4u; i > 0u; i--) {
    *w++ = word;
  }
  p = (uint8_t *) w;
  for (n &= 3u; n > 0u; n--) {
    *p++ = v;
  }
}

static void ltdc_init(void) {
  __HAL_RCC_LTDC_CLK_ENABLE();
  __HAL_RCC_LTDC_FORCE_RESET();
  __HAL_RCC_LTDC_RELEASE_RESET();

  // HSYNC, VSYNC and DE active low, pixel clock not inverted: all
  // polarity bits 0 (ST's MX_LTDC_Init)
  LTDC->GCR = 0;
  LTDC->SSCR = ((HSYNC - 1u) << 16) | (VSYNC - 1u);
  LTDC->BPCR = ((HSYNC + HBP - 1u) << 16) | (VSYNC + VBP - 1u);
  LTDC->AWCR = ((HSYNC + HBP + WIDTH - 1u) << 16) | (VSYNC + VBP + HEIGHT - 1u);
  LTDC->TWCR = ((HSYNC + HBP + WIDTH + HFP - 1u) << 16) | (VSYNC + VBP + HEIGHT + VFP - 1u);
  LTDC->BCCR = 0;  // black behind the layer
  // The flags only, polled by panel_rk050hr18_errors(); the LTDC's
  // interrupts stay off in the NVIC.
  LTDC->IER = LTDC_IER_FUIE | LTDC_IER_TERRIE;

  // Layer 1: the whole screen, L8 (window positions as the HAL's
  // LTDC_SetConfig)
  LTDC_Layer1->CR = 0;
  LTDC_Layer1->WHPCR = ((HSYNC + HBP + WIDTH - 1u) << 16) | (HSYNC + HBP);
  LTDC_Layer1->WVPCR = ((VSYNC + VBP + HEIGHT - 1u) << 16) | (VSYNC + VBP);
  LTDC_Layer1->PFCR = PF_L8;
  LTDC_Layer1->DCCR = 0;
  LTDC_Layer1->CACR = 0xFFu;
  LTDC_Layer1->BFCR = BF1_PAXCA | BF2_PAXCA;
  LTDC_Layer1->CFBAR = (uint32_t) fb;
  LTDC_Layer1->CFBLR = (WIDTH << 16) | (WIDTH + 3u);  // pitch, line length + 3 (RM0456)
  LTDC_Layer1->CFBLNR = HEIGHT;

  // The RGB332 palette (clut_rgb332.h), written while the layer is off
  // (RM0456, LTDC_LxCLUTWR)
  for (uint32_t i = 0; i < 256u; i++) {
    LTDC_Layer1->CLUTWR = (i << 24) | clut_rgb332(i);
  }
  LTDC_Layer1->CR = LTDC_LxCR_CLUTEN | LTDC_LxCR_LEN;
  LTDC->SRCR = LTDC_SRCR_IMR;
}

// The LTDC's line counter moves: its pixel clock runs.
static bool ltdc_running(void) {
  uint32_t y0 = LTDC->CPSR & LTDC_CPSR_CYPOS;
  uint32_t t0 = board_millis();
  while (board_millis() - t0 < RUN_WAIT_MS) {
    if ((LTDC->CPSR & LTDC_CPSR_CYPOS) != y0) {
      return true;
    }
  }
  return false;
}

bool panel_init(void) {
  // SRAM6 is clocked from reset on the U5Fx/5Gx (RM0456, RCC_AHB2ENR2);
  // check before the first write to the frame buffer.
  sram6_was_off = (RCC->AHB2ENR2 & RCC_AHB2ENR2_SRAM6EN) == 0u;
  if (sram6_was_off) {
    __HAL_RCC_SRAM6_CLK_ENABLE();
  }
  fill_bytes(fb, rgb332(0x000000u), sizeof fb_words);

  pins_init();
  if (!clock_init()) {
    return false;
  }
  ltdc_init();
  LTDC->GCR |= LTDC_GCR_LTDCEN;
  if (!ltdc_running()) {
    LTDC->GCR = 0;
    return false;
  }

  // Panel on, then the backlight once it has had about two frames
  HAL_GPIO_WritePin(DISP_PORT, DISP_PIN, GPIO_PIN_SET);
  uint32_t t0 = board_millis();
  while (board_millis() - t0 < 2u * FRAME_MS + 2u) {
  }
  HAL_GPIO_WritePin(BL_PORT, BL_PIN, GPIO_PIN_SET);

  // Flags from the start-up do not count
  LTDC->ICR = LTDC_ICR_CFUIF | LTDC_ICR_CTERRIF;
  return true;
}

uint16_t panel_width(void) {
  return WIDTH;
}

uint16_t panel_height(void) {
  return HEIGHT;
}

void panel_window(uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
  // Clipped to the screen
  if (x > WIDTH) {
    x = WIDTH;
  }
  if (y > HEIGHT) {
    y = HEIGHT;
  }
  win_x = x;
  win_y = y;
  win_w = (uint16_t) (w < WIDTH - x ? w : WIDTH - x);
  win_h = (uint16_t) (h < HEIGHT - y ? h : HEIGHT - y);
  cur_x = 0;
  cur_y = 0;
}

void panel_fill(uint32_t rgb, uint32_t n) {
  uint8_t v = rgb332(rgb);
  while (n > 0u && cur_y < win_h) {
    uint32_t run = (uint32_t) (win_w - cur_x);
    if (run > n) {
      run = n;
    }
    fill_bytes(&fb[(uint32_t) (win_y + cur_y) * WIDTH + win_x + cur_x], v, run);
    n -= run;
    cur_x = (uint16_t) (cur_x + run);
    if (cur_x >= win_w) {
      cur_x = 0;
      cur_y++;
    }
  }
}

// For 4 bits of a bitmap, most significant first: 0xFF in each byte of a
// word whose pixel is set, the first pixel in the lowest-addressed byte
#define NIBBLE_MASK(n) ((((n) & 8u) != 0u ? 0x000000FFu : 0u) | (((n) & 4u) != 0u ? 0x0000FF00u : 0u) \
                        | (((n) & 2u) != 0u ? 0x00FF0000u : 0u) | (((n) & 1u) != 0u ? 0xFF000000u : 0u))
static const uint32_t nibble_mask[16] = {
  NIBBLE_MASK(0u),  NIBBLE_MASK(1u),  NIBBLE_MASK(2u),  NIBBLE_MASK(3u),
  NIBBLE_MASK(4u),  NIBBLE_MASK(5u),  NIBBLE_MASK(6u),  NIBBLE_MASK(7u),
  NIBBLE_MASK(8u),  NIBBLE_MASK(9u),  NIBBLE_MASK(10u), NIBBLE_MASK(11u),
  NIBBLE_MASK(12u), NIBBLE_MASK(13u), NIBBLE_MASK(14u), NIBBLE_MASK(15u),
};

void panel_bits(const uint8_t *bits, uint32_t n, uint32_t fg, uint32_t bg) {
  uint8_t f = rgb332(fg);
  uint8_t b = rgb332(bg);
  uint32_t i = 0;

  // Whole rows from the window's left edge, when a row is whole bytes of
  // the bitmap (every font's glyph): eight pixels per bitmap byte as two
  // word stores, which may be unaligned (a glyph can start at any x). A
  // 32x64 glyph takes 43 us this way, 438 us a pixel at a time.
  if (cur_x == 0u && (win_w & 7u) == 0u) {
    uint32_t bw = b * 0x01010101u;
    uint32_t diff = (f * 0x01010101u) ^ bw;
    while (n - i >= win_w && cur_y < win_h) {
      uint8_t *p = &fb[(uint32_t) (win_y + cur_y) * WIDTH + win_x];
      const uint8_t *src = &bits[i >> 3];
      for (uint32_t k = 0; k < win_w / 8u; k++, p += 8) {
        uint32_t w0 = bw ^ (diff & nibble_mask[src[k] >> 4]);
        uint32_t w1 = bw ^ (diff & nibble_mask[src[k] & 15u]);
        memcpy(p, &w0, 4);
        memcpy(p + 4, &w1, 4);
      }
      i += win_w;
      cur_y++;
    }
  }

  // Anything else a pixel at a time
  for (; i < n && cur_y < win_h; i++) {
    uint8_t *p = &fb[(uint32_t) (win_y + cur_y) * WIDTH + win_x + cur_x];
    *p = (bits[i >> 3] & (0x80u >> (i & 7u))) != 0u ? f : b;
    if (++cur_x >= win_w) {
      cur_x = 0;
      cur_y++;
    }
  }
}

uint32_t panel_rk050hr18_fb(void) {
  return (uint32_t) fb;
}

bool panel_rk050hr18_sram6_was_off(void) {
  return sram6_was_off;
}

void panel_rk050hr18_errors(uint32_t *underruns, uint32_t *transfers) {
  uint32_t isr = LTDC->ISR;
  if ((isr & LTDC_ISR_FUIF) != 0u) {
    fifo_underruns++;
  }
  if ((isr & LTDC_ISR_TERRIF) != 0u) {
    transfer_errors++;
  }
  LTDC->ICR = ((isr & LTDC_ISR_FUIF) != 0u ? LTDC_ICR_CFUIF : 0u) | ((isr & LTDC_ISR_TERRIF) != 0u ? LTDC_ICR_CTERRIF : 0u);
  *underruns = fifo_underruns;
  *transfers = transfer_errors;
}

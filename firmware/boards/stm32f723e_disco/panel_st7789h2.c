// SPDX-License-Identifier: MIT AND BSD-3-Clause
// Copyright (c) 2026 Mete Balci
// The STM32F723E-DISCO's 1.54" 240x240 panel (FRD154BP2902) with an
// ST7789H2 controller that holds the frame: a 16-bit 8080 bus on the FMC,
// bank 1 sub-bank 2 (NE2 on PG9, 0x64000000), RS on FMC_A0 (PF0), so
// commands go to 0x64000000 and data to 0x64000002. Reset on PH7 (100 k
// pull-up), backlight enable on PH11 (100 k pull-down: off until driven),
// TE on PC8 unused. The bus pins D0-D15, NOE and NWE are shared with the
// PSRAM, whose chip selects stay inactive through their pull-ups as long as
// PD7 (NE1) is left alone.
//
// Bus timing and pins follow ST's 32F723E-Discovery BSP, with two changes:
// the FMC write FIFO stays enabled (ES0360 2.3.4: reads may be corrupted with
// it disabled), and reads use a slow timing of their own (extended mode A),
// since the panel's read strobe is longer than its write strobe. The
// controller's set-up follows ST's st7789h2 driver v1.1.5; see the notice at
// init_sequence below.
//
// The picture is turned by 180 degrees against ST's default, so that it
// stands upright as the board's silkscreen reads, the way ST's st7789h2
// driver v2.0.4 does it for ST7789H2_ORIENTATION_PORTRAIT_ROT180: MADCTL
// with MY and MX set, and every row address 80 higher, since the
// controller's RAM has 320 rows and with MY set the panel's 240 rows show
// rows 80 to 319.

#include "panel.h"

#include "family.h"
#include "panel_st7789h2.h"

#define PANEL_WIDTH   240u
#define PANEL_HEIGHT  240u
#define PANEL_ID1     0x85u  // RDDID's first byte (ST's driver: ST7789H2_ID)
#define MADCTL_ROT180 0xC0u  // MY and MX
#define ROW_OFFSET    80u    // 320 RAM rows less the 240 shown

// The MPU maps these 32 bytes as Device memory: every access goes to the bus
// once and in order, without merging or speculative reads.
#define PANEL_BASE 0x64000000u
#define PANEL_REG  (*(volatile uint16_t *) PANEL_BASE)           // RS low: command
#define PANEL_DATA (*(volatile uint16_t *) (PANEL_BASE + 2u))    // RS high: data

// FMC bank 1 sub-bank 2 (NE2): BCR2 is BTCR[2], BTR2 BTCR[3], BWTR2 BWTR[2]
#define FMC_NE2 2u

// ST7789H2 commands
#define CMD_SWRESET 0x01u
#define CMD_RDDID   0x04u
#define CMD_SLPIN   0x10u
#define CMD_SLPOUT  0x11u
#define CMD_INVON   0x21u
#define CMD_DISPON  0x29u
#define CMD_CASET   0x2Au
#define CMD_RASET   0x2Bu
#define CMD_RAMWR   0x2Cu
#define CMD_TEON    0x35u
#define CMD_MADCTL  0x36u
#define CMD_COLMOD  0x3Au

static uint8_t id1;

static void pins_init(void) {
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_GPIOF_CLK_ENABLE();
  __HAL_RCC_GPIOG_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();

  // Reset (PH7) asserted, backlight (PH11) off
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = GPIO_PIN_7 | GPIO_PIN_11;
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_WritePin(GPIOH, GPIO_PIN_7 | GPIO_PIN_11, GPIO_PIN_RESET);
  HAL_GPIO_Init(GPIOH, &gpio);

  // FMC: D2 D3 NOE NWE D13 D14 D15 D0 D1 on port D, D4-D12 on port E, A0 on
  // PF0, NE2 on PG9; pull-ups and speed as in ST's BSP
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Pull = GPIO_PULLUP;
  gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  gpio.Alternate = GPIO_AF12_FMC;
  gpio.Pin = GPIO_PIN_0 | GPIO_PIN_1 | GPIO_PIN_4 | GPIO_PIN_5 | GPIO_PIN_8 | GPIO_PIN_9 | GPIO_PIN_10 |
             GPIO_PIN_14 | GPIO_PIN_15;
  HAL_GPIO_Init(GPIOD, &gpio);
  gpio.Pin = GPIO_PIN_7 | GPIO_PIN_8 | GPIO_PIN_9 | GPIO_PIN_10 | GPIO_PIN_11 | GPIO_PIN_12 | GPIO_PIN_13 |
             GPIO_PIN_14 | GPIO_PIN_15;
  HAL_GPIO_Init(GPIOE, &gpio);
  gpio.Pin = GPIO_PIN_0;
  HAL_GPIO_Init(GPIOF, &gpio);
  gpio.Pin = GPIO_PIN_9;
  HAL_GPIO_Init(GPIOG, &gpio);
}

static void fmc_init(void) {
  __HAL_RCC_FMC_CLK_ENABLE();

  // Sub-bank 1 (the PSRAM, NE1) resets enabled: disable it. WFDIS lives in
  // BCR1 only and stays 0, so the write FIFO serves every sub-bank.
  FMC_Bank1->BTCR[0] &= ~(FMC_BCR1_MBKEN | FMC_BCR1_WFDIS);

  // Asynchronous SRAM, mode A, at 216 MHz HCLK (4.63 ns). Writes: ADDSET 9,
  // DATAST 6 (7 cycles with NWE low), BUSTURN 1: 17 HCLK, 78.7 ns a pixel.
  // Reads: ADDSET 15, DATAST 40 (185 ns with NOE low), BUSTURN 1. CLKDIV,
  // DATLAT and ADDHLD do not apply to mode A; they get the values ST's HAL
  // would write.
  const uint32_t common = (1u << FMC_BTR1_ADDHLD_Pos) | (1u << FMC_BTR1_BUSTURN_Pos) |
                          (1u << FMC_BTR1_CLKDIV_Pos) | (0u << FMC_BTR1_DATLAT_Pos);
  FMC_Bank1->BTCR[FMC_NE2 + 1] = common | (15u << FMC_BTR1_ADDSET_Pos) | (40u << FMC_BTR1_DATAST_Pos);
  FMC_Bank1E->BWTR[FMC_NE2] = common | (9u << FMC_BWTR1_ADDSET_Pos) | (6u << FMC_BWTR1_DATAST_Pos);

  // 16 bits wide, writes allowed, extended mode for the separate read
  // timing, enabled. MODIFY_REG keeps the reserved bit 7 at its reset value.
  MODIFY_REG(FMC_Bank1->BTCR[FMC_NE2],
             FMC_BCR1_MBKEN | FMC_BCR1_MUXEN | FMC_BCR1_MTYP | FMC_BCR1_MWID | FMC_BCR1_FACCEN |
                 FMC_BCR1_BURSTEN | FMC_BCR1_WAITPOL | FMC_BCR1_WAITCFG | FMC_BCR1_WREN | FMC_BCR1_WAITEN |
                 FMC_BCR1_EXTMOD | FMC_BCR1_ASYNCWAIT | FMC_BCR1_CPSIZE | FMC_BCR1_CBURSTRW,
             FMC_BCR1_MBKEN | FMC_BCR1_MWID_0 | FMC_BCR1_WREN | FMC_BCR1_EXTMOD);
}

// Above family.c's background region (number 0, which forbids all access
// from 0x60000000 to 0xDFFFFFFF, the FMC's control registers included): the
// panel's 32 bytes and the FMC's 4 KB of registers (FMC_R_BASE) as shared
// Device memory, no execution. ST's BSP example for this board opens the
// FMC registers the same way (8 KB at 0xA0000000, its region 4).
static void mpu_region(uint8_t number, uint32_t base, uint8_t size) {
  MPU_Region_InitTypeDef mpu = {0};
  mpu.Enable = MPU_REGION_ENABLE;
  mpu.Number = number;
  mpu.BaseAddress = base;
  mpu.Size = size;
  mpu.SubRegionDisable = 0x00;
  mpu.TypeExtField = MPU_TEX_LEVEL0;
  mpu.AccessPermission = MPU_REGION_FULL_ACCESS;
  mpu.DisableExec = MPU_INSTRUCTION_ACCESS_DISABLE;
  mpu.IsShareable = MPU_ACCESS_SHAREABLE;
  mpu.IsCacheable = MPU_ACCESS_NOT_CACHEABLE;
  mpu.IsBufferable = MPU_ACCESS_BUFFERABLE;
  HAL_MPU_ConfigRegion(&mpu);
}

static void mpu_init(void) {
  mpu_region(MPU_REGION_NUMBER1, PANEL_BASE, MPU_REGION_SIZE_32B);
  mpu_region(MPU_REGION_NUMBER3, FMC_R_BASE, MPU_REGION_SIZE_4KB);
  __DSB();
  __ISB();
}

static void command(uint8_t cmd, const uint8_t *params, uint32_t n) {
  PANEL_REG = cmd;
  for (uint32_t i = 0; i < n; i++) {
    PANEL_DATA = params[i];
  }
}

/*
 * The controller's set-up: ST's ST7789H2_Init() from the st7789h2 component
 * driver v1.1.5 (STMicroelectronics/stm32-st7789h2), as a table.
 *
 * Copyright (c) 2016 STMicroelectronics.
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
typedef struct {
  uint8_t cmd;
  uint8_t n;         // parameters
  uint8_t delay_ms;  // after the command
  uint8_t params[14];
} init_step_t;

static const init_step_t init_sequence[] = {
    {CMD_SLPIN, 0, 10, {0}},
    {CMD_SWRESET, 0, 200, {0}},
    {CMD_SLPOUT, 0, 120, {0}},
    {CMD_MADCTL, 1, 0, {0x00}},  // memory access: the default orientation
    {CMD_COLMOD, 1, 0, {0x05}},  // 16 bits a pixel, RGB 5-6-5
    {CMD_INVON, 0, 0, {0}},
    {CMD_CASET, 4, 0, {0x00, 0x00, 0x00, 0xEF}},
    {CMD_RASET, 4, 0, {0x00, 0x00, 0x00, 0xEF}},
    {0xB2, 5, 0, {0x0C, 0x0C, 0x00, 0x33, 0x33}},  // porch control
    {0xB7, 1, 0, {0x35}},                          // gate control
    {0xBB, 1, 0, {0x1F}},                          // VCOM
    {0xC0, 1, 0, {0x2C}},                          // LCM control
    {0xC2, 2, 0, {0x01, 0xC3}},                    // VDV and VRH command enable
    {0xC4, 1, 0, {0x20}},                          // VDV
    {0xC6, 1, 0, {0x0F}},                          // frame rate in normal mode
    {0xD0, 2, 0, {0xA4, 0xA1}},                    // power control
    {0xE0, 14, 0, {0xD0, 0x08, 0x11, 0x08, 0x0C, 0x15, 0x39, 0x33, 0x50, 0x36, 0x13, 0x14, 0x29, 0x2D}},
    {0xE1, 14, 0, {0xD0, 0x08, 0x10, 0x08, 0x06, 0x06, 0x39, 0x44, 0x51, 0x0B, 0x16, 0x14, 0x2F, 0x31}},
    {CMD_DISPON, 0, 0, {0}},
    {CMD_SLPOUT, 0, 0, {0}},
    {CMD_TEON, 1, 0, {0x00}},  // tearing effect line on, V-blank only
};

static uint16_t rgb565(uint32_t rgb) {
  return (uint16_t) (((rgb >> 8) & 0xF800u) | ((rgb >> 5) & 0x07E0u) | ((rgb >> 3) & 0x001Fu));
}

bool panel_init(void) {
  pins_init();
  mpu_init();
  fmc_init();

  // Hardware reset as in ST's BSP (BSP_LCD_InitEx): low 5 ms, high 10 ms,
  // low 20 ms, high 10 ms
  HAL_Delay(5);
  HAL_GPIO_WritePin(GPIOH, GPIO_PIN_7, GPIO_PIN_SET);
  HAL_Delay(10);
  HAL_GPIO_WritePin(GPIOH, GPIO_PIN_7, GPIO_PIN_RESET);
  HAL_Delay(20);
  HAL_GPIO_WritePin(GPIOH, GPIO_PIN_7, GPIO_PIN_SET);
  HAL_Delay(10);

  // RDDID: a dummy read, then ID1 on D7-D0
  PANEL_REG = CMD_RDDID;
  (void) PANEL_DATA;
  id1 = (uint8_t) PANEL_DATA;
  if (id1 != PANEL_ID1) {
    return false;
  }

  for (uint32_t i = 0; i < sizeof init_sequence / sizeof init_sequence[0]; i++) {
    const init_step_t *step = &init_sequence[i];
    command(step->cmd, step->params, step->n);
    if (step->delay_ms != 0) {
      __DSB();  // the command is on the bus before the wait starts
      HAL_Delay(step->delay_ms);
    }
  }

  // Turned by 180 degrees (see the top of this file)
  const uint8_t madctl = MADCTL_ROT180;
  command(CMD_MADCTL, &madctl, 1);

  panel_window(0, 0, PANEL_WIDTH, PANEL_HEIGHT);
  panel_fill(0x000000, PANEL_WIDTH * PANEL_HEIGHT);

  // The backlight only once the frame is black; the DSB keeps the GPIO write
  // behind the last pixel's bus write
  __DSB();
  HAL_GPIO_WritePin(GPIOH, GPIO_PIN_11, GPIO_PIN_SET);
  return true;
}

uint8_t st7789h2_id(void) {
  return id1;
}

uint16_t panel_width(void) {
  return PANEL_WIDTH;
}

uint16_t panel_height(void) {
  return PANEL_HEIGHT;
}

void panel_window(uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
  uint16_t x1 = (uint16_t) (x + w - 1u);
  y = (uint16_t) (y + ROW_OFFSET);
  uint16_t y1 = (uint16_t) (y + h - 1u);
  const uint8_t cols[4] = {(uint8_t) (x >> 8), (uint8_t) x, (uint8_t) (x1 >> 8), (uint8_t) x1};
  const uint8_t rows[4] = {(uint8_t) (y >> 8), (uint8_t) y, (uint8_t) (y1 >> 8), (uint8_t) y1};
  command(CMD_CASET, cols, 4);
  command(CMD_RASET, rows, 4);
  command(CMD_RAMWR, NULL, 0);
}

void panel_fill(uint32_t rgb, uint32_t n) {
  uint16_t c = rgb565(rgb);
  for (uint32_t i = 0; i < n; i++) {
    PANEL_DATA = c;
  }
}

void panel_bits(const uint8_t *bits, uint32_t n, uint32_t fg, uint32_t bg) {
  uint16_t f = rgb565(fg);
  uint16_t b = rgb565(bg);
  for (uint32_t i = 0; i < n; i++) {
    PANEL_DATA = (bits[i >> 3] & (0x80u >> (i & 7u))) != 0 ? f : b;
  }
}

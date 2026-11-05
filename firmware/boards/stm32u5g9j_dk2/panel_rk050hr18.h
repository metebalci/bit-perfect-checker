// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The STM32U5G9J-DK2's 5" 800x480 panel (RK050HR18) on the LTDC: what the
// display code needs beyond panel.h.

#ifndef PANEL_RK050HR18_H
#define PANEL_RK050HR18_H

#include <stdbool.h>
#include <stdint.h>

// The frame buffer's address (in SRAM6), for the log.
uint32_t panel_rk050hr18_fb(void);

// SRAM6's clock was off when panel_init() looked (RCC_AHB2ENR2.SRAM6EN), so
// that panel_init() turned it on before it wrote the frame buffer.
bool panel_rk050hr18_sram6_was_off(void);

// The counts of the LTDC's FIFO underrun and transfer error flags (LTDC_ISR
// FUIF and TERRIF) since panel_init(). Each call reads and clears the flags
// and adds one for each flag that was set, so several events between two
// calls count once.
void panel_rk050hr18_errors(uint32_t *underruns, uint32_t *transfers);

#endif

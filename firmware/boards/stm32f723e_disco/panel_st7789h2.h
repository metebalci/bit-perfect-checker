// The ST7789H2 panel driver's own addition to panel.h, for this board's
// display code.

#ifndef PANEL_ST7789H2_H
#define PANEL_ST7789H2_H

#include <stdint.h>

// The first ID byte panel_init() read (RDDID); 0x85 for an ST7789H2.
uint8_t st7789h2_id(void);

#endif

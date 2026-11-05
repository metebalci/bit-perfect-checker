// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The STM32U5G9J-DK2's capacitive touch panel: a Goodix GT911 on the LCD
// board, on I2C2 (SCL PF1, SDA PF0, 1.5 k pull-ups on the board), address
// 0x5D, INT on PE5 (220 k pull-down on the LCD board), reset on NRST
// (UM3223, Table 10; MB1860). INT is low while NRST is, so the controller
// takes address 0x5D at every reset of the MCU.
//
// INT stays a floating input: a pull on it would change the address at the
// next reset (GT911 Programming Guide, 1 and 4.2). The controller's
// configuration is never written (it needs a checksum and Config_Fresh,
// 4.4, and comes from the panel's maker); touch_init() only reads it.
//
// The INT line's mode is the configuration's (0x804D bits 1:0): in the edge
// modes INT pulses for each report until the host clears the report's flag
// in 0x814E, in the level modes it stays active while a finger is down
// (GT911 guide, 5). touch_task() reads 0x814E when INT is away from its idle
// level, at most every READ_MS: bit 7 set means a new report, bits 3:0 the
// number of touches in it; writing 0 acknowledges it, as ST's gt911 driver
// does (gt911_reg.c, gt911_td_status).
//
// The idle level is INT's level at touch_init(), not the one the mode
// implies: this board's GT911 reports mode 1 (falling edge), yet INT read
// low at start-up, held by the LCD board's pull-down. If a finger was on the panel at that moment and the
// level was a report's, a read that finds no new report (bit 7 clear) takes
// INT's level at that read as the idle level: once the finger has left and
// its last report is acknowledged, INT rests at its true idle level, and the
// next read corrects it.

#include "touch.h"

#include "board.h"
#include "family.h"
#include "log.h"

#define TOUCH_ADDR 0xBAu  // 0x5D, as it goes into I2C_CR2.SADD for 7-bit addressing

#define REG_PRODUCT_ID   0x8140u  // 4 bytes, ASCII "911" and a 0
#define REG_CONFIG_VER   0x8047u
#define REG_MODULE_SW1   0x804Du  // bits 1:0 INT mode
#define REG_REFRESH_RATE 0x8056u  // bits 3:0: reports every 5 + n ms
#define REG_STATUS       0x814Eu  // bit 7 a new report, bits 3:0 touches

#define STATUS_READY   0x80u
#define STATUS_TOUCHES 0x0Fu

#define START_MS   200u   // since reset, before the first transfer
#define TIMEOUT_MS 2u     // one byte at 400 kHz takes 25 us
#define READ_MS    10u    // at most one status read this often
#define DOWN_MS    1000u  // a finger counts as down this long after its last report

// 400 kHz from the 160 MHz PCLK1 (I2C2's clock after reset): the search in
// ST's I2C_GetTiming() (stm32u5g9j_discovery_bus.c, Fast-mode limits,
// analog filter on) gives PRESC 12, SCLDEL 4, SDADEL 1, SCLH 9, SCLL 15 for
// a 160 MHz clock: SCL low 1.36 us, high 0.87 us, about 390 kHz with the
// rise and fall times. ES0595 2.14.1 asks for an I2C clock of at least
// 10 MHz in Fast mode.
#define I2C_TIMING 0xC041090Fu

static bool found;
static bool int_idle;       // INT's level between reports (see above)
static bool down;
static uint32_t last_read_ms;
static uint32_t last_down_ms;
static uint32_t reads;

static void i2c_init(void) {
  __HAL_RCC_GPIOF_CLK_ENABLE();
  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = GPIO_PIN_0 | GPIO_PIN_1;
  gpio.Mode = GPIO_MODE_AF_OD;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_MEDIUM;
  gpio.Alternate = GPIO_AF4_I2C2;
  HAL_GPIO_Init(GPIOF, &gpio);

  __HAL_RCC_GPIOE_CLK_ENABLE();
  gpio.Pin = GPIO_PIN_5;
  gpio.Mode = GPIO_MODE_INPUT;
  HAL_GPIO_Init(GPIOE, &gpio);

  __HAL_RCC_I2C2_CLK_ENABLE();
  __HAL_RCC_I2C2_FORCE_RESET();
  __HAL_RCC_I2C2_RELEASE_RESET();
  I2C2->CR1 = 0;  // TIMINGR only with PE = 0
  I2C2->TIMINGR = I2C_TIMING;
  I2C2->CR1 = I2C_CR1_PE;
}

// Waits for one of the ISR flags in want. false on a NACK, a lost
// arbitration or the time-out. A bus error in master mode may be spurious and
// does not disturb the transfer: it is only cleared (ES0595 2.14.2).
static bool wait_for(uint32_t want) {
  uint32_t t0 = board_millis();
  while (1) {
    uint32_t isr = I2C2->ISR;
    if ((isr & I2C_ISR_BERR) != 0) {
      I2C2->ICR = I2C_ICR_BERRCF;
    }
    if ((isr & want) != 0) {
      return true;
    }
    if ((isr & (I2C_ISR_NACKF | I2C_ISR_ARLO)) != 0 || board_millis() - t0 > TIMEOUT_MS) {
      return false;
    }
  }
}

// After a transfer: the STOP that AUTOEND or a NACK sends, then clear flags.
// A transfer that hangs resets the peripheral: PE = 0, checked, then PE = 1
// (RM0456, I2C software reset).
static bool finish(bool ok) {
  uint32_t t0 = board_millis();
  while ((I2C2->ISR & I2C_ISR_STOPF) == 0) {
    if (board_millis() - t0 > TIMEOUT_MS) {
      ok = false;
      I2C2->CR1 = 0;
      while ((I2C2->CR1 & I2C_CR1_PE) != 0) {
      }
      I2C2->CR1 = I2C_CR1_PE;
      break;
    }
  }
  I2C2->ICR = I2C_ICR_STOPCF | I2C_ICR_NACKCF | I2C_ICR_BERRCF | I2C_ICR_ARLOCF;
  return ok;
}

// The GT911's registers have 16-bit addresses, high byte first.
static bool send_reg(uint16_t reg, uint32_t nbytes, uint32_t end) {
  I2C2->CR2 = TOUCH_ADDR | (nbytes << I2C_CR2_NBYTES_Pos) | end | I2C_CR2_START;
  bool ok = wait_for(I2C_ISR_TXIS);
  if (ok) {
    I2C2->TXDR = (uint8_t) (reg >> 8);
    ok = wait_for(I2C_ISR_TXIS);
  }
  if (ok) {
    I2C2->TXDR = (uint8_t) reg;
  }
  return ok;
}

static bool reg_read(uint16_t reg, uint8_t *data, uint32_t n) {
  bool ok = send_reg(reg, 2u, 0u) && wait_for(I2C_ISR_TC);
  if (!ok) {
    // A NACK ends the transfer with a STOP; a time-out resets the peripheral
    return finish(false);
  }
  I2C2->CR2 = TOUCH_ADDR | I2C_CR2_RD_WRN | (n << I2C_CR2_NBYTES_Pos) | I2C_CR2_AUTOEND | I2C_CR2_START;
  for (uint32_t i = 0; i < n && ok; i++) {
    ok = wait_for(I2C_ISR_RXNE);
    if (ok) {
      data[i] = (uint8_t) I2C2->RXDR;
    }
  }
  return finish(ok);
}

static bool reg_write(uint16_t reg, uint8_t value) {
  bool ok = send_reg(reg, 3u, I2C_CR2_AUTOEND) && wait_for(I2C_ISR_TXIS);
  if (ok) {
    I2C2->TXDR = value;
  }
  return finish(ok);
}

static bool int_level(void) {
  return HAL_GPIO_ReadPin(GPIOE, GPIO_PIN_5) == GPIO_PIN_SET;
}

void touch_init(void) {
  while (board_millis() < START_MS) {
  }
  i2c_init();

  uint8_t id[5] = {0};
  uint8_t cfg = 0;
  uint8_t sw1 = 0;
  uint8_t rate = 0;
  bool ok = reg_read(REG_PRODUCT_ID, id, 4u) && reg_read(REG_CONFIG_VER, &cfg, 1u)
            && reg_read(REG_MODULE_SW1, &sw1, 1u) && reg_read(REG_REFRESH_RATE, &rate, 1u);
  for (int i = 0; i < 4; i++) {
    if (id[i] < 0x20u || id[i] > 0x7Eu) {
      id[i] = 0;  // the ID is ASCII up to a 0; keep the log line printable
    }
  }
  if (!ok) {
    log_line("touch none");
    return;
  }
  int_idle = int_level();
  found = true;
  log_line("touch gt911 id=%s cfg=0x%02x sw1=0x%02x rate=%u int=%u", (const char *) id, cfg, sw1,
           (unsigned) (rate & 0x0Fu), (unsigned) int_level());
}

void touch_task(void) {
  if (!found) {
    return;
  }
  uint32_t now = board_millis();
  if (down && now - last_down_ms >= DOWN_MS) {
    down = false;  // the safety net: no report for a second
  }
  if (int_level() == int_idle || now - last_read_ms < READ_MS) {
    return;
  }
  last_read_ms = now;
  reads++;
  uint8_t status = 0;
  bool level = int_level();
  if (!reg_read(REG_STATUS, &status, 1u)) {
    return;
  }
  if ((status & STATUS_READY) == 0u) {
    int_idle = level;  // no report pending: INT is at rest
    return;
  }
  down = (status & STATUS_TOUCHES) != 0u;
  if (down) {
    last_down_ms = now;
  }
  (void) reg_write(REG_STATUS, 0);
}

bool touch_down(void) {
  return down;
}

uint32_t touch_reads(void) {
  return reads;
}

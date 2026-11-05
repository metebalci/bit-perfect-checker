// The STM32F723E-DISCO's capacitive touch panel: a FocalTech FT6x36 or
// FT3x67 on I2C3 (SCL PA8, SDA PH8, 2.7 k pull-ups on the board, 400 kHz at
// most), address 0x70 (0x38 shifted), INT on PI9 (active low), reset on PH9
// (held low by 100 k pull-downs until driven high). The detection and the
// registers follow ST's 32F723E-Discovery BSP and its ft6x06 and ft3x67
// component drivers.
//
// The controller is set to "interrupt polling" mode (G_MODE 0x00), in which
// INT stays low as long as a finger is on the panel and goes high when it
// leaves (FocalTech's application note for the FT6x06, 1.2), so reading INT
// is all board_button() needs; no I2C after touch_init().

#include "touch.h"

#include "board.h"
#include "family.h"
#include "log.h"

#define TOUCH_ADDR     0x70u  // as it goes into I2C_CR2.SADD for 7-bit addressing
#define REG_G_MODE     0xA4u  // interrupt mode
#define REG_CHIP_ID    0xA8u
#define REG_GESTURE_EN 0xD0u  // FT3x67 only
#define G_MODE_POLLING 0x00u  // INT low while touched
#define ID_FT6X36      0xCDu
#define ID_FT3X67      0x11u

#define RESET_MS   10u   // reset low; at least 5 ms (FT6x06 datasheet, Trst)
#define START_MS   300u  // reset released to first report (FT6x06 datasheet, Trsi)
#define TIMEOUT_MS 5u    // one byte at 100 kHz takes 0.1 ms

// 100 kHz from the 54 MHz PCLK1 (I2C3's clock after reset): ST's BSP value
// (DISCOVERY_I2Cx_TIMING). ES0360 2.9.1 asks for an I2C clock of at least 4 MHz
// in Standard mode.
#define I2C_TIMING 0x40912732u

static uint32_t released_ms;
static bool found;

void touch_reset_release(void) {
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOI_CLK_ENABLE();

  GPIO_InitTypeDef gpio = {0};
  gpio.Pin = GPIO_PIN_9;
  gpio.Mode = GPIO_MODE_OUTPUT_PP;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  // A reset of its own: the MCU's reset leaves PH9 floating only for the
  // few ms until here, shorter than the 5 ms the controller needs (FT6x06
  // datasheet, Trst). Without it the first transfer after a flash failed
  // (arbitration lost, SDA held low).
  HAL_GPIO_WritePin(GPIOH, GPIO_PIN_9, GPIO_PIN_RESET);
  HAL_GPIO_Init(GPIOH, &gpio);
  HAL_Delay(RESET_MS);
  HAL_GPIO_WritePin(GPIOH, GPIO_PIN_9, GPIO_PIN_SET);
  released_ms = board_millis();

  // INT: the pull-up as in ST's BSP
  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(GPIOI, &gpio);
}

static void i2c_init(void) {
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  GPIO_InitTypeDef gpio = {0};
  gpio.Mode = GPIO_MODE_AF_OD;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_HIGH;
  gpio.Alternate = GPIO_AF4_I2C3;
  gpio.Pin = GPIO_PIN_8;
  HAL_GPIO_Init(GPIOA, &gpio);
  HAL_GPIO_Init(GPIOH, &gpio);

  __HAL_RCC_I2C3_CLK_ENABLE();
  __HAL_RCC_I2C3_FORCE_RESET();
  __HAL_RCC_I2C3_RELEASE_RESET();
  I2C3->CR1 = 0;  // TIMINGR only with PE = 0
  I2C3->TIMINGR = I2C_TIMING;
  I2C3->CR1 = I2C_CR1_PE;
}

// Waits for one of the ISR flags in want. false on a NACK, a lost
// arbitration or the time-out. A bus error in master mode may be spurious and
// does not disturb the transfer: it is only cleared (ES0360 2.9.2).
static bool wait_for(uint32_t want) {
  uint32_t t0 = board_millis();
  while (1) {
    uint32_t isr = I2C3->ISR;
    if ((isr & I2C_ISR_BERR) != 0) {
      I2C3->ICR = I2C_ICR_BERRCF;
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
// A transfer that hangs resets the peripheral: write PE = 0, check it, write
// PE = 1, which keeps PE low for the three APB cycles RM0431 26.4.6 asks for.
static bool finish(bool ok) {
  uint32_t t0 = board_millis();
  while ((I2C3->ISR & I2C_ISR_STOPF) == 0) {
    if (board_millis() - t0 > TIMEOUT_MS) {
      ok = false;
      I2C3->CR1 = 0;
      while ((I2C3->CR1 & I2C_CR1_PE) != 0) {
      }
      I2C3->CR1 = I2C_CR1_PE;
      break;
    }
  }
  I2C3->ICR = I2C_ICR_STOPCF | I2C_ICR_NACKCF | I2C_ICR_BERRCF | I2C_ICR_ARLOCF;
  return ok;
}

static bool reg_write(uint8_t reg, uint8_t value) {
  I2C3->CR2 = TOUCH_ADDR | (2u << I2C_CR2_NBYTES_Pos) | I2C_CR2_AUTOEND | I2C_CR2_START;
  bool ok = wait_for(I2C_ISR_TXIS);
  if (ok) {
    I2C3->TXDR = reg;
    ok = wait_for(I2C_ISR_TXIS);
  }
  if (ok) {
    I2C3->TXDR = value;
  }
  return finish(ok);
}

static bool reg_read(uint8_t reg, uint8_t *value) {
  I2C3->CR2 = TOUCH_ADDR | (1u << I2C_CR2_NBYTES_Pos) | I2C_CR2_START;
  bool ok = wait_for(I2C_ISR_TXIS);
  if (ok) {
    I2C3->TXDR = reg;
    ok = wait_for(I2C_ISR_TC);
  }
  if (!ok) {
    // A NACK ends the transfer with a STOP; a time-out resets the peripheral
    return finish(false);
  }
  I2C3->CR2 = TOUCH_ADDR | I2C_CR2_RD_WRN | (1u << I2C_CR2_NBYTES_Pos) | I2C_CR2_AUTOEND | I2C_CR2_START;
  ok = wait_for(I2C_ISR_RXNE);
  if (ok) {
    *value = (uint8_t) I2C3->RXDR;
  }
  return finish(ok);
}

void touch_init(void) {
  while (board_millis() - released_ms < START_MS) {
  }
  i2c_init();

  uint8_t id = 0;
  uint8_t mode = 0xFF;
  const char *name = NULL;
  if (reg_read(REG_CHIP_ID, &id)) {
    if (id == ID_FT6X36) {
      name = "ft6x36";
    } else if (id == ID_FT3X67) {
      name = "ft3x67";
    }
  }
  bool ok = name != NULL;
  if (ok && id == ID_FT3X67) {
    ok = reg_write(REG_GESTURE_EN, 0x00);  // as ST's ft3x67 driver: no gestures
  }
  ok = ok && reg_write(REG_G_MODE, G_MODE_POLLING) && reg_read(REG_G_MODE, &mode) && mode == G_MODE_POLLING;

  if (ok) {
    found = true;
    log_line("touch %s id=0x%02x g_mode=0x%02x", name, id, mode);
  } else {
    log_line("touch none id=0x%02x g_mode=0x%02x", id, mode);
  }
}

bool touch_down(void) {
  return found && HAL_GPIO_ReadPin(GPIOI, GPIO_PIN_9) == GPIO_PIN_RESET;
}

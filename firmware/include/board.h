// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// What the firmware needs from a board. Every board implements it, in
// boards/<board>/board.c together with its family's families/<family>/.

#ifndef BOARD_H
#define BOARD_H

#include <stdbool.h>
#include <stdint.h>

// The LEDs by the colour their meaning is defined by: green bit-perfect,
// blue waiting or streaming, red lost or altered. A board without one of
// them ignores it.
typedef enum {
  LED_GREEN,
  LED_BLUE,
  LED_RED,
  LED_COUNT
} led_t;

// Clocks, power, pins, the tick, the log UART and the USB device controller
// with its PHY, ready for tusb_init().
void board_init(void);

void board_led(led_t led, bool on);

// For the log, e.g. "green PC7"; NULL if the board has no such LED.
const char *board_led_name(led_t led);

// The user button is pressed (B1 on a Nucleo); false on a board without one.
bool board_button(void);

// VBUS is present on the user USB port: a host or a powered cable is
// plugged in. Polled from the main loop (vbus.h debounces it); the USB
// controller does not sense VBUS.
bool board_vbus(void);

// Milliseconds since boot.
uint32_t board_millis(void);

// A free-running count of CPU cycles (it wraps), and the CPU clock in Hz.
uint32_t board_cycles(void);
uint32_t board_cpu_hz(void);

// The USB controller's (micro)frame number, modulo 0x4000. At High-Speed it
// advances every 125 us. Called from the USB interrupt.
uint32_t board_usb_microframe(void);

// What the USB interrupt that runs now read at its entry, before TinyUSB ran:
// the (micro)frame number, as board_usb_microframe() gives it, and the audio
// OUT endpoint's EONUM bit (DOEPCTL bit 16), true when the endpoint was set
// to receive in an odd (micro)frame. Called from the USB interrupt.
void board_usb_entry(uint32_t *uframe, bool *odd);

// The fault handler calls this before it lights every LED and stops: a board
// turns off here what must not keep showing a state, such as a display's
// backlight. Interrupts are off and the stack may be gone; no waiting. The
// family's default does nothing.
void board_fault(void);

// Mask all interrupts around a short copy of data an interrupt writes.
void board_irq_disable(void);
void board_irq_enable(void);

// The log has bytes queued: start sending them. The UART interrupt then
// pulls them with log_tx_byte() (log.h) until it returns -1.
void board_log_start(void);

#endif

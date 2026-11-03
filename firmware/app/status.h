// The device's state as the LEDs and a display show it. The USB callbacks
// and the stream probe write it, then call status_changed().

#ifndef STATUS_H
#define STATUS_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
  USB_NOT_MOUNTED,
  USB_MOUNTED,
  USB_SUSPENDED
} usb_state_t;

typedef struct {
  uint32_t version;     // advances at every change
  usb_state_t usb;
  bool high_speed;
  uint32_t rate;        // Hz, as set by the host
  uint8_t alt;          // streaming alt setting, 0 when closed
  uint8_t slot;         // bytes per sample
  uint8_t resolution;   // bits, as the alt setting declares
  bool streaming;
  uint32_t stream_id;   // counts streams since boot, from 1
  uint8_t bits;         // bits in use, over the last second
  bool dop;
  uint32_t lost;        // packets lost in this stream
  uint32_t overflows;   // receive FIFO overflows in this stream
  bool allow_silence;   // silence outside every file gives no result
  // The results of the run: since the user button, the mark command or reset
  bool run_pass;        // a BIT_PERFECT result
  bool run_fail;        // a NOT_BIT_PERFECT result, or a file's first difference
  bool run_unknown;     // an UNKNOWN_SIGNAL result
  bool run_overrun;     // a DEVICE_OVERRUN result
} status_t;

extern status_t status;

static inline void status_changed(void) {
  status.version++;
}

#endif

// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// UAC2 control requests and TinyUSB device callbacks.
//
// The device has no audio clock of its own to follow. It runs asynchronous
// and reports the nominal rate on the feedback endpoint, so the host's USB
// timing paces the stream.

#include "uac2.h"

#include <string.h>

#include "board.h"
#include "log.h"
#include "status.h"
#include "stream.h"
#include "device/dcd.h"
#include "tusb.h"
#include "usb_descriptors.h"
#include "vbus.h"

static const uint32_t rates[] = {
  44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000, 705600, 768000,
};
#define N_RATES TU_ARRAY_SIZE(rates)

static uint32_t current_rate = 48000;

// How long the device stays disconnected when its formats change, so the
// host notices
#define RECONNECT_MS 300u

static bool unplugging;         // in uac2_unplug()

static void log_formats(const char *extra) {
  char text[32];
  usb_formats_text(text, sizeof(text));
  log_line("formats %s%s", text, extra);
}

void uac2_init(void) {
  uac2_alt_t alts[UAC2_ALT_MAX];
  uint8_t n;
  if (!usb_formats_parse(UAC2_FORMATS_DEFAULT, alts, &n)) {
    log_line("error formats-default=%s", UAC2_FORMATS_DEFAULT);
    usb_formats_parse(UAC2_FORMATS_ALL, alts, &n);
  }
  usb_formats_set(alts, n);
  log_formats("");
  // The rate GET_CUR reports before any SET_CUR. A host that finds the rate
  // it wants there need not set it (Linux does not), so the stream and its
  // checker must start with it too.
  stream_set_rate(current_rate);
}

bool uac2_formats(const char *text) {
  if (text == NULL) {
    log_formats("");
    return true;
  }
  uac2_alt_t alts[UAC2_ALT_MAX];
  uint8_t n;
  if (!usb_formats_parse(strcmp(text, "default") == 0 ? UAC2_FORMATS_DEFAULT : text, alts, &n)) {
    return false;
  }
  if (usb_formats_equal(alts, n)) {
    log_formats(" changed=0");
    return true;
  }
  // The host reads the formats at enumeration: disconnect, change, reconnect
  // (vbus.h: the pull-up comes on again RECONNECT_MS later, once VBUS is there)
  vbus_hold_pullup(RECONNECT_MS);
  stream_reset();
  status.usb = USB_NOT_MOUNTED;
  status_changed();
  usb_formats_set(alts, n);
  log_formats(" changed=1");
  log_line("usb disconnected");
  return true;
}

static bool rate_supported(uint32_t rate) {
  for (size_t i = 0; i < N_RATES; i++) {
    if (rates[i] == rate) {
      return true;
    }
  }
  return false;
}

// Frames per microframe in 16.16, rounded to nearest.
static uint32_t feedback_value(uint32_t rate) {
  return (uint32_t) ((((uint64_t) rate << 16) + 4000u) / 8000u);
}

//--------------------------------------------------------------------+
// Device callbacks
//--------------------------------------------------------------------+

static const char *speed_name(tusb_speed_t speed) {
  switch (speed) {
    case TUSB_SPEED_HIGH: return "high";
    case TUSB_SPEED_FULL: return "full";
    default: return "other";
  }
}

void tud_mount_cb(void) {
  status.usb = USB_MOUNTED;
  status.high_speed = tud_speed_get() == TUSB_SPEED_HIGH;
  status_changed();
  log_line("usb mounted speed=%s", speed_name(tud_speed_get()));
}

// The host deconfigured the device (logged once, when it was connected),
// or VBUS went (uac2_unplug(), which logs the disconnect itself)
void tud_umount_cb(void) {
  stream_reset();
  if (status.usb != USB_NOT_MOUNTED) {
    status.usb = USB_NOT_MOUNTED;
    status_changed();
    if (!unplugging) {
      log_line("usb disconnected");
    }
  }
}

// VBUS has gone. The USB controller does not sense VBUS (TinyUSB forces the
// session valid), so it sees only a suspend, or a bus reset when the host
// resets the bus as the plug leaves. TinyUSB learns it as from a controller
// that senses VBUS (dcd_dwc2.c, GOTGINT_SEDET): DCD_EVENT_UNPLUGGED ends
// the configuration and calls tud_umount_cb(), here at once, so that a
// stream's lines come before the caller's "usb disconnected".
void uac2_unplug(void) {
  unplugging = true;
  dcd_int_disable(BOARD_TUD_RHPORT);  // the event as if from the USB interrupt
  dcd_event_bus_signal(BOARD_TUD_RHPORT, DCD_EVENT_UNPLUGGED, true);
  dcd_int_enable(BOARD_TUD_RHPORT);
  tud_task();
  unplugging = false;
}

void tud_suspend_cb(bool remote_wakeup_en) {
  (void) remote_wakeup_en;
  status.usb = USB_SUSPENDED;
  status_changed();
  log_line("usb suspended");
}

void tud_resume_cb(void) {
  status.usb = tud_mounted() ? USB_MOUNTED : USB_NOT_MOUNTED;
  status_changed();
  log_line("usb resumed");
}

//--------------------------------------------------------------------+
// Audio class callbacks
//--------------------------------------------------------------------+

static bool clock_get(uint8_t rhport, tusb_control_request_t const *req) {
  uint8_t const ctrl = TU_U16_HIGH(req->wValue);

  if (ctrl == AUDIO20_CS_CTRL_SAM_FREQ && req->bRequest == AUDIO20_CS_REQ_CUR) {
    audio20_control_cur_4_t cur = {.bCur = (int32_t) tu_htole32(current_rate)};
    return tud_audio_buffer_and_schedule_control_xfer(rhport, req, &cur, sizeof(cur));
  }
  if (ctrl == AUDIO20_CS_CTRL_SAM_FREQ && req->bRequest == AUDIO20_CS_REQ_RANGE) {
    audio20_control_range_4_n_t(N_RATES) range = {.wNumSubRanges = tu_htole16(N_RATES)};
    for (size_t i = 0; i < N_RATES; i++) {
      range.subrange[i].bMin = (int32_t) tu_htole32(rates[i]);
      range.subrange[i].bMax = (int32_t) tu_htole32(rates[i]);
      range.subrange[i].bRes = 0;
    }
    return tud_audio_buffer_and_schedule_control_xfer(rhport, req, &range, sizeof(range));
  }
  if (ctrl == AUDIO20_CS_CTRL_CLK_VALID && req->bRequest == AUDIO20_CS_REQ_CUR) {
    audio20_control_cur_1_t valid = {.bCur = 1};
    return tud_audio_buffer_and_schedule_control_xfer(rhport, req, &valid, sizeof(valid));
  }
  return false;
}

static bool clock_set(tusb_control_request_t const *req, uint8_t const *buf) {
  uint8_t const ctrl = TU_U16_HIGH(req->wValue);

  if (ctrl != AUDIO20_CS_CTRL_SAM_FREQ || req->bRequest != AUDIO20_CS_REQ_CUR
      || req->wLength != sizeof(audio20_control_cur_4_t)) {
    return false;
  }
  uint32_t rate = tu_le32toh(tu_unaligned_read32(buf));
  if (!rate_supported(rate)) {
    log_line("rate unsupported=%lu", (unsigned long) rate);
    return false;
  }
  current_rate = rate;
  tud_audio_fb_set(feedback_value(rate));
  stream_set_rate(rate);
  return true;
}

bool tud_audio_get_req_entity_cb(uint8_t rhport, tusb_control_request_t const *req) {
  uint8_t const entity = TU_U16_HIGH(req->wIndex);
  if (entity == UAC2_ENTITY_CLOCK && clock_get(rhport, req)) {
    return true;
  }
  log_line("usb unsupported get entity=%u ctrl=%u req=%u", entity, TU_U16_HIGH(req->wValue),
           req->bRequest);
  return false;
}

bool tud_audio_set_req_entity_cb(uint8_t rhport, tusb_control_request_t const *req, uint8_t *buf) {
  (void) rhport;
  uint8_t const entity = TU_U16_HIGH(req->wIndex);
  if (entity == UAC2_ENTITY_CLOCK && clock_set(req, buf)) {
    return true;
  }
  log_line("usb unsupported set entity=%u ctrl=%u req=%u", entity, TU_U16_HIGH(req->wValue),
           req->bRequest);
  return false;
}

// After the endpoints of the new alt setting are open. The driver has just
// cleared the feedback value, so set it again.
bool tud_audio_set_itf_cb(uint8_t rhport, tusb_control_request_t const *req) {
  (void) rhport;
  uint8_t const itf = tu_u16_low(req->wIndex);
  uint8_t const alt = tu_u16_low(req->wValue);

  if (itf == ITF_NUM_AUDIO_STREAMING && alt <= uac2_alt_count) {
    stream_set_alt(alt);
    if (alt != 0) {
      tud_audio_fb_set(feedback_value(current_rate));
    }
  }
  return true;
}

// Constant feedback, set by this file: the driver computes nothing.
void tud_audio_feedback_params_cb(uint8_t func_id, uint8_t alt_itf, audio_feedback_params_t *param) {
  (void) func_id;
  (void) alt_itf;
  param->method = AUDIO_FEEDBACK_METHOD_DISABLED;
  param->sample_freq = current_rate;
}

bool tud_audio_rx_done_isr(uint8_t rhport, uint16_t n_bytes_received, uint8_t func_id, uint8_t ep_out,
                           uint8_t cur_alt_setting) {
  (void) rhport;
  (void) func_id;
  (void) ep_out;
  (void) cur_alt_setting;
  stream_packet_isr(n_bytes_received);
  return true;
}

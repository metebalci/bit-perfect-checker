// Device, configuration and string descriptors.

#include <string.h>

#include "stm32u5xx.h"
#include "tusb.h"
#include "usb_descriptors.h"

uac2_alt_t uac2_alts[UAC2_ALT_MAX + 1];
uint8_t uac2_alt_count;

//--------------------------------------------------------------------+
// Device
//--------------------------------------------------------------------+

static const tusb_desc_device_t desc_device = {
  .bLength            = sizeof(tusb_desc_device_t),
  .bDescriptorType    = TUSB_DESC_DEVICE,
  .bcdUSB             = 0x0200,
  // IAD device class, required for UAC2
  .bDeviceClass       = TUSB_CLASS_MISC,
  .bDeviceSubClass    = MISC_SUBCLASS_COMMON,
  .bDeviceProtocol    = MISC_PROTOCOL_IAD,
  .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,
  .idVendor           = USB_VID,
  .idProduct          = USB_PID,
  .bcdDevice          = USB_BCD,
  .iManufacturer      = 1,
  .iProduct           = 2,
  .iSerialNumber      = 3,
  .bNumConfigurations = 1,
};

uint8_t const *tud_descriptor_device_cb(void) {
  return (uint8_t const *) &desc_device;
}

// A High-Speed device must answer for its other speed. This one is useless
// at Full-Speed, so the qualifier simply mirrors the device descriptor.
static const tusb_desc_device_qualifier_t desc_qualifier = {
  .bLength            = sizeof(tusb_desc_device_qualifier_t),
  .bDescriptorType    = TUSB_DESC_DEVICE_QUALIFIER,
  .bcdUSB             = 0x0200,
  .bDeviceClass       = TUSB_CLASS_MISC,
  .bDeviceSubClass    = MISC_SUBCLASS_COMMON,
  .bDeviceProtocol    = MISC_PROTOCOL_IAD,
  .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,
  .bNumConfigurations = 1,
  .bReserved          = 0,
};

uint8_t const *tud_descriptor_device_qualifier_cb(void) {
  return (uint8_t const *) &desc_qualifier;
}

//--------------------------------------------------------------------+
// Configuration
//--------------------------------------------------------------------+

#define AC_UNITS_LEN (TUD_AUDIO20_DESC_CLK_SRC_LEN + TUD_AUDIO20_DESC_INPUT_TERM_LEN \
                      + TUD_AUDIO20_DESC_OUTPUT_TERM_LEN)

#define AS_ALT_LEN (TUD_AUDIO20_DESC_STD_AS_LEN + TUD_AUDIO20_DESC_CS_AS_INT_LEN \
                    + TUD_AUDIO20_DESC_TYPE_I_FORMAT_LEN + TUD_AUDIO20_DESC_STD_AS_ISO_EP_LEN \
                    + TUD_AUDIO20_DESC_CS_AS_ISO_EP_LEN + TUD_AUDIO20_DESC_STD_AS_ISO_FB_EP_LEN)

#define HEAD_LEN (TUD_CONFIG_DESC_LEN + TUD_AUDIO20_DESC_IAD_LEN + TUD_AUDIO20_DESC_STD_AC_LEN \
                  + TUD_AUDIO20_DESC_CS_AC_LEN + AC_UNITS_LEN + TUD_AUDIO20_DESC_STD_AS_LEN)
#define CONFIG_MAX_LEN (HEAD_LEN + UAC2_ALT_MAX * AS_ALT_LEN)

#define STEREO (AUDIO20_CHANNEL_CONFIG_FRONT_LEFT | AUDIO20_CHANNEL_CONFIG_FRONT_RIGHT)

// One streaming alt setting, numbered 0 here and renumbered when the
// configuration is put together: asynchronous isochronous OUT every
// microframe, plus the explicit feedback IN endpoint polled every 8
// microframes (1 ms).
#define AS_ALT(_slot, _resolution, _epsize)                                                      \
  TUD_AUDIO20_DESC_STD_AS_INT(ITF_NUM_AUDIO_STREAMING, 0, 2, 0),                                 \
  TUD_AUDIO20_DESC_CS_AS_INT(UAC2_ENTITY_INPUT_TERMINAL, AUDIO20_CTRL_NONE, AUDIO20_FORMAT_TYPE_I, \
                             AUDIO20_DATA_FORMAT_TYPE_I_PCM, UAC2_CHANNELS, STEREO, 0),          \
  TUD_AUDIO20_DESC_TYPE_I_FORMAT(_slot, _resolution),                                            \
  TUD_AUDIO20_DESC_STD_AS_ISO_EP(EP_AUDIO_OUT,                                                   \
                                 (uint8_t) ((uint8_t) TUSB_XFER_ISOCHRONOUS                      \
                                            | (uint8_t) TUSB_ISO_EP_ATT_ASYNCHRONOUS             \
                                            | (uint8_t) TUSB_ISO_EP_ATT_DATA),                   \
                                 _epsize, 1),                                                    \
  TUD_AUDIO20_DESC_CS_AS_ISO_EP(AUDIO20_CS_AS_ISO_DATA_EP_ATT_NON_MAX_PACKETS_OK,                \
                                AUDIO20_CTRL_NONE,                                               \
                                AUDIO20_CS_AS_ISO_DATA_EP_LOCK_DELAY_UNIT_MILLISEC, 1),          \
  TUD_AUDIO20_DESC_STD_AS_ISO_FB_EP(EP_AUDIO_FB, 4, 4)

// The streaming interface descriptor's bAlternateSetting
#define AS_ALT_NUMBER_OFFSET 3

static const uint8_t desc_head[] = {
  // wTotalLength is set when the configuration is put together
  TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, 0, 0x00, 100),

  TUD_AUDIO20_DESC_IAD(ITF_NUM_AUDIO_CONTROL, ITF_NUM_TOTAL, 0),
  TUD_AUDIO20_DESC_STD_AC(ITF_NUM_AUDIO_CONTROL, 0, 0),
  TUD_AUDIO20_DESC_CS_AC(0x0200, AUDIO20_FUNC_IO_BOX, AC_UNITS_LEN, 0),
  // Internal programmable clock: the host sets the rate, and reads it and
  // its validity back.
  TUD_AUDIO20_DESC_CLK_SRC(UAC2_ENTITY_CLOCK, AUDIO20_CLOCK_SOURCE_ATT_INT_PRO_CLK,
                           (AUDIO20_CTRL_RW << AUDIO20_CLOCK_SOURCE_CTRL_CLK_FRQ_POS)
                           | (AUDIO20_CTRL_R << AUDIO20_CLOCK_SOURCE_CTRL_CLK_VAL_POS),
                           0, 0),
  TUD_AUDIO20_DESC_INPUT_TERM(UAC2_ENTITY_INPUT_TERMINAL, AUDIO_TERM_TYPE_USB_STREAMING, 0,
                              UAC2_ENTITY_CLOCK, UAC2_CHANNELS, STEREO, 0, 0, 0),
  TUD_AUDIO20_DESC_OUTPUT_TERM(UAC2_ENTITY_OUTPUT_TERMINAL, AUDIO_TERM_TYPE_OUT_GENERIC_SPEAKER, 0,
                               UAC2_ENTITY_INPUT_TERMINAL, UAC2_ENTITY_CLOCK, 0, 0),

  // Alt 0: zero bandwidth
  TUD_AUDIO20_DESC_STD_AS_INT(ITF_NUM_AUDIO_STREAMING, 0, 0, 0),
};

TU_VERIFY_STATIC(sizeof(desc_head) == HEAD_LEN, "configuration head length");

typedef struct {
  uac2_alt_t alt;
  uint8_t desc[AS_ALT_LEN];
} format_t;

static const format_t formats[UAC2_ALT_MAX] = {
  {{2, 16}, {AS_ALT(2, 16, UAC2_EP_OUT_SZ_2BYTE)}},
  {{4, 24}, {AS_ALT(4, 24, UAC2_EP_OUT_SZ_4BYTE)}},
  {{4, 32}, {AS_ALT(4, 32, UAC2_EP_OUT_SZ_4BYTE)}},
  {{3, 24}, {AS_ALT(3, 24, UAC2_EP_OUT_SZ_3BYTE)}},
};

static uint8_t desc_configuration[CONFIG_MAX_LEN];
static uint16_t desc_configuration_len;

static const format_t *format_find(uint8_t slot, uint8_t resolution) {
  for (size_t i = 0; i < TU_ARRAY_SIZE(formats); i++) {
    if (formats[i].alt.slot == slot && formats[i].alt.resolution == resolution) {
      return &formats[i];
    }
  }
  return NULL;
}

bool usb_formats_parse(const char *text, uac2_alt_t *alts, uint8_t *count) {
  uint8_t n = 0;
  const char *p = text;
  while (*p != '\0') {
    unsigned bits = 0;
    unsigned bytes = 0;
    while (*p >= '0' && *p <= '9' && bits < 100) {
      bits = bits * 10 + (unsigned) (*p++ - '0');
    }
    if (*p++ != '/') {
      return false;
    }
    while (*p >= '0' && *p <= '9' && bytes < 100) {
      bytes = bytes * 10 + (unsigned) (*p++ - '0');
    }
    if (*p == ',') {
      p++;
    } else if (*p != '\0') {
      return false;
    }
    if (n == UAC2_ALT_MAX || format_find((uint8_t) bytes, (uint8_t) bits) == NULL) {
      return false;
    }
    for (uint8_t i = 0; i < n; i++) {
      if (alts[i].slot == bytes && alts[i].resolution == bits) {
        return false;
      }
    }
    alts[n++] = (uac2_alt_t) {(uint8_t) bytes, (uint8_t) bits};
  }
  if (n == 0) {
    return false;
  }
  *count = n;
  return true;
}

void usb_formats_set(const uac2_alt_t *alts, uint8_t count) {
  memcpy(desc_configuration, desc_head, HEAD_LEN);
  uint16_t len = HEAD_LEN;
  uac2_alts[0] = (uac2_alt_t) {0, 0};
  for (uint8_t i = 0; i < count; i++) {
    const format_t *f = format_find(alts[i].slot, alts[i].resolution);
    memcpy(desc_configuration + len, f->desc, AS_ALT_LEN);
    desc_configuration[len + AS_ALT_NUMBER_OFFSET] = (uint8_t) (i + 1);
    len += AS_ALT_LEN;
    uac2_alts[i + 1] = alts[i];
  }
  uac2_alt_count = count;
  desc_configuration[2] = TU_U16_LOW(len);
  desc_configuration[3] = TU_U16_HIGH(len);
  desc_configuration_len = len;
}

bool usb_formats_equal(const uac2_alt_t *alts, uint8_t count) {
  if (count != uac2_alt_count) {
    return false;
  }
  for (uint8_t i = 0; i < count; i++) {
    if (alts[i].slot != uac2_alts[i + 1].slot || alts[i].resolution != uac2_alts[i + 1].resolution) {
      return false;
    }
  }
  return true;
}

void usb_formats_text(char *buf, size_t size) {
  size_t n = 0;
  buf[0] = '\0';
  for (uint8_t i = 1; i <= uac2_alt_count && n + 6 < size; i++) {
    unsigned bits = uac2_alts[i].resolution;
    if (i > 1) {
      buf[n++] = ',';
    }
    buf[n++] = (char) ('0' + bits / 10);
    buf[n++] = (char) ('0' + bits % 10);
    buf[n++] = '/';
    buf[n++] = (char) ('0' + uac2_alts[i].slot);
    buf[n] = '\0';
  }
}

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
  (void) index;
  return desc_configuration;
}

// The other-speed configuration is the same configuration, retyped.
static uint8_t desc_other_speed[CONFIG_MAX_LEN];

uint8_t const *tud_descriptor_other_speed_configuration_cb(uint8_t index) {
  (void) index;
  memcpy(desc_other_speed, desc_configuration, desc_configuration_len);
  desc_other_speed[1] = TUSB_DESC_OTHER_SPEED_CONFIG;
  return desc_other_speed;
}

//--------------------------------------------------------------------+
// Strings
//--------------------------------------------------------------------+

static const char *const string_desc[] = {
  NULL,                   // 0: language, answered below
  "Mete Balci",           // 1: manufacturer
  "Bit-Perfect Checker",  // 2: product
  NULL,                   // 3: serial, the chip's unique ID
};

static uint16_t desc_str[1 + 32];

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
  (void) langid;
  size_t n = 0;

  if (index == 0) {
    desc_str[1] = 0x0409;  // English (US)
    n = 1;
  } else if (index == 3) {
    static const char hex[] = "0123456789ABCDEF";
    const volatile uint32_t *uid = (const volatile uint32_t *) UID_BASE;
    for (int w = 0; w < 3; w++) {
      uint32_t v = uid[w];
      for (int i = 0; i < 8; i++) {
        desc_str[1 + n++] = (uint16_t) hex[(v >> 28) & 0xF];
        v <<= 4;
      }
    }
  } else if (index < TU_ARRAY_SIZE(string_desc)) {
    const char *s = string_desc[index];
    while (s[n] != '\0' && n < TU_ARRAY_SIZE(desc_str) - 1) {
      desc_str[1 + n] = (uint16_t) s[n];
      n++;
    }
  } else {
    return NULL;
  }

  desc_str[0] = (uint16_t) ((TUSB_DESC_STRING << 8) | (2 * n + 2));
  return desc_str;
}

// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The UAC2 descriptor parser, the RANGE reader and the alt setting choice
// of the BPC1 generator (uac2_host.h).
//
// The parser walks the configuration descriptor twice. The first walk checks
// every descriptor's length against wTotalLength and collects the audio
// control interface, its clock entities and input terminals, and which
// streaming interface links to which terminal. The second walk reads the
// alt settings of the streaming interface linked to the USB streaming input
// terminal; other interfaces, and other audio streaming interfaces (a
// capture path, say), are skipped.

#include "uac2_host.h"

#include <string.h>

// Descriptor types: USB 2.0 Table 9-5; CS_INTERFACE, UAC2 Table A-8
#define DESC_CONFIGURATION 0x02u
#define DESC_INTERFACE     0x04u
#define DESC_ENDPOINT      0x05u
#define DESC_CS_INTERFACE  0x24u

#define CONFIGURATION_LEN  9u
#define INTERFACE_LEN      9u
#define ENDPOINT_LEN       7u

// Interface class, subclasses and protocol: UAC2 Tables A-4, A-5, A-6
#define CLASS_AUDIO        0x01u
#define SUBCLASS_CONTROL   0x01u
#define SUBCLASS_STREAMING 0x02u
#define PROTOCOL_UAC1      0x00u
#define PROTOCOL_UAC2      0x20u  // IP_VERSION_02_00

// Audio control descriptor subtypes (UAC2 Table A-9) and their lengths
// (4.7.2 Table 4-5, 4.7.2.1 Table 4-6, 4.7.2.2 Table 4-7, 4.7.2.4 Table 4-9)
#define AC_HEADER          0x01u
#define AC_INPUT_TERMINAL  0x02u
#define AC_CLOCK_SOURCE    0x0Au
#define AC_CLOCK_SELECTOR  0x0Bu
#define AC_HEADER_LEN      9u
#define INPUT_TERMINAL_LEN 17u
#define CLOCK_SOURCE_LEN   8u
#define CLOCK_SELECTOR_LEN 7u  // plus one byte per input pin
#define BCD_ADC_2_00       0x0200u

// The USB streaming terminal type (Terminal Types 2.0, Table 2-1)
#define TERMINAL_USB_STREAMING 0x0101u

// Audio streaming descriptor subtypes (UAC2 Table A-10) and their lengths
// (4.9.2 Table 4-27; Audio Data Formats 2.0 2.3.1.6 Table 2-2)
#define AS_GENERAL         0x01u
#define AS_FORMAT_TYPE     0x02u
#define AS_GENERAL_LEN     16u
#define FORMAT_TYPE_I_LEN  6u
#define FORMAT_TYPE_I      0x01u  // Audio Data Formats 2.0 Table A-1
#define FORMAT_PCM         0x00000001u  // bmFormats bit 0, Table A-2

// Endpoint bmAttributes (USB 2.0 Table 9-13): transfer type, sync type,
// usage type; wMaxPacketSize bits 12:11, additional transactions
#define EP_ISOCHRONOUS     0x01u
#define EP_USAGE_DATA      0u
#define EP_USAGE_FEEDBACK  1u
#define EP_USAGE_IMPLICIT  2u
#define EP_DIR_IN          0x80u
#define FEEDBACK_HS_LEN    4u  // 16.16 at High-Speed (USB 2.0 5.12.4.2)

// Limits of what the first walk keeps
#define ENTITY_MAX 16u
#define LINK_MAX   16u

static const uint32_t bpc1_rates[UAC2_HOST_RATES] = {
  44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000, 705600, 768000,
};

static uint16_t le16(const uint8_t *p) {
  return (uint16_t) (p[0] | (uint16_t) p[1] << 8);
}

static uint32_t le32(const uint8_t *p) {
  return (uint32_t) p[0] | (uint32_t) p[1] << 8 | (uint32_t) p[2] << 16 | (uint32_t) p[3] << 24;
}

typedef struct {
  uint8_t id;
  uint8_t a;  // clock source: bmControls; clock selector: bNrInPins; terminal: bCSourceID
  uint8_t b;  // clock selector: its first input
  uint16_t type;  // terminal: wTerminalType
} entity_t;

typedef struct {
  bool found;          // an audio control interface
  uint8_t itf;
  uint8_t protocol;
  bool header;
  uint16_t bcd_adc;
  entity_t sources[ENTITY_MAX];
  entity_t selectors[ENTITY_MAX];
  entity_t terminals[ENTITY_MAX];
  uint8_t n_sources, n_selectors, n_terminals;
  uint8_t link_itf[LINK_MAX];  // a UAC2 streaming interface and its bTerminalLink
  uint8_t link_terminal[LINK_MAX];
  uint8_t n_links;
} walk1_t;

static void add_entity(entity_t *list, uint8_t *n, entity_t e) {
  if (*n < ENTITY_MAX) {
    list[(*n)++] = e;
  }
}

static const entity_t *find_entity(const entity_t *list, uint8_t n, uint8_t id) {
  for (uint8_t i = 0; i < n; i++) {
    if (list[i].id == id) {
      return &list[i];
    }
  }
  return NULL;
}

// First walk: lengths, the audio control interface and the links
static const char *walk1(const uint8_t *desc, uint16_t total, walk1_t *w) {
  uint8_t cls = 0;
  uint8_t sub = 0;
  uint8_t proto = 0;
  uint8_t itf = 0;
  bool in_ac = false;  // inside the audio control interface found

  for (uint32_t off = 0; off < total;) {
    if (total - off < 2u) {
      return "cut";
    }
    const uint8_t *d = desc + off;
    const uint8_t bl = d[0];
    if (bl < 2u) {
      return "length";
    }
    if (bl > total - off) {
      return "cut";
    }
    const uint8_t type = d[1];

    if (off == 0) {
      if (bl < CONFIGURATION_LEN) {
        return "length";
      }
    } else if (type == DESC_INTERFACE) {
      if (bl < INTERFACE_LEN) {
        return "length";
      }
      itf = d[2];
      cls = d[5];
      sub = d[6];
      proto = d[7];
      in_ac = false;
      if (cls == CLASS_AUDIO && sub == SUBCLASS_CONTROL && (!w->found || w->itf == itf)) {
        w->found = true;
        w->itf = itf;
        w->protocol = proto;
        in_ac = true;
      }
    } else if (type == DESC_ENDPOINT) {
      if (bl < ENDPOINT_LEN) {
        return "length";
      }
    } else if (type == DESC_CS_INTERFACE && cls == CLASS_AUDIO && proto == PROTOCOL_UAC2) {
      if (bl < 3u) {
        return "length";
      }
      const uint8_t subtype = d[2];
      if (in_ac && subtype == AC_HEADER) {
        if (bl < AC_HEADER_LEN) {
          return "length";
        }
        w->header = true;
        w->bcd_adc = le16(d + 3);
      } else if (in_ac && subtype == AC_INPUT_TERMINAL) {
        if (bl < INPUT_TERMINAL_LEN) {
          return "length";
        }
        add_entity(w->terminals, &w->n_terminals, (entity_t) {.id = d[3], .a = d[7], .type = le16(d + 4)});
      } else if (in_ac && subtype == AC_CLOCK_SOURCE) {
        if (bl < CLOCK_SOURCE_LEN) {
          return "length";
        }
        add_entity(w->sources, &w->n_sources, (entity_t) {.id = d[3], .a = d[5]});
      } else if (in_ac && subtype == AC_CLOCK_SELECTOR) {
        if (bl < CLOCK_SELECTOR_LEN || bl < CLOCK_SELECTOR_LEN + (uint32_t) d[4]) {
          return "length";
        }
        add_entity(w->selectors, &w->n_selectors,
                   (entity_t) {.id = d[3], .a = d[4], .b = d[4] > 0u ? d[5] : 0u});
      } else if (sub == SUBCLASS_STREAMING && subtype == AS_GENERAL) {
        if (bl < AS_GENERAL_LEN) {
          return "length";
        }
        if (w->n_links < LINK_MAX) {
          w->link_itf[w->n_links] = itf;
          w->link_terminal[w->n_links++] = d[3];
        }
      }
    }
    off += bl;
  }
  return NULL;
}

// An alt setting of the streaming interface, while the second walk is in it
typedef struct {
  uac2_host_alt_t a;
  uint8_t endpoints;   // bNumEndpoints
  uint8_t seen;        // endpoint descriptors seen
  bool general;        // AS_GENERAL seen
  bool format;         // FORMAT_TYPE seen
  bool data;           // data endpoint seen
} alt_walk_t;

static const char *alt_finish(const alt_walk_t *w, uac2_host_device_t *dev, bool *alt0) {
  if (w->seen != w->endpoints) {
    return "endpoint";
  }
  if (w->a.alt == 0u) {
    if (w->endpoints != 0u) {
      return "no-streaming";
    }
    *alt0 = true;
    return NULL;
  }
  if (!w->general || !w->format) {
    return "format";
  }
  if (!w->data) {
    return "endpoint";
  }
  if (w->a.sync == UAC2_SYNC_ASYNC && w->a.ep_fb == 0u) {
    return "implicit-feedback";
  }
  for (uint8_t i = 0; i < dev->alt_count; i++) {
    if (dev->alts[i].alt == w->a.alt) {
      return "alt-repeated";
    }
  }
  if (dev->alt_count == UAC2_HOST_ALT_MAX) {
    return "too-many-alts";
  }
  // In alt setting order
  uint8_t i = dev->alt_count++;
  while (i > 0u && dev->alts[i - 1u].alt > w->a.alt) {
    dev->alts[i] = dev->alts[i - 1u];
    i--;
  }
  dev->alts[i] = w->a;
  return NULL;
}

static const char *alt_descriptor(alt_walk_t *w, const uint8_t *d) {
  const uint8_t bl = d[0];
  if (d[1] == DESC_CS_INTERFACE && bl < 3u) {
    return "length";
  }
  if (d[1] == DESC_CS_INTERFACE && d[2] == AS_GENERAL) {
    if (bl < AS_GENERAL_LEN) {
      return "length";
    }
    if (d[5] != FORMAT_TYPE_I || (le32(d + 6) & FORMAT_PCM) == 0u) {
      return "format";
    }
    if (d[10] != 2u) {
      return "channels";
    }
    w->general = true;
  } else if (d[1] == DESC_CS_INTERFACE && d[2] == AS_FORMAT_TYPE) {
    if (bl < FORMAT_TYPE_I_LEN) {
      return "length";
    }
    const uint8_t slot = d[4];
    const uint8_t res = d[5];
    if (d[3] != FORMAT_TYPE_I || slot < 2u || slot > 4u || res == 0u || res > 8u * slot) {
      return "format";
    }
    w->a.slot = slot;
    w->a.res = res;
    w->format = true;
  } else if (d[1] == DESC_ENDPOINT) {
    const uint8_t addr = d[2];
    const uint8_t attr = d[3];
    const uint16_t mps = le16(d + 4);
    const uint8_t interval = d[6];
    const uint8_t usage = (uint8_t) ((attr >> 4) & 3u);
    w->seen++;
    if ((attr & 3u) != EP_ISOCHRONOUS) {
      return "endpoint";
    }
    if (usage == EP_USAGE_IMPLICIT) {
      return "implicit-feedback";
    }
    if (usage == EP_USAGE_DATA) {
      if ((addr & EP_DIR_IN) != 0u || w->data) {
        return "endpoint";
      }
      if ((mps >> 11) != 0u) {
        return "high-bandwidth";
      }
      if (interval != 1u) {
        return "interval";
      }
      w->data = true;
      w->a.ep_out = addr;
      w->a.out_size = (uint16_t) (mps & 0x7FFu);
      w->a.sync = (uint8_t) ((attr >> 2) & 3u);
    } else if (usage == EP_USAGE_FEEDBACK) {
      if ((addr & EP_DIR_IN) == 0u || w->a.ep_fb != 0u || (mps >> 11) != 0u || (mps & 0x7FFu) < FEEDBACK_HS_LEN
          || interval == 0u || interval > 16u) {
        return "endpoint";
      }
      w->a.ep_fb = addr;
      w->a.fb_size = (uint16_t) (mps & 0x7FFu);
      w->a.fb_interval = interval;
    } else {
      return "endpoint";
    }
  }
  return NULL;
}

// Second walk: the alt settings of the streaming interface
static const char *walk2(const uint8_t *desc, uint16_t total, uac2_host_device_t *dev) {
  alt_walk_t w;
  bool in = false;
  bool alt0 = false;
  const char *why;

  for (uint32_t off = desc[0]; off < total; off += desc[off]) {
    const uint8_t *d = desc + off;
    if (d[1] == DESC_INTERFACE) {
      if (in && (why = alt_finish(&w, dev, &alt0)) != NULL) {
        return why;
      }
      in = d[2] == dev->itf_as;
      if (in) {
        memset(&w, 0, sizeof(w));
        w.a.alt = d[3];
        w.endpoints = d[4];
      }
    } else if (in && (why = alt_descriptor(&w, d)) != NULL) {
      return why;
    }
  }
  if (in && (why = alt_finish(&w, dev, &alt0)) != NULL) {
    return why;
  }
  if (!alt0) {
    return "no-streaming";
  }
  if (dev->alt_count == 0u) {
    return "no-alt";
  }
  return NULL;
}

const char *uac2_host_parse(const uint8_t *desc, size_t len, uac2_host_device_t *dev) {
  memset(dev, 0, sizeof(*dev));
  if (len < CONFIGURATION_LEN) {
    return "cut";
  }
  if (desc[1] != DESC_CONFIGURATION) {
    return "not-config";
  }
  const uint16_t total = le16(desc + 2);
  if (total > len) {
    return "cut";
  }
  if (total < CONFIGURATION_LEN) {
    return "length";
  }

  static walk1_t w;  // about 330 bytes: off the stack
  memset(&w, 0, sizeof(w));
  const char *why = walk1(desc, total, &w);
  if (why != NULL) {
    return why;
  }
  if (!w.found) {
    return "not-audio";
  }
  if (w.protocol == PROTOCOL_UAC1) {
    return "uac1";
  }
  if (w.protocol != PROTOCOL_UAC2 || !w.header || w.bcd_adc != BCD_ADC_2_00) {
    return "version";
  }

  // The first USB streaming input terminal with a streaming interface
  const entity_t *terminal = NULL;
  bool streaming_terminal = false;
  for (uint8_t i = 0; i < w.n_terminals && terminal == NULL; i++) {
    if (w.terminals[i].type != TERMINAL_USB_STREAMING) {
      continue;
    }
    streaming_terminal = true;
    for (uint8_t k = 0; k < w.n_links; k++) {
      if (w.link_terminal[k] == w.terminals[i].id && w.link_itf[k] != w.itf) {
        terminal = &w.terminals[i];
        dev->itf_as = w.link_itf[k];
        break;
      }
    }
  }
  if (!streaming_terminal) {
    return "no-terminal";
  }
  if (terminal == NULL) {
    return "no-streaming";
  }

  // Its clock: a clock source, or a clock selector with one input that is
  // one. With more inputs the device picks the clock, so the requests could
  // go to the wrong one.
  const entity_t *source = find_entity(w.sources, w.n_sources, terminal->a);
  if (source == NULL) {
    const entity_t *selector = find_entity(w.selectors, w.n_selectors, terminal->a);
    if (selector != NULL && selector->a == 1u) {
      source = find_entity(w.sources, w.n_sources, selector->b);
    }
  }
  if (source == NULL) {
    return "clock";
  }

  dev->itf_ac = w.itf;
  dev->terminal = terminal->id;
  dev->clock = source->id;
  dev->clock_set = (source->a & 3u) == 3u;  // bmControls D1..0: frequency, host-programmable
  return walk2(desc, total, dev);
}

uint32_t uac2_host_rate(uint32_t i) {
  return i < UAC2_HOST_RATES ? bpc1_rates[i] : 0u;
}

bool uac2_host_range(const uint8_t *range, size_t len, uint32_t *rates) {
  if (len < UAC2_HOST_RANGE_LEN(1)) {
    return false;
  }
  uint32_t n = le16(range);
  if (n > (len - 2u) / 12u) {
    n = (uint32_t) ((len - 2u) / 12u);
  }
  if (n > UAC2_HOST_RANGE_MAX) {
    n = UAC2_HOST_RANGE_MAX;
  }
  if (n == 0u) {
    return false;
  }
  uint32_t mask = 0;
  for (uint32_t s = 0; s < n; s++) {
    const uint8_t *p = range + 2u + 12u * s;
    const uint32_t lo = le32(p);
    const uint32_t hi = le32(p + 4);
    const uint32_t res = le32(p + 8);
    for (uint32_t i = 0; i < UAC2_HOST_RATES; i++) {
      const uint32_t r = bpc1_rates[i];
      if (res == 0u ? r == lo : (r >= lo && r <= hi && (r - lo) % res == 0u)) {
        mask |= 1u << i;
      }
    }
  }
  *rates = mask;
  return true;
}

uint32_t uac2_host_packet_max(uint32_t rate, uint8_t slot) {
  return ((rate + 7999u) / 8000u + 1u) * slot * 2u;
}

int uac2_host_choose(const uac2_host_device_t *dev, uint32_t rate, uint8_t depth, bool dop) {
  const uint8_t need = dop ? 24u : depth;
  int best = -1;
  for (int i = 0; i < (int) dev->alt_count; i++) {
    const uac2_host_alt_t *a = &dev->alts[i];
    if (a->res < need || 8u * a->slot < need || uac2_host_packet_max(rate, a->slot) > a->out_size) {
      continue;
    }
    if (best < 0 || a->slot < dev->alts[best].slot) {
      best = i;
    }
  }
  return best;
}

// Appends text to buf at *n, keeping a terminating zero; false when full
static bool append(char *buf, size_t size, size_t *n, const char *text) {
  size_t k = strlen(text);
  if (*n + k + 1u > size) {
    return false;
  }
  memcpy(buf + *n, text, k + 1u);
  *n += k;
  return true;
}

static void decimal(char *out, uint32_t v) {
  char tmp[11];
  int k = 0;
  do {
    tmp[k++] = (char) ('0' + v % 10u);
    v /= 10u;
  } while (v != 0u);
  while (k > 0) {
    *out++ = tmp[--k];
  }
  *out = '\0';
}

void uac2_host_formats_text(const uac2_host_device_t *dev, char *buf, size_t size) {
  size_t n = 0;
  buf[0] = '\0';
  for (uint8_t i = 0; i < dev->alt_count; i++) {
    char item[16];
    item[0] = ',';
    decimal(item + 1, dev->alts[i].res);
    size_t k = strlen(item);
    item[k++] = '/';
    decimal(item + k, dev->alts[i].slot);
    if (!append(buf, size, &n, i == 0u ? item + 1 : item)) {
      return;
    }
  }
}

void uac2_host_rates_text(uint32_t rates, char *buf, size_t size) {
  size_t n = 0;
  buf[0] = '\0';
  for (uint32_t i = 0; i < UAC2_HOST_RATES; i++) {
    if ((rates & (1u << i)) != 0u) {
      char item[12];
      item[0] = ',';
      decimal(item + 1, bpc1_rates[i]);
      if (!append(buf, size, &n, n == 0u ? item + 1 : item)) {
        return;
      }
    }
  }
}

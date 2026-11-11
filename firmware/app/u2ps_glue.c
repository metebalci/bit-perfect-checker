// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// The USB audio function on u2ps (uac2-playback-stack): u2ps's app
// callbacks, the stream's class memory, and the main loop's side (uac2.h).
// u2ps builds every descriptor from the description in usb_formats.c.
//
// The device has no audio clock of its own to follow. It runs asynchronous
// and reports the nominal rate on the feedback endpoint (the value u2ps
// sets), so the host's USB timing paces the stream.
//
// u2ps calls the app in the USB interrupt, and in the main loop with its
// vector masked at a detach, a VBUS loss, a re-init and a deferred open.
// The packet path stays in the interrupt: each packet goes to the stream's
// queue in its buffer (stream_packet_isr()), and the main loop gives the
// buffer back once it has read it (uac2_release()). Everything else is
// queued and handled by uac2_task() in the main loop.
//
// A stream start and a change of the sampling frequency wait for the main
// loop (U2PS_STREAM_DEFER, U2PS_REQ_DEFER): it sets the new format, which
// ends the old stream and makes the next packet begin a new segment, before
// it opens the stream, so no packet of the new stream reaches the
// interrupt's side before its segment. It answers the request and, only if
// the answer went out, sets the new rate in the same stretch with
// interrupts off, so the next packet the interrupt takes begins the new
// rate's segment. That packet may still be one sent at the old rate: one
// received while the answer masked the interrupt, or, from a host that
// streams through SET_CUR, one sent after the status stage. Only a pause
// begins a segment from the interrupt.

#include "uac2.h"

#include <stdatomic.h>
#include <string.h>

#include "board.h"
#include "family.h"
#include "log.h"
#include "status.h"
#include "stream.h"
#include "u2ps_dev.h"
#include "u2ps_dport_cfg.h"
#include "usb_descriptors.h"
#include "vbus.h"

// The DWC2 port names why it did not start or refused a stream; a build for
// the PC test kit has a fake port instead
#if defined(U2PS_CHIP_STM32F723) || defined(U2PS_CHIP_STM32U5A5)
#include "u2ps_dwc2_dcd.h"
#define PORT_DWC2 1
#endif

#if !U2PS_CONTROL_HOOK
#error "u2ps_glue.c needs u2ps built with U2PS_CONTROL_HOOK=ON (CMakeLists.txt)"
#endif
#if U2PS_VBUS != U2PS_VBUS_APP
#error "u2ps_glue.c reports VBUS itself: u2ps built with U2PS_VBUS=app (CMakeLists.txt)"
#endif

// u2ps's pause is the stream's idle time: a longer gap without a packet
// begins a new stream in both, so a u2ps default cannot move the line
// between a hole and a pause
_Static_assert(U2PS_GAP_PAUSE == STREAM_IDLE_MS * 8u, "u2ps's pause (U2PS_GAP_PAUSE) must be STREAM_IDLE_MS");

#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

// How long the device stays disconnected when its formats change, so the
// host notices
#define RECONNECT_MS 300u

// How often the main loop reads u2ps's counters, besides after a stream
#define COUNTERS_MS 1000u

// How long a stream start waits for the packets of the stream before it to
// be read (stream_ending()), counted from the host's SETUP: two thirds of
// the time u2ps holds SET_INTERFACE's status for it (U2PS_START_MAX, which
// counts from the first tick after the SETUP), so that a pass of the main
// loop still fits, and the host, which allows 50 ms from the SETUP (USB 2.0
// 9.2.6.4), gets its answer by then while the passes stay short
#define START_CHECK_MS (U2PS_START_MAX * 2u / 3u)

//--------------------------------------------------------------------+
// Class memory and the interrupt's state
//--------------------------------------------------------------------+

// The stream's buffers, one packet each, which the main loop reads in place
// and gives back in order (stream.c): as many as CLASS_MEM_BYTES (the
// family's, family.cmake) holds at the stream's stride, so formats with
// smaller packets get more. A buffer holds one microframe, 125 us, at every
// rate. In memory the OTG HS DMA reaches (FAMILY_CLASS_MEM, family.h).
static _Alignas(U2PS_DPORT_ALIGN) uint8_t class_mem[CLASS_MEM_BYTES] FAMILY_CLASS_MEM;

// The port keeps U2PS_DPORT_ISO_OUT_RESERVE of the buffers armed, so the
// main loop can hold all but those. The largest packets (32/4 at 768 kHz)
// get CLASS_MEM_BUFFERS_MIN buffers that it can hold; the smallest strides
// (16/2) get the most, which u2ps must allow
#define STRIDE(_slot) ((uint32_t) U2PS_DPORT_STRIDE(UAC2_EP_OUT_SZ(_slot)))
_Static_assert(CLASS_MEM_BYTES / STRIDE(4) - U2PS_DPORT_ISO_OUT_RESERVE >= CLASS_MEM_BUFFERS_MIN,
               "too little class memory");
_Static_assert(USB_BUFFERS_MAX == CLASS_MEM_BYTES / STRIDE(2), "the 16/2 stride is the smallest");
_Static_assert(USB_BUFFERS_MAX <= U2PS_ISO_OUT_COUNT_MAX,
               "U2PS_ISO_OUT_COUNT_MAX (family.cmake) must allow every buffer of the 16/2 stride");

static volatile uint32_t isr_stream;   // the open stream's number, 0 for none
static volatile uint32_t isr_packets;  // packets u2ps delivered, zero-length ones included
static volatile uint32_t isr_sent;     // hole microframes in which the host sent (not NO_PACKET)

//--------------------------------------------------------------------+
// Events from the callbacks to the main loop
//--------------------------------------------------------------------+

typedef enum {
  EV_RATE,        // value: the rate to set; id: the request to answer
  EV_RATE_BAD,    // value: a rate asked for that is not in the list (refused)
  EV_CONFIGURED,  // value: the speed it entered at, 0 when it left; why
  EV_START,       // a deferred stream start: info, cfg; id: board_millis() at it
  EV_ALT0,        // SET_INTERFACE alternate setting 0 of the streaming interface
  EV_REFUSED,     // setup: a class request to an entity that the stack refused
  EV_STOP,        // a stream stopped
  EV_SET_CONFIG,  // SET_CONFIGURATION to a configuration, which sets every interface to alt 0
  EV_RESET,
  EV_SUSPEND,
  EV_RESUME,
  EV_FAULT        // value: the fault; id: the port's reason
} ev_type_t;

typedef struct {
  uint8_t type;
  uint32_t value;
  uint32_t id;
  u2ps_config_end_t why;
  u2ps_setup_t setup;
  u2ps_stream_info_t info;
  u2ps_stream_cfg_t cfg;
} event_t;

// Written by the callbacks, which never nest, read by the main loop
#define EVENTS 32u
static event_t events[EVENTS];
static volatile uint32_t ev_head;  // the callbacks'
static volatile uint32_t ev_tail;  // the main loop's
static volatile uint32_t ev_lost;

static FAMILY_ISR_CODE event_t *ev_put(ev_type_t type, uint32_t value) {
  uint32_t head = ev_head;
  if ((head + 1u) % EVENTS == ev_tail) {
    ev_lost++;
    return NULL;
  }
  events[head].type = (uint8_t) type;
  events[head].value = value;
  return &events[head];
}

static FAMILY_ISR_CODE void ev_commit(void) {
  atomic_signal_fence(memory_order_release);  // the event before the head
  ev_head = (ev_head + 1u) % EVENTS;
}

static FAMILY_ISR_CODE void ev_simple(ev_type_t type, uint32_t value) {
  if (ev_put(type, value) != NULL) {
    ev_commit();
  }
}

//--------------------------------------------------------------------+
// u2ps app callbacks
//--------------------------------------------------------------------+

// Those that u2ps calls in the USB interrupt, and the event queue's side
// they use, run from ITCM RAM where the family has it (FAMILY_ISR_CODE,
// family.h); u2ps_app_on_vbus() is called from the main loop only.

// No vendor requests
FAMILY_ISR_CODE int u2ps_app_on_request(const u2ps_request_t *req) {
  (void) req;
  return U2PS_REQ_STALL;
}

// Only the listed rates: a rate not in the list is refused, not taken as
// the closest one. A listed one is answered from the main loop once the
// stream has taken it (the stack then sets the nominal feedback), so u2ps
// takes no rate the stream has not.
FAMILY_ISR_CODE int u2ps_app_on_sampling_frequency(uint32_t requested, uint32_t chosen, uint32_t id) {
  if (requested != chosen) {
    ev_simple(EV_RATE_BAD, requested);
    return U2PS_REQ_STALL;
  }
  event_t *e = ev_put(EV_RATE, chosen);
  if (e == NULL) {
    return U2PS_REQ_STALL;  // no room to defer: refused, a lost event (counters_task())
  }
  e->id = id;
  ev_commit();
  return U2PS_REQ_DEFER;
}

FAMILY_ISR_CODE void u2ps_app_on_configured(bool configured, u2ps_config_end_t why) {
  event_t *e = ev_put(EV_CONFIGURED, configured ? (uint32_t) u2ps_dev_speed() : 0u);
  if (e != NULL) {
    e->why = why;
    ev_commit();
  }
}

// Opened by the main loop (u2ps_dev_stream_open()) once the stream module
// has taken the new format; the feedback stays the nominal value the stack
// filled in: there is no audio clock to follow
FAMILY_ISR_CODE u2ps_stream_start_t u2ps_app_on_stream_start(const u2ps_stream_info_t *info, u2ps_stream_cfg_t *cfg) {
  event_t *e = ev_put(EV_START, info->alternate_setting);
  if (e == NULL) {
    return U2PS_STREAM_REFUSE;
  }
  uint32_t stride = U2PS_DPORT_STRIDE(info->max_packet_size);
  uint32_t count = CLASS_MEM_BYTES / stride;
  e->id = board_millis();  // the SETUP's time: the host's 50 ms count from it
  e->info = *info;
  e->cfg = *cfg;
  e->cfg.mem = class_mem;
  e->cfg.stride = (uint16_t) stride;
  e->cfg.count = (uint16_t) count;  // at most USB_BUFFERS_MAX
  ev_commit();
  return U2PS_STREAM_DEFER;
}

FAMILY_ISR_CODE void u2ps_app_on_packet(const uint8_t *data, uint16_t len, u2ps_uframe_t uframe) {
  isr_packets++;
  stream_packet_isr(data, len, uframe);
}

// on_hole and on_pause also run in the main loop with the USB vector
// masked: at each tick of a long gap, for the holes so far
// (u2ps_dev_tick() in uac2_task()), and for a stream's tail inside
// u2ps_dev_connect(false), a re-init or a VBUS loss (uac2_pullup(),
// stack_init(), uac2_unplug()), always before on_stream_stop. None of those
// calls comes from inside stream.c, and stream.c reads what these write
// only with interrupts off.
FAMILY_ISR_CODE void u2ps_app_on_hole(uint16_t uframes, u2ps_miss_t why) {
  if (why != U2PS_MISS_NO_PACKET) {
    isr_sent += uframes;
  }
  stream_hole_isr(uframes, (unsigned) why);
}

FAMILY_ISR_CODE void u2ps_app_on_pause(uint16_t uframes) {
  (void) uframes;
  stream_restart_isr();
}

// u2ps has the stream's buffers back: the packets the main loop has not
// read yet are not given back (uac2_release())
FAMILY_ISR_CODE void u2ps_app_on_stream_stop(void) {
  isr_stream = 0;
  ev_simple(EV_STOP, 0);
}

FAMILY_ISR_CODE void u2ps_app_on_fault(u2ps_fault_t fault) {
  event_t *e = ev_put(EV_FAULT, (uint32_t) fault);
  if (e != NULL) {
#ifdef PORT_DWC2
    e->id = (uint32_t) u2ps_dwc2_dcd_open_error();
#else
    e->id = 0;
#endif
    ev_commit();
  }
}

FAMILY_ISR_CODE void u2ps_app_on_reset(void) {
  ev_simple(EV_RESET, 0);
}

FAMILY_ISR_CODE void u2ps_app_on_suspend(void) {
  ev_simple(EV_SUSPEND, 0);
}

FAMILY_ISR_CODE void u2ps_app_on_resume(void) {
  ev_simple(EV_RESUME, 0);
}

// The loss itself is handled where it is reported (uac2_unplug())
void u2ps_app_on_vbus(bool present) {
  (void) present;
}

#define REQ_TYPE_MASK   0x60u
#define REQ_TYPE_STD    0x00u
#define REQ_TYPE_CLASS  0x20u
#define REQ_RECIP_MASK  0x1Fu
#define REQ_RECIP_ITF   0x01u
#define REQ_RECIP_DEV   0x00u
#define SET_CONFIGURATION 9u
#define SET_INTERFACE   11u

// What the log names from the control path besides the stream starts:
// SET_INTERFACE alternate setting 0 of the streaming interface (also without
// a stream, each class request to an entity that nothing answered, and
// SET_CONFIGURATION to a configuration, which stops the stream with no
// on_configured() call while the device is configured (u2ps_dev.h)
FAMILY_ISR_CODE void u2ps_app_on_control(const u2ps_setup_t *setup, int result) {
  uint8_t type = setup->bmRequestType & REQ_TYPE_MASK;
  if ((setup->bmRequestType & REQ_RECIP_MASK) == REQ_RECIP_DEV) {
    if (type == REQ_TYPE_STD && setup->bRequest == SET_CONFIGURATION && result >= 0 && setup->wValue != 0u) {
      ev_simple(EV_SET_CONFIG, 0);
    }
    return;
  }
  if ((setup->bmRequestType & REQ_RECIP_MASK) != REQ_RECIP_ITF) {
    return;
  }
  if (type == REQ_TYPE_STD && setup->bRequest == SET_INTERFACE && result >= 0
      && (setup->wIndex & 0xFFu) == ITF_NUM_AUDIO_STREAMING && (setup->wValue & 0xFFu) == 0u) {
    ev_simple(EV_ALT0, 0);
  } else if (type == REQ_TYPE_CLASS && result == U2PS_REQ_STALL && (setup->wIndex >> 8) != 0u) {
    event_t *e = ev_put(EV_REFUSED, 0);
    if (e != NULL) {
      e->setup = *setup;
      ev_commit();
    }
  }
}

//--------------------------------------------------------------------+
// Names for the log
//--------------------------------------------------------------------+

static const char *name(const char *const *names, size_t n, unsigned i) {
  return i < n ? names[i] : "unknown";
}

// u2ps_dev_err_t
static const char *const init_errors[] = {
  "ok", "description", "clock", "power", "security", "phy", "reset", "mode", "memory", "port", "dma_memory", "timer",
};

// u2ps_dev_description_error_t
static const char *const description_errors[] = {
  "ok", "null", "formats", "format_count", "sampling_frequencies", "sampling_frequency_count",
  "nr_channels", "max_power_ma", "self_powered", "manufacturer", "product", "serial_number",
  "channel_config", "terminal_type", "sampling_frequency_order", "initial_sampling_frequency",
  "subslot_size", "bit_resolution", "audio_data_format", "max_packet_size", "configuration_size",
};

// u2ps_open_result_t
static const char *const open_results[] = {"done", "not_pending", "refused", "fault"};

// One name per value up to each enum's last one as named here: a value
// u2ps inserts or removes before it moves that one's number and fails the
// build; a value appended after it does not, and logs as "unknown" (name())
_Static_assert(ARRAY_SIZE(init_errors) == U2PS_DEV_ERR_TIMER + 1u, "a name for each u2ps_dev_err_t");
_Static_assert(ARRAY_SIZE(description_errors) == U2PS_DESCRIPTION_CONFIGURATION_SIZE + 1u,
               "a name for each u2ps_dev_description_error_t");
_Static_assert(ARRAY_SIZE(open_results) == U2PS_OPEN_FAULT + 1u, "a name for each u2ps_open_result_t");

#ifdef PORT_DWC2
// u2ps_dwc2_err_t
static const char *const port_init_errors[] = {"ok", "clock", "power", "tzen", "phy", "reset", "mode", "fifo", "cycles"};

// u2ps_dwc2_open_err_t
static const char *const port_open_errors[] = {
  "ok", "count", "mem_null", "stride_short", "stride_align", "mem_align", "not_dma", "stream",
};

_Static_assert(ARRAY_SIZE(port_init_errors) == U2PS_DWC2_ERR_CYCLES + 1u, "a name for each u2ps_dwc2_err_t");
_Static_assert(ARRAY_SIZE(port_open_errors) == U2PS_DWC2_OPEN_STREAM + 1u, "a name for each u2ps_dwc2_open_err_t");
#endif

static const char *port_init_error(void) {
#ifdef PORT_DWC2
  return name(port_init_errors, ARRAY_SIZE(port_init_errors), (unsigned) u2ps_dwc2_dcd_init_error());
#else
  return "none";
#endif
}

static const char *port_open_error(uint32_t reason) {
#ifdef PORT_DWC2
  return name(port_open_errors, ARRAY_SIZE(port_open_errors), reason);
#else
  (void) reason;
  return "none";
#endif
}

//--------------------------------------------------------------------+
// Main loop
//--------------------------------------------------------------------+

static bool started;     // u2ps_dev_init() succeeded once
static bool mounted;     // configured, as the last event said
static bool quiet;       // a detach the caller logs itself (a pull, a formats change)
static bool counters_due;
static uint32_t quiet_packets;  // isr_packets as the last tick before a new one saw them
static uint32_t quiet_start;    // that tick's time
static uint32_t quiet_sent;     // isr_sent then
static bool long_gap;           // u2ps's long gap, as the last tick saw it
static bool alt0_due;           // alt 0 for the stream, once u2ps has stopped it (EV_STOP)
static uint32_t counters_ms;
static u2ps_dev_stats_t counters;   // as last logged
static uint32_t counters_ev_lost;

// The USB fault: every LED on (leds.h)
static void fault(void) {
  if (!status.usb_fault) {
    status.usb_fault = true;
    status_changed();
  }
}

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
  stream_set_rate(USB_START_RATE);
}

// u2ps_dev_init() with a description of the formats in uac2_alts: the
// start, or a re-init while detached
static bool stack_init(void) {
  u2ps_dev_err_t e = u2ps_dev_init(usb_description());
  if (e != U2PS_DEV_OK) {
    log_line("usb init failed reason=%s description=%s port=%s", name(init_errors, ARRAY_SIZE(init_errors), e),
             name(description_errors, ARRAY_SIZE(description_errors), u2ps_dev_description_error()),
             port_init_error());
    fault();
    return false;
  }
  if (!started) {
    family_usb_irq_start();
    started = true;
  }
  return true;
}

// The attach is allowed from the start; the pull-up comes on once
// uac2_vbus() reports VBUS (vbus_start() may end the permission again)
void uac2_start(void) {
  if (stack_init()) {
    uac2_pullup(true);
  }
}

void uac2_pullup(bool on) {
  if (started) {
    u2ps_dev_connect(on);
  }
}

void uac2_vbus(bool present) {
  u2ps_dev_vbus(present);
}

// With interrupts off (stream.c): the open stream cannot stop meanwhile
void uac2_release(uint32_t n) {
  if (isr_stream != 0u) {
    u2ps_dev_release(isr_stream, (uint16_t) n);
  }
}

// u2ps's counters since boot, logged when one changed; a lost event is a
// fault. Lost events are the event queue's and the segment starts the
// stream's interrupt dropped (stream_lost_starts()). Not logged: corrupted,
// always 0 on the DWC2 port, whose core drops a CRC-error packet unseen
// (u2ps_dwc2_dcd.c, u2ps_dport_stats()).
static void counters_task(uint32_t now) {
  if (!counters_due && now - counters_ms < COUNTERS_MS) {
    return;
  }
  counters_due = false;
  counters_ms = now;
  u2ps_dev_stats_t st;
  u2ps_dev_stats(&st);
  uint32_t lost = ev_lost + stream_lost_starts();
  if (memcmp(&st, &counters, sizeof(st)) == 0 && lost == counters_ev_lost) {
    return;
  }
  counters = st;
  counters_ev_lost = lost;
  log_line("usb counters late_completions=%lu late=%lu no_buffer=%lu fb_recoveries=%lu errors=%lu "
           "release_errors=%lu uframe_back=%lu ev_lost=%lu",
           (unsigned long) st.late_completions, (unsigned long) st.late, (unsigned long) st.no_buffer,
           (unsigned long) st.fb_recoveries, (unsigned long) st.errors,
           (unsigned long) st.release_errors, (unsigned long) st.uframe_back, (unsigned long) lost);
  if (lost != 0u) {
    fault();
  }
}

// A deferred stream start: the new format is set, which ends the old
// stream and makes the first packet begin a new segment. The open waits
// while packets of the old stream are still being read, at most
// START_CHECK_MS; then the old stream is cut, and the class memory is lent
// again.
static event_t pending;        // the start that waits
static bool pending_due;
static uint32_t pending_ms;    // the SETUP's time (board_millis() in the interrupt)

static void start(const event_t *e) {
  stream_set_alt(e->info.alternate_setting);
  pending = *e;
  pending_due = true;
  pending_ms = e->id;
}

static void start_task(uint32_t now) {
  if (!pending_due || (stream_ending() && (int32_t) (now - pending_ms) < (int32_t) START_CHECK_MS)) {
    return;
  }
  pending_due = false;
  stream_cut();
  if (!stream_queue_empty()) {
    fault();  // a packet still queued would point into memory lent again
    log_line("usb fault queue not empty dropped=%lu", (unsigned long) stream_drop());
  }
  isr_stream = pending.info.stream;
  u2ps_open_result_t r = u2ps_dev_stream_open(pending.info.stream, &pending.cfg);
  if (r != U2PS_OPEN_DONE) {
    isr_stream = 0;
    log_line("usb fault start result=%s", name(open_results, ARRAY_SIZE(open_results), r));
  }
}

// The stream ends (its packets not read yet are still checked,
// stream_reset()), and a start that waits is no longer pending in u2ps
static void reset_stream(void) {
  pending_due = false;
  alt0_due = false;
  stream_reset();
}

// The host set alt 0, or SET_CONFIGURATION, which sets it: a start not yet
// opened is cancelled in u2ps. An open stream ends once u2ps has stopped it
// (EV_STOP), since its close may take a while (DWC2) and the stream's tail
// holes, overflows among them, come before on_stream_stop: they count in
// the stream, and its file is OVERRUN.
static void alt0(void) {
  pending_due = false;
  if (isr_stream != 0u) {
    alt0_due = true;
  } else {
    stream_set_alt(0);
  }
}

// SET_CONFIGURATION sets every interface to alt 0 (USB 2.0 9.1.1.5): as
// alt 0, but not at the enumeration's first one, when no alt setting is
// open
static void set_configuration(void) {
  if (status.alt != 0) {
    alt0();
  } else {
    pending_due = false;
  }
}

// The configuration ended
static void unconfigured(void) {
  mounted = false;
  if (status.usb != USB_NOT_MOUNTED) {
    status.usb = USB_NOT_MOUNTED;
    status_changed();
    if (!quiet) {
      log_line("usb disconnected");
    }
  }
}

// The device is no longer set up, and the stream ends with it
static void unmounted(void) {
  reset_stream();
  unconfigured();
}


// A deferred change of the sampling frequency: the request is answered,
// and the stream takes the rate (the next packet begins a new segment;
// stream.c reads the old rate's packets over the next passes) with
// interrupts off, so no packet comes between, and the host sends the new
// rate's packets only after. A request no longer pending (a new SETUP, a
// bus reset, a detach, a VBUS loss or a re-init cancelled it) leaves u2ps
// and the stream at the old rate: no boundary, no `rate` line.
static void rate(const event_t *e) {
  board_irq_disable();
  bool taken = u2ps_dev_reply(e->id, NULL, 0);
  if (taken) {
    stream_rate_boundary(e->value);
  }
  board_irq_enable();
  if (taken) {
    stream_rate_taken(e->value);
  }
}

static void handle(const event_t *e) {
  switch (e->type) {
    case EV_RATE:
      rate(e);
      break;
    case EV_RATE_BAD:
      log_line("rate unsupported=%lu", (unsigned long) e->value);
      break;
    case EV_REFUSED:
      log_line("usb unsupported %s entity=%u ctrl=%u req=%u", (e->setup.bmRequestType & 0x80u) ? "get" : "set",
               e->setup.wIndex >> 8, e->setup.wValue >> 8, e->setup.bRequest);
      break;
    case EV_CONFIGURED:
      if (e->value != 0u) {
        mounted = true;
        status.usb = USB_MOUNTED;
        status.high_speed = e->value == U2PS_SPEED_HIGH;
        status_changed();
        log_line("usb mounted speed=%s", status.high_speed ? "high" : "full");
      } else if (e->why == U2PS_CONFIG_END_SET_CONFIGURATION) {
        // SET_CONFIGURATION 0: u2ps closes the stream after it, and the
        // stream's tail comes before on_stream_stop (u2ps_dev.h), so it
        // ends as at alt 0; `usb disconnected` first, in u2ps's order,
        // also when the close has already ended
        unconfigured();
        set_configuration();
      } else if (e->why != U2PS_CONFIG_END_BUS_RESET) {
        // A detach, VBUS or a re-init; a bus reset is EV_RESET's
        unmounted();
      }
      break;
    case EV_START:
      start(e);
      break;
    case EV_ALT0:
      alt0();
      break;
    case EV_SET_CONFIG:
      set_configuration();
      break;
    case EV_STOP:
      counters_due = true;
      if (alt0_due) {
        alt0_due = false;
        stream_set_alt(0);
      }
      break;
    case EV_RESET:
      // The stream and the configuration have ended; logged when there was
      // one or an alt setting, not at the first reset of an enumeration
      mounted = false;
      counters_due = true;
      if (status.streaming || status.alt != 0 || status.usb == USB_MOUNTED) {
        reset_stream();
        status.usb = USB_NOT_MOUNTED;
        status_changed();
        log_line("usb reset");
      }
      break;
    case EV_SUSPEND:
      status.usb = USB_SUSPENDED;
      status_changed();
      log_line("usb suspended");
      break;
    case EV_RESUME:
      status.usb = mounted ? USB_MOUNTED : USB_NOT_MOUNTED;
      status_changed();
      log_line("usb resumed");
      break;
    case EV_FAULT:
      log_line("usb fault open reason=%s", port_open_error(e->id));
      fault();
      break;
    default:
      break;
  }
}

static void events_task(void) {
  while (ev_tail != ev_head) {
    atomic_signal_fence(memory_order_acquire);  // the head before the event
    uint32_t tail = ev_tail;
    event_t e = events[tail];
    atomic_signal_fence(memory_order_release);  // the event read before its slot is free
    ev_tail = (tail + 1u) % EVENTS;
    handle(&e);
  }
}

void uac2_task(void) {
  if (!started) {
    return;
  }
  uint32_t now = board_millis();
  // u2ps's long gap counts from the first tick that saw no new packet. The
  // packets are counted before the tick and the gap judged after it, with
  // the same time, so it can only start later here and end no sooner than
  // u2ps's: when quiet, u2ps has flushed the gap's holes in this tick. As
  // for u2ps's pause (u2ps_app_on_pause()), the microframes of the holes in
  // which the host sent (LATE, NO_BUFFER, CORRUPTED), which the ticks of a
  // long gap report, this tick's included, are no part of the gap
  uint32_t seen = isr_packets;
  u2ps_fault_t f = u2ps_dev_tick(now);
  if (seen != quiet_packets) {
    quiet_packets = seen;
    quiet_start = now;
    quiet_sent = isr_sent;
  }
  long_gap = (uint64_t) (now - quiet_start) * 8u > U2PS_GAP_PAUSE + (uint64_t) (isr_sent - quiet_sent);
  if (f == U2PS_FAULT_CLOSE) {
    log_line("usb fault close");
  } else if (f == U2PS_FAULT_START) {
    // u2ps completed SET_INTERFACE without the open, and the packets the
    // host sent before the open are lost unreported
    fault();
    log_line("usb fault start timeout");
  }
  events_task();
  counters_task(now);
  start_task(now);
}

bool uac2_quiet(void) {
  return long_gap;
}

// VBUS has gone (vbus.c, debounced): the stack ends the stream and the
// configuration at once, and the packets not read yet are read now, so
// their lines come first; the caller logs the disconnect and starts a new
// run, then ends the attach
void uac2_unplug(void) {
  quiet = true;
  u2ps_dev_vbus(false);
  events_task();
  unmounted();
  stream_flush();
  quiet = false;
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
  // (vbus.h: the pull-up comes on again RECONNECT_MS later, once VBUS is
  // there). u2ps takes a new description only by a re-init, while detached;
  // the clock keeps its rate.
  quiet = true;
  vbus_hold_pullup(RECONNECT_MS);
  uac2_pullup(false);
  events_task();
  reset_stream();
  stream_flush();  // the stream's lines before the formats'
  status.usb = USB_NOT_MOUNTED;
  status_changed();
  mounted = false;
  usb_formats_set(alts, n);
  (void) stack_init();
  events_task();
  quiet = false;
  log_formats(" changed=1");
  log_line("usb disconnected");
  return true;
}

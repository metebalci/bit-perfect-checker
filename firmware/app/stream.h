// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Stream probe and BPC1 checker: records what the host chose (rate, alt
// setting), what arrived (packets, frames, the bits in use, DoP markers) and
// what was lost (microframes without a packet, packets the device had no
// buffer for: overflows), checks every frame against the BPC1 test signal,
// and logs it.
//
// A stream starts with the first packet after an alt setting is selected or
// after a pause, and stops when the alt setting changes, the device is reset,
// or no packet arrives for STREAM_IDLE_MS. Each file the checker finds, and
// each stretch of the stream outside every file, gets a "result" line for
// test automation.
//
// A run is every result since the user button, the "mark" command or reset;
// the LEDs show its results.

#ifndef STREAM_H
#define STREAM_H

#include <stdbool.h>
#include <stdint.h>

#define STREAM_IDLE_MS 50

// From the USB control path (main loop context). stream_set_rate(): the
// host's rate, as at the start; at a change the glue calls its two parts
// itself: stream_rate_boundary() with interrupts off, in the stretch that
// answers the host, then stream_rate_taken(), which logs it.
void stream_set_rate(uint32_t rate);
void stream_rate_boundary(uint32_t rate);
void stream_rate_taken(uint32_t rate);
// stream_set_alt(): the host selected an alt setting, so a stream that ran,
// and the segments of it still queued, end once their packets are read
// (stream_ending(): packets wait). stream_reset(): the same for a bus
// reset, a detach, a VBUS loss or a formats change. stream_cut(): before
// the class memory is lent again, they end now, each segment as a stream of
// its own, and their packets not read yet count as overflows; then the
// queue is empty (stream_queue_empty()). stream_flush(): after
// stream_reset(), they are read and stop now, with their lines.
void stream_set_alt(uint8_t alt);
bool stream_ending(void);
void stream_cut(void);
void stream_flush(void);
// Every queued packet goes unread and uncounted; returns how many went
uint32_t stream_drop(void);
// The last result of the stream that stopped last held the stream's end;
// if not, what came after the units it was told of is on the stop line
// only (bpc1_checker_end_held())
bool stream_end_held(void);
// Its lost microframes before its first audio packet: on its stop line
// only, no result's
int64_t stream_lead_lost(void);
bool stream_queue_empty(void);
void stream_reset(void);

// From the USB interrupt (u2ps's callbacks, app/u2ps_glue.c):
//   stream_packet_isr()   each packet, zero-length ones included, in the
//                         buffer u2ps lent it, with its microframe: the main
//                         loop reads it there and gives the buffer back
//                         (uac2_release())
//   stream_hole_isr()     microframes without a packet before the next
//                         packet, or after a stream's last one (its tail,
//                         before u2ps stops the stream), and why
//                         (u2ps_miss_t)
//   stream_restart_isr()  a pause: the next packet begins a new segment
void stream_packet_isr(const uint8_t *data, uint16_t n_bytes, uint32_t uframe);
void stream_hole_isr(uint32_t uframes, unsigned why);
void stream_restart_isr(void);

// From the main loop: read the packets, probe the samples, log.
void stream_task(void);

// Log the last result line again ("result none" before the first).
void stream_log_result(void);

// A new run: clears the run's results and logs "run start", after the line
// of a result taken before it if that line still waits.
void stream_run_start(void);

// Audio received and not read yet, in microseconds; 0 outside a stream.
uint32_t stream_backlog_us(void);

// Segment starts the interrupt dropped, since boot: a pause, rate or alt
// boundary while the main loop was too many segments behind to keep
// another's counters. The glue counts them as lost events, a fault.
uint32_t stream_lost_starts(void);

// The packets the interrupt counted, the low 32 bits: it moves while audio
// arrives (it starts over with each segment of a stream).
uint32_t stream_packets(void);

#endif

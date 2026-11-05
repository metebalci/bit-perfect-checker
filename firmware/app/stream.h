// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mete Balci
// Stream probe and BPC1 checker: records what the host chose (rate, alt
// setting), what arrived (packets, frames, the bits in use, DoP markers) and
// what was lost (microframes without a packet, receive FIFO overflows),
// checks every frame against the BPC1 test signal, and logs it.
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

#include <stdint.h>

#define STREAM_IDLE_MS 50

// From the USB control path (main loop context).
void stream_set_rate(uint32_t rate);
void stream_set_alt(uint8_t alt);
void stream_reset(void);

// From the USB interrupt, once per received audio packet.
void stream_packet_isr(uint16_t n_bytes);

// From the main loop: drain the receive FIFO, probe the samples, log.
void stream_task(void);

// Log the last result line again ("result none" before the first).
void stream_log_result(void);

// A new run: clears the run's results and logs "run start", after the line
// of a result taken before it if that line still waits.
void stream_run_start(void);

// Audio waiting in the receive FIFO, in microseconds; 0 outside a stream.
uint32_t stream_backlog_us(void);

// The packets the interrupt counted, the low 32 bits: it moves while audio
// arrives (it starts over with each segment of a stream).
uint32_t stream_packets(void);

#endif

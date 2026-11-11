# Measurements

What was measured with the hardware checker on real hosts, and what it means
for anyone who tests with it or extends it. The design and its reasons are in
[design.md](design.md); the device's log, commands and LEDs are in the
[firmware README](../firmware/README.md); the signal is in [bpc1.md](bpc1.md).

The figures come from the device's serial log. Each one names the firmware
stage it was made with, because the device changed during the work:

| Stage | What the device did |
|---|---|
| First receive path | counted packets and frames and had no checker; its `result` line said `UNCHECKED` |
| With the checker | gave a verdict per file |
| 1.0.0 release candidate | `fw=1.0.0`, before the tag `firmware-v1.0.0` |
| After 1.0.0 | builds made after the tag `firmware-v1.0.0` (sections 2.8 and 2.9) |
| 1.1.0 | builds of the code of firmware 1.1.0: each packet counted in its own microframe, and both TinyUSB patches (sections 2.10 to 2.14) |
| 1.1.1 | firmware 1.1.1: the zero-length packet patch only, the re-arm patch off (section 7) |

Every stage here was built on TinyUSB, with the local patches each section
names. From firmware 1.2.0 the USB device stack is u2ps
([design.md](design.md#u2ps)); no figure here comes from it.

Verdicts are named as the spec names them now. Firmware 1.0.0 logged the
`OVERRUN` verdict under an earlier, longer name.

Nothing here was played from macOS, Windows or Android; only the compliance
test of section 7 ran on Windows. A bare-metal Linux
laptop was used for one comparison (section 2.12), and a bare-metal Linux PC
with an AMD controller for the check at the end of section 2.13; every other
Linux figure comes from the virtual machine of section 1.

Contents:

1. [Setups](#1-setups)
2. [Linux](#2-linux)
3. [A virtual machine with USB port passthrough](#3-a-virtual-machine-with-usb-port-passthrough)
4. [iPad](#4-ipad)
5. [Load and the receive FIFO at 768 kHz](#5-load-and-the-receive-fifo-at-768-khz)
6. [Testing on Linux](#6-testing-on-linux)
7. [USB-IF Chapter 9 tests](#7-usb-if-chapter-9-tests)

## 1. Setups

| Setup | Description |
|---|---|
| Linux, PCIe controller | A Linux virtual machine with a PCIe USB 3 card (Renesas uPD720201, driver `xhci-pci-renesas`) passed through to it. The board's user USB port was on the card, directly or behind a hub. |
| Linux, port passthrough | The same virtual machine with the board's USB port passed through by the hypervisor, which emulates an xHCI controller. |
| Linux, bare metal | A Linux laptop with an Intel xHCI controller, the board on a root port with no hub, playing with `aplay` to `hw:`. |
| Linux, bare metal (AMD) | A Linux PC with an AMD FCH xHCI controller, the board on a root port with no hub, playing to `hw:`. |
| iPad | An iPad playing the files from a player app, the board as its USB DAC. |

- **Linux playback** was raw ALSA `hw:CARD=Checker` with snd-usb-audio: no
  sound server, no mixing, no conversion. A small program that writes the
  file's frames through libasound did the playing. In the runs with the
  checker it reported no xrun in any of 660 plays.
- **Test player.** For what Linux never sends, a small libusb program
  drives the device itself (snd-usb-audio detached): zero-length packets, a
  rate change during a stream, and rate requests during a stream.
- **usbmon**, the Linux kernel's USB capture, gives the host's status of
  each isochronous packet: 0 when the host controller sent it, `-EXDEV`
  when it skipped it. With it a packet the host skipped is told apart from
  one the device missed.
- **Board.** NUCLEO-U5A5ZJ-Q, as in the firmware README. The repeats of
  section 2.8 and sections 2.9 to 2.11, 2.13 and 2.14 also used an
  STM32F723E-DISCO, and sections 2.10, 2.11, 2.13 and 2.14 an
  STM32U5G9J-DK2. The USB controller's mode is in section 2.3.
- **Test files.** The BPC1 regular set and, in the early runs, the 32-bit
  WAVs the BPC1 file generator writes on request. Streams of the early runs
  last 8.6 s (352.8 kHz and above) or 12.6 s; later files are 12.6 s at
  every rate.
- `lost` is `missed - doubled` ([firmware README](../firmware/README.md#load-figures)):
  packets missing on the bus. A *hole* is a run of microframes without a
  packet; `maxhole` is the longest. Firmware 1.1.0 counts each packet in its
  own microframe, so `doubled` is 0 and `lost` equals `missed`. Earlier
  stages read the microframe after the endpoint's re-arm, so a packet that
  completed late showed as a hole of one microframe plus a doubled packet,
  which cancel in `lost`; their `holes` include such pairs.

## 2. Linux

### 2.1 What arrives

First receive path, 33 files (30 PCM WAVs in 16, 24 and 32 bit at all ten
rates, and three DoP files), board behind a hub on the PCIe controller:

- Every rate, alt setting and bit depth arrived as expected. Linux used alt 1
  for 16-bit files, alt 4 (24-bit in 3-byte slots) for 24-bit files and alt 2
  for 32-bit files. It never used alt 3 (design.md, section 6).
- DoP arrived with its markers at all three DSD rates.
- No receive FIFO overflow in any stream.
- Up to 192 kHz no packet was lost in any of the 19 streams. This held in
  the later matrices on the PCIe controller too (section 2.2).

### 2.2 Lost packets at 705.6 and 768 kHz

At 705.6 and 768 kHz, streams lost a few packets each, in holes of one or two
microframes. A stream of 8.6 s holds about 69,000 packets. Lost packets per
stream, first receive path, DMA mode, with the file at 16-bit, 24-bit in
3-byte slots (alt 4) and 32-bit (alt 2):

| Run | 705.6 kHz 16/2 | 705.6 kHz 24/3 | 705.6 kHz 24/4 | 768 kHz 16/2 | 768 kHz 24/3 | 768 kHz 24/4 |
|---|---|---|---|---|---|---|
| Behind a hub | 2 | 7 | 15 | 2 | 11 | 15 |
| Direct on the card | 0 | 6 | 17 | 0 | 11 | 12 |
| Direct, with an earlier TinyUSB re-arm change (section 2.3) | 0 | 2 | 4 | 0 | 12 | 10 |
| As the line above, feedback endpoint polled every microframe instead of every 8 | 0 | 2 | 6 | 4 | 4 | 6 |
| Direct, a later run | 0 | 6 | 12 | 0 | 4 | 18 |

In the last run the 24/3 and 24/4 streams at 705.6 and 768 kHz lost 4 to 18
packets in 7 to 11 holes; `maxhole` was 2. That is about one packet in 3,800
to 17,000. A 12.6 s stream at 705.6 kHz (24/3) lost 4 packets in 12 holes
(with the counter of that stage, see section 1).

With the checker, packets were lost in the same way. Eight matrices on the
PCIe controller, each with 5 or 6 streams at 705.6 or 768 kHz:

| Matrix | Streams at 705.6 or 768 kHz with a loss | Packets lost per stream |
|---|---|---|
| 1 | 5 of 5 | 9, 2, 5, 6, 6 |
| 2 | 3 of 5 | 6, 4, 6 |
| 3 | 1 of 5 | 1 |
| 4 | 2 of 5 | 2, 1 |
| 5 | 3 of 5 | 4, 1, 1 |
| 6 | 2 of 6 | 8, 3 |
| 7 | 3 of 6 | 2, 2, 1 |
| 8 | 4 of 6 | 16, 2, 2, 2 |

- Every hole in these runs was one or two microframes long, with one exception: one
  8.6 s stream at 768 kHz lost 30 packets, with a longest hole of 14
  microframes.
- **16-bit streams are not always complete.** 16-bit streams at 705.6 and
  768 kHz lost packets in several runs: 2 packets at each rate (behind a hub),
  4 packets at 768 kHz (feedback endpoint polled every microframe), 2 and 6
  packets (matrix 1), 4 packets in one 12.6 s stream at 768 kHz of a short
  repeat test, and 1 packet (matrix 7, a 25.3 s stream at 768 kHz). The last
  run of the table above lost none at 16-bit.
- Below 705.6 kHz a few streams lost packets too: 1 to 6 packets at 352.8 kHz
  and 384 kHz in earlier runs, and one packet at 352.8 kHz (24/3) in matrix 2.
  None below 352.8 kHz.
- A hub or none, the feedback endpoint's polling interval (every microframe
  instead of every 8), and an earlier change to TinyUSB's re-arm of the
  isochronous OUT endpoint (section 2.3) made no clear difference: the counts of the table differ from
  run to run by as much as the rows differ. A repeat test (alternating 705.6
  and 768 kHz, 24/4, four times each, with the re-arm change and the
  feedback interval) lost 0 to 30 packets per stream.
- In the runs with the checker, the playback program reported no xrun in any
  play, including those with lost packets, so the missing packets are not the
  player running dry.

The iPad lost no packet at any rate (section 4), with the same files and
firmware stage. The losses therefore point to the host, not to the file or
the board alone. Section 2.9 shows that most of them were packets the host's
controller skipped, and section 2.10 that the rest came from packets that
completed late in their microframe, which the re-arm patch of firmware 1.1.0
handles.

### 2.3 DMA mode

The first Linux run on the PCIe controller used the DWC2 controller in slave
(FIFO) mode, where the CPU copies each packet in the interrupt. Of its 33
streams, 10 lost packets: 3 to 33 each at 705.6 and 768 kHz, and 1 to 4 at
352.8 and 384 kHz. The firmware uses DMA mode
(`firmware/app/tusb_config.h:15-20` at the tag `firmware-v1.0.0`), where
the controller moves the packets itself and the next transfer is armed
sooner; every other run of section 2.2 used it. The two modes were not
compared on the same streams with repeats, so a benefit of DMA mode is not
proven.

An earlier change to TinyUSB's audio driver, which re-armed the isochronous
OUT endpoint before copying the packet, was tried for the highest rates and
dropped: the iPad is clean at every rate without it, and its effect on Linux
was within the noise of the table. The re-arm patch of firmware 1.1.0 is a
different change, in the DWC2 driver (section 2.10).

### 2.4 What a lost packet does to a verdict

A packet lost inside a file's body fails the file. With the checker, in the
23 single-file streams of one matrix, 22 files were `BIT_PERFECT` and one,
the 705.6 kHz 24-bit file, was `NOT_BIT_PERFECT` with `lost=2` and a first
difference at 4.671 s. The DSD256 DoP file of the same matrix lost 2 packets
and was `BIT_PERFECT`: the verdict follows the frames, not the counts
(firmware README, "Result lines"). The second file of a 25.3 s stream of two
768 kHz 16-bit files lost one packet and was `NOT_BIT_PERFECT` (`diff=5.361`,
`lost=1`).

So a `NOT_BIT_PERFECT` with `lost` above 0 at 705.6 or 768 kHz on Linux may
come from the host's packet placement and not from the signal path.

### 2.5 The rate at enumeration

Linux sets the rate to 768000 right after the device enumerates, and tries
each alt setting once. Later it sends SET_CUR for a rate only when the rate
changes: two files of the same rate in a row (for example 44.1 kHz at 16 and
at 24 bit) give one `rate` line, not two. The device logs a `rate` line for
every SET_CUR it receives.

A host that finds the rate it wants already reported by GET_CUR need not set
it, and Linux does not. So the stream and the checker must start at the rate
GET_CUR reports (`firmware/app/uac2.c:46-48` at the tag `firmware-v1.0.0`).

### 2.6 Feedback

On firmware 1.0.0 to 1.1.1 the first feedback packet after an alt setting
was chosen was 0: the TinyUSB driver cleared the feedback value, and the
firmware set it again after the endpoints were open
(`firmware/app/uac2.c:208-219` at the tag `firmware-v1.0.0`). Linux ignores
a feedback of 0.

### 2.7 Frames beyond the file

A Linux stream carries more frames than the file: 17 to 20 ms at every rate
(956 frames for a 24-bit 48 kHz file, 798 at 44.1 kHz, 15,200 at 768 kHz).
None of them lies outside every file's extent (`outside=0`, bpc1.md, section
12.6). The figures are from the small playback program above; another player
may add another amount.

### 2.8 Repeats at 384 kHz with 4-byte slots

On 2026-11-05 the regular 384 kHz 32-bit WAV (4-byte slots, 12.6 s) was played
again and again on the Linux PCIe controller setup (section 1), to two boards:

| Board and build | Plays | Plays with lost packets | Packets lost in all |
|---|---|---|---|
| STM32F723E-DISCO, commit 14e5362 | 10 | 3 | 6 |
| NUCLEO-U5A5ZJ-Q, the build with the project's USB ID (16C0:27E0) | 9 | 2 | 2 |

Both boards lost packets in about one play in four, in holes of one or two
microframes. This agrees with the losses at 384 kHz of section 2.2: at 384 kHz
with 4-byte slots a packet goes missing now and then on this host, less often than
at 705.6 and 768 kHz.

### 2.9 Where the lost packets come from

After 1.0.0, PCIe controller setup with usbmon, 10 streams per board, five
each of a 768 kHz 32-bit and a 705.6 kHz 24-bit file. The host reported every
packet with status 0 or `-EXDEV`, and the `-EXDEV` always came two in a row.

- NUCLEO-U5A5ZJ-Q: 25 microframes missed in 15 holes. 20 were packets the
  host skipped, in 10 pairs; 5 were single packets the host sent and the
  device missed.
- STM32F723E-DISCO, with a test build that logs each packet's microframe:
  17 holes of 2 packets were host skips, and 2 holes of 1 packet were device
  misses. Each device miss followed a packet that completed late in its
  microframe, so that its re-arm ran in the next microframe (section 2.10).
  The interrupt itself was short: 5.6 to 7.8 us from its entry to the
  re-arm.

Most packets lost on this host were the host's. The device's share comes
from late completions.

### 2.10 Late completions and the re-arm patch

The endpoint holds one transfer and is re-armed from the interrupt of each
packet; the controller receives a packet only in a microframe whose parity
the re-arm sets. Unpatched, TinyUSB 0.21.0 takes that parity from the
microframe at the time of the re-arm, so a re-arm that runs into the next
microframe waits for the one after it, and the device misses the packet in
between. The re-arm patch of firmware 1.1.0
(`firmware/lib/patches/tinyusb-dwc2-iso-out-next.patch` at the tag
`firmware-v1.1.0`) targets the microframe after the packet instead, while it
has not passed. u2ps, the USB stack from firmware 1.2.0, arms by the same
rule ([design.md](design.md#u2ps)).

**When packets complete.** Test builds with a time stamp at each SOF, PCIe
controller setup: time from the SOF of a packet's microframe to the entry
of the interrupt that serves it, in us (median, 95th and 99th percentile,
maximum), and the share of packets at 100 us or later:

| Board | 192 kHz | 705.6 kHz | 768 kHz |
|---|---|---|---|
| NUCLEO-U5A5ZJ-Q | 9, 12, 13, 116; 0.018 % | 16, 20, 21, 120; 0.024 % | 21, 25, 26, 121; 0.028 % |
| STM32F723E-DISCO | 9, 12, 13, 114; 0.010 % | 16, 20, 20, 121; 0.028 % | 21, 24, 25, 120; 0.014 % |

- The late packets completed 117 to 121 us after their SOF, and their
  re-arm then ran 0 to 4.5 us into the next microframe. Without the patch
  each of these re-arms lost one packet.
- With the patch, 169 of 170 such re-arms on natural traffic received the
  next packet. One lost 2: its re-arm ran 4.0 us into the next microframe,
  after the host's packet.
- At 192 kHz the host's token came 1.6 to 5.5 us (NUCLEO-U5A5ZJ-Q) and 5 to
  10 us (STM32F723E-DISCO) after the SOF, so a re-arm that late in the
  microframe can still be in time.
- Handling the controller's incomplete isochronous OUT interrupt as well
  made it worse: that interrupt comes at 80 % of the microframe (99 to
  101 us after the SOF), took the packets that arrived later, and the device
  missed 42 to 119 packets per 4 natural streams on both boards. It is not
  used.

**Injected late re-arms.** 1.1.0 test builds (`BPC_TEST_INJECT`), 192 kHz
32-bit, every 1000th re-arm held until the next microframe began and then
for a delay, 5 streams per build (505 injections); usbmon showed no host
skip in any of them. The re-arm ended 2.1 to 2.7 us ("early") or 61.1 to
61.7 us ("late") into the next microframe. Packets lost:

| Board | Patches | Early | Late |
|---|---|---|---|
| NUCLEO-U5A5ZJ-Q | without `iso_out` | 505, one per injection | 505, one per injection |
| NUCLEO-U5A5ZJ-Q | with `iso_out` | 0, 5 of 5 files `BIT_PERFECT` | 1,008 in 504 injections, two each |
| STM32F723E-DISCO | without `iso_out` | 505, one per injection | 505, one per injection |
| STM32F723E-DISCO | with `iso_out` | 0, 5 of 5 files `BIT_PERFECT` | 1,002 in 504 injections: two each, 3 lost none |

"Without" is the build with no patch and the one with `zlp` only, which
gave the same counts; "with" is `iso_out` alone and both. No packet was
lost in these streams apart from the injections.

**Natural traffic.** 1.1.0, PCIe controller setup with usbmon, all three
boards, one run per board: blocks of 10 streams alternated between the
build with `zlp` only and the build with both patches, each block 5 times a
768 kHz 32-bit and a 705.6 kHz 24-bit file. Device misses are the packets
lost less the packets the host skipped:

| Board | Patches | Streams | Lost | Host skipped | Device misses | `BIT_PERFECT` |
|---|---|---|---|---|---|---|
| NUCLEO-U5A5ZJ-Q | `zlp` | 40 | 294 | 228 | 66 | 5 |
| NUCLEO-U5A5ZJ-Q | both | 40 | 222 | 222 | 0 | 10 |
| STM32U5G9J-DK2 | `zlp` | 20 | 77 | 60 | 17 | 3 |
| STM32U5G9J-DK2 | both | 20 | 66 | 66 | 0 | 10 |
| STM32F723E-DISCO | `zlp` | 60 | 375 | 360 | 15 | 11 |
| STM32F723E-DISCO | both | 60 | 362 | 362 | 0 | 18 |

- With both patches, `lost` equalled the packets the host skipped in every
  stream, and every hole was a host skip of 2 packets. Every device miss
  was a single packet. `doubled` and `ovf` were 0 in all 240 streams.
- If the two builds missed equally often, 0 against 66, 17 and 15 would
  have a chance of 1.4e-20, 7.6e-6 and 3.1e-5.
- Many streams still lost packets, those the host skipped. On this host
  they decide whether a 705.6 or 768 kHz file passes.

### 2.11 Zero-length packets

Linux sent no zero-length packet in any stream measured. The test player
sent the published 48 kHz 16-bit file `00001` as 24-byte packets, with a
zero-length packet after data packets 40,000, 56,000 and 72,000 (5, 7 and
9 s in, inside the file's body), then, after alt setting 0, one second of
silence (8,000 packets) in a new stream. usbmon showed all three zero-length
packets sent with status 0. 1.1.0 builds:

| Patches | Boards | Result |
|---|---|---|
| without `zlp` | no patch on all three; `iso_out` only on the NUCLEO-U5A5ZJ-Q | `NOT_BIT_PERFECT ... end=STOP diff=5.000 lost=0`, then `stream stop ... reason=idle ... pkts=40000`: the endpoint stopped at the first zero-length packet |
| with `zlp` | both patches on all three; `zlp` only on the NUCLEO-U5A5ZJ-Q | `BIT_PERFECT ... lost=3`; `stream stop ... lost=3 missed=3 holes=3 maxhole=1 doubled=0` |

The silence stream arrived whole (`pkts=8000 lost=0`) in 7 of 8 runs; in
the eighth the device received 7,999 packets (section 2.14).

### 2.12 A second Linux host

Are the late completions of section 2.10 the host's doing? The same
NUCLEO-U5A5ZJ-Q, the same test build (SOF time stamps, the unpatched re-arm
rule) and the same three files (768 kHz 32-bit, 768 kHz 24-bit, 705.6 kHz
24-bit, 5 streams each), played minutes apart from two hosts; only the
board's user USB port moved. No usbmon.

| Host | Interrupt entry after the SOF, us (median, 95th, 99th, max) | Entries at 100 us or later | Streams `BIT_PERFECT` |
|---|---|---|---|
| Linux, PCIe controller (section 1), behind a hub | 17, 22, 25, 121 | 202 of 1,517,781 (0.013 %) | 1 of 15; 0 to 21 lost per stream, `maxhole=2` |
| Linux, bare metal (section 1) | 23, 28, 32, 50 | 0 of 1,529,900 | 15 of 15, `lost=0` |

The device's interrupt latency does not depend on the host, so the late
completions come from the host side: where that host path places the OUT
transaction in the microframe. The test does not say which part of the path
does it: the virtual machine and passthrough, the controller, the hub or
the player. On the bare-metal laptop no completion came late, so the re-arm
patch arms there exactly what the unpatched driver arms. Windows and macOS
were not measured for this; the iPad never lost a packet (section 4).

TinyUSB's maintainer then reproduced the late completions on a matching
setup, a NUCLEO-U5A5 behind a hub on a uPD720201 passed through to a
virtual machine, with a wire capture, and reported in
[pull request 4444](https://github.com/hathach/tinyusb/pull/4444):

- The host's OUT tokens normally came about 7 us after the SOF, but up to
  0.05 % of them 30 us or more after it, as late as 106 us, with no other
  traffic ahead of them: late tokens from the host's periodic schedule,
  which the maintainer attributes to the virtual machine's passthrough,
  not to TinyUSB's device driver.
- The device's handler ran 2 to 4 us after each packet ended, so the late
  completions were not interrupt latency.
- In the maintainer's build the re-arm patch lost more packets, not fewer:
  30, 38 and 34 against 20, 27 and 15 per 20 s stream at 768 kHz in DMA
  mode. The pull request was closed without merging, and the patch stayed
  local to firmware 1.1.0 and 1.1.1.

The measurements of section 2.10 still stand: in firmware 1.1.0 the re-arm
ran in the transfer-complete interrupt, 0 to 4.5 us into the next
microframe after a late packet, and on the PCIe controller setup the
device's own misses went from 66, 17 and 15 to 0. The patch helps here,
but it is not a safe general change.

### 2.13 Firmware 1.1.0 on all three boards

Builds of 1.1.0 with both patches, PCIe controller setup:

- **Matrix** of the published set (20 PCM and 3 DoP WAVs, and the 7
  negative files), with usbmon. Good files `BIT_PERFECT`: NUCLEO-U5A5ZJ-Q
  23 of 23, STM32U5G9J-DK2 20 of 23, STM32F723E-DISCO 21 of 23. Each of
  the 5 others lost as many packets as the host skipped (DK2: 768 kHz
  16-bit 2, 768 kHz 24-bit 4, DSD256 DoP 4; F723: 705.6 kHz 24-bit 4,
  768 kHz 24-bit 6). Device misses: 0. Every negative file was not
  `BIT_PERFECT` on every board, and every stream's results added up to its
  `stream stop` line.
- **Port reset** 3 s into a 768 kHz 24-bit stream: `NOT_BIT_PERFECT
  end=STOP`, then `usb reset`.
- **Stall.** `stall 2000` in a 768 kHz 16-bit stream gave `OVERRUN` with
  15,916, 15,918 and 15,921 packets overflowed, and `stall 200` in a 48 kHz
  24-bit stream 683, 691 and 689 (NUCLEO, DK2, F723), each within 8 packets
  (one ms of the stall's timing) of what the 32,760-byte FIFO predicts.
- **Pause.** Ten 60 ms host pauses inside `stall 100`: ten `stream stop ...
  reason=pause` and 11 `NOT_BIT_PERFECT` results with `lost=0`, the same on
  all three boards.
- **Rate change during a stream** (test player): `SET_CUR` 96000 after
  40,000 packets of a 48 kHz 16-bit stream, then a 96 kHz file: `stream
  stop ... reason=rate`, a new stream at 96000, and the 96 kHz file
  `BIT_PERFECT` with `lost=0`, on all three boards.
- **Rate requests during a stream** (test player): a `GET_CUR` of the rate
  every 1 ms during 768 kHz 24-bit streams. Every request completed (11,676
  to 12,624 per stream), and `lost` equalled the packets the host skipped,
  as in the streams without them.
- **Cable pull** during a Linux play (STM32F723E-DISCO): the cut file's
  `result` line, `stream stop ... reason=idle`, then `usb disconnected` and
  `run start`.

Every test passed, with 0 device misses. The load figures are in section 5.
After 1.1.0 the default build went back to `zlp` only, because on a
bare-metal Linux PC with an AMD controller, as on the laptop of section
2.12, no packet completed late (the latest 75 us into the microframe), so
`iso_out` changed nothing there.

### 2.14 Packets neither received nor counted

With usbmon, the host's packets less the device's packets less `lost` is 0
or 1 in every stream with a complete capture. It is 1, one packet the host
sent with status 0 that the device neither received nor counted as lost,
in about half the streams: 99 of 203 in section 2.10 and 51 of 89 matrix
streams in section 2.13. It was seen in every build, with and without the
patches and with the counter of earlier stages alike, all on TinyUSB. It
never shows as a hole, so it is a stream's first or last packet, and no
verdict changes. Its cause is not known yet.

## 3. A virtual machine with USB port passthrough

First receive path, the same 33 files, the board's USB port passed through by
the hypervisor's emulated xHCI controller:

- 6 of 33 streams lost packets, each in one burst of 34 to 44 microframes
  (4.3 to 5.5 ms): 40, 40, 40, 36, 34 and 44
  packets.
- They were at 88.2 kHz (two streams), 96 kHz, 176.4 kHz (two) and 705.6 kHz.
  Three were 16-bit streams.
- The same 33 files through the PCIe controller lost no packet up to 192 kHz
  (section 2.1).

No stream had a FIFO overflow, and the same board and files lost no packet up
to 192 kHz through the PCIe controller, so the bursts point to the emulated
controller, not to the board. Test on bare metal or through a PCIe
controller passed through to the virtual machine, not through the
hypervisor's emulated USB. The bare-metal Linux laptop of section 2.12 was
tested for late completions only.

## 4. iPad

First receive path and later, the files played from a player on the iPad, the
board as its USB DAC:

| What | Observed |
|---|---|
| Rate | Set to each file's rate: all ten rates, no resampling. |
| Alt setting | Always alt 3 (32-bit in 4-byte slots), in every stream. |
| Bits in use | Follow the file: 16-, 24- and 32-bit content arrive zero-padded in the 32-bit slot (`depth bits=16`, `24`, `32`). |
| DoP | The markers arrived intact at all three DSD rates, and the DoP files were `BIT_PERFECT`. |
| Gapless | Files of one rate join into one stream: the three files of one rate (16, 24 and 32 bit) gave one stream, for example 39 s at 44.1 kHz, so 33 files gave 13 streams. |
| Leading zeros | About 0.2 s of zeros before the first file of a stream: 0.15 to 0.60 s, median 0.19 s, in 78 streams (the received frame where the checker found the file's first X-sync). |
| Lost packets | None. 0 in 13 of 13 streams of the first run, with 768 kHz included, and 0 in 80 of 80 streams of a run of the full set, all ten rates; no FIFO overflow in either. |
| End of a stream | When playback was paused or stopped inside a file, a full second of all-zero frames (`bits=0` in a `stat` line) arrived, and the stream stopped (`alt 0`) 0.3 and 0.8 s after it, in two cases. The file ended `NOT_BIT_PERFECT`. |

- The first run was made with the earlier TinyUSB re-arm change (section
  2.3); a repeat without it was clean too (13 streams, 0 lost, no overflow).
- With `allow_silence` off, the zeros before every file gave an
  `UNKNOWN_SIGNAL` at the start of every stream; the device defaults to on
  (design.md, section 4).
- Results that depend on a player's decoders are not USB matters; for ALAC
  see [design.md](design.md#alac).

## 5. Load and the receive FIFO at 768 kHz

The largest values of the `stat` lines (the [load
figures](../firmware/README.md#load-figures)) over the 768 kHz streams of a
run, firmware of the 1.0.0 release candidate or the build just before it.
This firmware kept the received audio in a receive FIFO in TinyUSB's audio
driver; from 1.2.0 the device checks each packet in the USB stack's buffers,
and `fifo_max` is the audio received and not read yet:

| Host and format | `isr_pct` | `isr_max` (cycles) | `ck_cpf` | `ck_pct` | `loop_max` (us) | `fifo_max` (us) |
|---|---|---|---|---|---|---|
| Linux, 16/2 (alt 1) | 7.3 | 1185 to 1193 | 66 to 67 | 31.9 to 32.1 | 358 to 364 | 375 |
| Linux, 24/3 (alt 4) | 7.9 | 1291 to 1296 | 71 to 73 | 34.1 to 35.4 | 456 to 464 | 250 to 375 |
| iPad, 32/4 (alt 3) | 8.4 | 1988 | 78 | 37.7 | 448 | 375 |

The Linux rows are the range of two runs. The CPU of the NUCLEO-U5A5ZJ-Q runs at 160 MHz.

- The longest main-loop iteration was under 0.5 ms, and the receive FIFO got
  at most 0.375 ms full. The FIFO holds 32,760 bytes
  (`firmware/app/tusb_config.h:43` at the tag `firmware-v1.0.0`), which is
  5.3 ms of audio at 768 kHz in 4-byte slots. Code added to the main loop,
  such as a display driver, must return well within that (the firmware
  README at that tag asked for about a millisecond).
- **The FIFO's time** is 32,760 bytes divided by (bytes per frame times the
  rate): 5.33 ms at 768 kHz with 4-byte slots (8 bytes per frame), 10.66 ms
  at 768 kHz with 2-byte slots, 113.75 ms at 48 kHz with 3-byte slots.
- **A stall longer than that overruns the FIFO.** The console's `stall <ms>`
  test command busy-waits in the main loop while the USB interrupt keeps
  filling the FIFO:
  - `stall 200` in a 48 kHz 24/3 stream: `overflow packets=684`; the file's
    result was `OVERRUN` with `ovf=684`; `loop_max` of that second was
    199,057 us and `fifo_max` 113,750 us, the whole FIFO.
  - `stall 50` in a 768 kHz 16/2 stream: `ovf=312`, `OVERRUN`.
  - A file in the same stream before the stall stayed `BIT_PERFECT`.

**With firmware 1.1.0.** The re-arm patch adds work to the USB interrupt.
The load suite (ten streams each of 705.6 and 768 kHz with 3- and 4-byte
slots, DSD256, 384 kHz, 16-bit streams and a stall) on the build with both
patches and, in the same run on the same board, the build with `zlp`
only. Largest `isr_max` in cycles:

| Board (CPU clock) | `zlp` only | Both patches | Change |
|---|---|---|---|
| NUCLEO-U5A5ZJ-Q (160 MHz) | 1614 | 1729 | +7.1 % |
| STM32U5G9J-DK2 (160 MHz) | 1627 | 1708 | +5.0 % |
| STM32F723E-DISCO (216 MHz) | 2106 | 2219 | +5.4 % |

- Per class of ten streams the change was +4.4 to +7.1 % on the
  NUCLEO-U5A5ZJ-Q, +3.5 to +5.0 % on the STM32U5G9J-DK2 (one class
  +13.1 %), and +5.4 to +6.6 % on the STM32F723E-DISCO: about 5 %.
  `isr_pct` rose by 0.2 to 0.4 points in every class.
- The highest is the STM32F723E-DISCO at 705.6 kHz with 24-bit samples in
  3-byte slots: 2,219 cycles, 10.3 us. The interrupt's budget there is
  11 us (2,376 cycles at 216 MHz).
- Every stream added up, and at 768 kHz `fifo_max` stayed at or under
  1,000 us.

### 5.1 The chip's errata

The errata sheet of the STM32U59x/5Ax, ES0553 (Rev 6), lists no erratum for
device revision X in the USB OTG_HS, GPDMA, ICACHE or HSE/PLL1 path the
firmware uses. Of the errata that apply to revision X, 2.2.13 (a hang when
entering low-power modes) does not apply because the firmware enters no Stop
or Standby mode, 2.2.14 (a spurious MSI PLL unlock) does not apply because
the clocks come from the HSE, and 2.16.1 (noise on a USART receive line)
affects only the console's input.

## 6. Testing on Linux

- Play to `hw:CARD=Checker` with no sound server in between (the case is
  significant). Make PipeWire or WirePlumber ignore the card so that the test
  keeps it to itself.
- Play one file per run, or a gapless playlist; the device gives one result
  per file either way. Count the results: `bpcctl run --expect <n>` fails
  when fewer than n files give `BIT_PERFECT` or `OVERRUN`, which
  catches a file that did not arrive or was muted ([README](../README.md#automate-with-bpcctl)).
- Do not test through a hypervisor's emulated USB (section 3).
- At 705.6 and 768 kHz, and now and then at 384 kHz with 4-byte slots
  (section 2.8), expect an occasional `lost` on a Linux PC. Read
  `lost` on the result line and `lost`, `missed`, `holes` and `maxhole` on
  the `stream stop` line before blaming the signal path: a `NOT_BIT_PERFECT`
  with a few holes of one or two microframes is a packet the device did not
  get (section 2.2). Play the file again; the holes differ from play to play.
  usbmon tells whether the host skipped them (`-EXDEV`, section 2.9).
- Expect about 20 ms more frames than the file on a Linux stream
  (section 2.7), and a few hundred ms of zeros before a file from an iPad
  (section 4). Both are inside what the device accepts.

## 7. USB-IF Chapter 9 tests

USB-IF's USB3CV (USB 3 Gen X Command Verifier) on Windows 11, suite
"Chapter 9 Tests [USB 2 devices]", all 36 tests, on firmware 1.1.1
(`v1.1.1-5762032`) on the NUCLEO-U5A5ZJ-Q at High-Speed: passed, 0 fails,
0 aborts. This includes the device qualifier and other-speed configuration
requests, suspend and resume, and 150 enumerations. The only warnings came
from the tool, which had no vendor name for 16C0. The Full-Speed run and the
other two boards were not tested.

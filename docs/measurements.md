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
| After 1.0.0 | builds made after the tag `firmware-v1.0.0` (section 2.8) |

Verdicts are named as the spec names them now. Firmware 1.0.0 logged the
`OVERRUN` verdict under an earlier, longer name.

Nothing here was measured on macOS, Windows or Android, or on a Linux PC
without a virtual machine.

Contents:

1. [Setups](#1-setups)
2. [Linux](#2-linux)
3. [A virtual machine with USB port passthrough](#3-a-virtual-machine-with-usb-port-passthrough)
4. [iPad](#4-ipad)
5. [Load and the receive FIFO at 768 kHz](#5-load-and-the-receive-fifo-at-768-khz)
6. [Testing on Linux](#6-testing-on-linux)

## 1. Setups

| Setup | Description |
|---|---|
| Linux, PCIe controller | A Linux virtual machine with a PCIe USB 3 card (Renesas uPD720201, driver `xhci-pci-renesas`) passed through to it. The board's user USB port was on the card, directly or behind a hub. |
| Linux, port passthrough | The same virtual machine with the board's USB port passed through by the hypervisor, which emulates an xHCI controller. |
| iPad | An iPad playing the files from a player app, the board as its USB DAC. |

- **Linux playback** was raw ALSA `hw:CARD=Checker` with snd-usb-audio: no
  sound server, no mixing, no conversion. A small program that writes the
  file's frames through libasound did the playing. In the runs with the
  checker it reported no xrun in any of 660 plays.
- **Board.** NUCLEO-U5A5ZJ-Q, as in the firmware README; the repeats of
  section 2.8 also used an STM32F723E-DISCO. The USB controller's mode is in
  section 2.3.
- **Test files.** The BPC1 regular set and, in the early runs, the 32-bit
  WAVs the generator writes on request. Streams of the early runs last 8.6 s
  (352.8 kHz and above) or 12.6 s; later files are 12.6 s at every rate.
- `lost` is `missed - doubled` ([firmware README](../firmware/README.md#load-figures)):
  packets missing on the bus. A *hole* is a run of microframes without a
  packet; `maxhole` is the longest.

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
| Direct, with a TinyUSB re-arm change | 0 | 2 | 4 | 0 | 12 | 10 |
| As the line above, feedback endpoint polled every microframe instead of every 8 | 0 | 2 | 6 | 4 | 4 | 6 |
| Direct, a later run | 0 | 6 | 12 | 0 | 4 | 18 |

In the last run the 24/3 and 24/4 streams at 705.6 and 768 kHz lost 4 to 18
packets in 7 to 11 holes; `maxhole` was 2. That is about one packet in 3,800
to 17,000. A 12.6 s stream at 705.6 kHz (24/3) lost 4 packets in 12 holes.

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
  instead of every 8), and a change to TinyUSB's re-arm of the isochronous
  OUT endpoint made no clear difference: the counts of the table differ from
  run to run by as much as the rows differ. A repeat test (alternating 705.6
  and 768 kHz, 24/4, four times each, with the re-arm change and the
  feedback interval) lost 0 to 30 packets per stream.
- In the runs with the checker, the playback program reported no xrun in any
  play, including those with lost packets, so the missing packets are not the
  player running dry.

The iPad lost no packet at any rate (section 4), with the same files and
firmware stage. The losses therefore point to where the host places packets
within the microframe, not to the file or the board alone: a limit of the host
and the controller together (design.md, section 8).

### 2.3 DMA mode

The first Linux run on the PCIe controller used the DWC2 controller in slave
(FIFO) mode, where the CPU copies each packet in the interrupt. Of its 33
streams, 10 lost packets: 3 to 33 each at 705.6 and 768 kHz, and 1 to 4 at
352.8 and 384 kHz. The firmware uses DMA mode
(`firmware/app/tusb_config.h:15-20`), where the controller moves the packets
itself and the next transfer is armed sooner; every other run of section 2.2
used it. The two modes were not compared on the same streams with repeats,
so a benefit of DMA mode is not proven.

A change to TinyUSB's re-arm of the isochronous OUT endpoint was tried for
the highest rates and dropped: the iPad is clean at every rate without it,
and its effect on Linux was within the noise of the table. (The patch the
build applies today concerns zero-length packets; see the firmware README.)

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
GET_CUR reports (`firmware/app/uac2.c:46-48`).

### 2.6 Feedback

The first feedback packet after an alt setting is chosen is 0: the TinyUSB
driver clears the feedback value, and the firmware sets it again after the
endpoints are open (`firmware/app/uac2.c:208-219`). Linux ignores a feedback
of 0.

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
hypervisor's emulated USB. No bare-metal Linux test was made.

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

- The first run was made with the TinyUSB re-arm change; a repeat without it
  was clean too (13 streams, 0 lost, no overflow).
- With `allow_silence` off, the zeros before every file gave an
  `UNKNOWN_SIGNAL` at the start of every stream; the device defaults to on
  (design.md, section 4).
- Results that depend on a player's decoders are not USB matters; for ALAC
  see [design.md](design.md#alac).

## 5. Load and the receive FIFO at 768 kHz

The largest values of the `stat` lines (the [load
figures](../firmware/README.md#load-figures)) over the 768 kHz streams of a
run, firmware of the 1.0.0 release candidate or the build just before it:

| Host and format | `isr_pct` | `isr_max` (cycles) | `ck_cpf` | `ck_pct` | `loop_max` (us) | `fifo_max` (us) |
|---|---|---|---|---|---|---|
| Linux, 16/2 (alt 1) | 7.3 | 1185 to 1193 | 66 to 67 | 31.9 to 32.1 | 358 to 364 | 375 |
| Linux, 24/3 (alt 4) | 7.9 | 1291 to 1296 | 71 to 73 | 34.1 to 35.4 | 456 to 464 | 250 to 375 |
| iPad, 32/4 (alt 3) | 8.4 | 1988 | 78 | 37.7 | 448 | 375 |

The Linux rows are the range of two runs. The CPU of the NUCLEO-U5A5ZJ-Q runs at 160 MHz.

- The longest main-loop iteration was under 0.5 ms, and the receive FIFO got
  at most 0.375 ms full. The FIFO holds 32,760 bytes
  (`firmware/app/tusb_config.h:43`), which is 5.3 ms of audio at 768 kHz in
  4-byte slots. Code added to the main loop, such as a display driver, must
  return well within that (the firmware README asks for about a millisecond).
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
- Expect about 20 ms more frames than the file on a Linux stream
  (section 2.7), and a few hundred ms of zeros before a file from an iPad
  (section 4). Both are inside what the device accepts.

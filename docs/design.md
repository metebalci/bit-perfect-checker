# Design

How Bit-Perfect Checker is built and why. The signal itself is specified in
[bpc1.md](bpc1.md); this document gives the choices around
it. How to use, build and release is in the [README](../README.md); the
device's log, commands and LEDs are in the [firmware README](../firmware/README.md);
what was measured on hardware is in [measurements.md](measurements.md).

Contents:

1. [What it proves](#1-what-it-proves)
2. [Formats and rates](#2-formats-and-rates)
3. [The signal format](#3-the-signal-format)
4. [Verdicts](#4-verdicts)
5. [Test files and releases](#5-test-files-and-releases)
6. [USB bandwidth and host behaviour](#6-usb-bandwidth-and-host-behaviour)
7. [Hardware](#7-hardware)
8. [Firmware](#8-firmware)
9. [Tested hosts](#9-tested-hosts)

## 1. What it proves

The project checks whether a playback chain (player, operating system, USB
audio stack) delivers a file bit-perfect. The user plays a downloaded test
file with any player on any operating system, and a USB Audio Class 2 sink
checks what arrives.

The checker proves that a file arrived bit-perfect. It does not say what
changed when a file did not. A pass proves that every sample arrived
unchanged. A fail almost always means a real change; rarely, a USB packet the
device missed fails a file the host sent intact. A reference software
checker in `checker/` gives an informative diagnosis of what changed; no
verdict depends on it.

## 2. Formats and rates

- **PCM:** 16-bit and 24-bit at every standard rate: 44.1, 48, 88.2, 96,
  176.4, 192, 352.8, 384, 705.6 and 768 kHz. Each file is checked against its
  own declared depth. A floating-point path is not a test goal; spotting one
  is a side effect.
- **32-bit PCM:** part of the format and of the device, not of the published
  set. No converter resolves 32 bits and a float path is not a test goal. The
  generator writes 32-bit files on request (`--32bit`). The device keeps its
  32-bit alt setting because hosts use it as the transport for 16- and 24-bit
  music.
- **DSD:** DSD64, DSD128 and DSD256. DSD512 is out of scope: over DoP it would
  need 1424 bytes per microframe, more than one transaction of a
  High-Speed isochronous endpoint can carry (section 6). DSD reaches the device as DoP (DSD
  over PCM in 24-bit words). The device has no native (raw) DSD alt setting.
- **Channels:** stereo.

## 3. The signal format

BPC1 is specified in [bpc1.md](bpc1.md), which is
self-contained and has golden test vectors (`vectors/`), so that other
implementations can follow it. The main choices:

- **Two layers in every file.** The exact layer (X-sync, walking-bit burst,
  payload) is compared frame by frame and bit by bit; the verdict uses it only.
  The robust layer (an R-header every 16th block, in large slow square-wave
  chips) survives volume change, dither, truncation, resampling and
  DSD-to-PCM conversion, and serves the diagnosis.
- **Blocks of about 90 ms at every rate.** A block starts with an X-sync that
  names the file and the block, so a receiver locks anywhere within one block.
- **A payload regenerated from the frame index.** A counter-based hash
  (lowbias32) lets a checker compute any frame directly, at two
  multiplications per sample. A 64-bit generator costs about twice as much on
  a 32-bit microcontroller.
- **Header fields in the top 16 bits** of each word keep the X-sync intact in
  16-bit files and through truncation to 16 bits.
- **A low-level payload with full-scale bursts.** The payload is at about
  -48 dBFS (-36 dBFS for 16-bit) and still exercises every bit of the file
  depth. The X-sync and the walking-bit burst are full scale and exercise the
  top bits in every block. The files are less dangerous when played by
  mistake, but they are not for speakers.
- **A 2 s lead-in, a 10 s body and a 0.5 s tail at every rate.** Only the body
  counts. The lead-in and tail absorb rate switching, fades and cuts. Every file
  proves the same 10 seconds of audio. A change that affects every sample is
  found within the first samples, so a longer body is not needed. Rare
  intermittent faults are found by playing the files continuously, for hours if
  needed, or by looping one file gaplessly (bpc1.md, section 14).
- **A code-sweep set.** A separate set of full-scale files in which each
  channel carries every code of its depth exactly once, in a scrambled
  noise-like order, so that a pass proves every code arrived unchanged. The
  regular set cannot find a fault that changes a single code. The sweep files
  are long (up to 416 s at 24-bit 44.1 kHz) and full-scale noise for their
  whole length, which is why they are a separate set. The sweep is 16- and
  24-bit at all ten rates.
- **A bijection generates the sweep** without a table or a state, so a
  checker still computes any unit directly.

## 4. Verdicts

Both checkers give one verdict per file and one per stretch of unknown
signal. Only `BIT_PERFECT` is a pass.

| Verdict | Meaning |
|---|---|
| `BIT_PERFECT` | the received rate and channel count are the file's, the whole body arrived as one run of consecutive units, every unit equals the expected one in every channel, and the X-syncs before and after the body arrived intact |
| `NOT_BIT_PERFECT` | the file was found and that does not hold |
| `UNKNOWN_SIGNAL` | audio that belongs to no file, silence included |
| `DEVICE_OVERRUN` | hardware checker only: its own receive buffer overflowed before any difference, so it could not judge the file; play it again |

The exact rules are in bpc1.md, section 12.6. The choices behind
them:

- **Prove bit-perfect; do not name the change.** The aim is to prove that a
  file arrived bit-perfect. What changed is not reported. The hardware checker
  has no robust layer and no reason checks.
- **Every body unit is compared,** X-syncs and R-header units included. A unit
  whose expected value depends on what is not known yet (the block count) is
  checked against its possible values and settled at the end, so no unit escapes
  uncompared. The R-header's count is read strictly: every chip at its centre,
  all 13 Barker chips and both chips of every bit as specified. A damaged
  R-header gives no count.
- **The body's limits must be shown.** The X-sync of the block before the body
  (lead-in) and of the block after it (tail, or the body's own LAST flag) must
  have arrived intact. If either is broken or missing the file is
  `NOT_BIT_PERFECT`: a rare false fail is chosen over a possible false pass.
- **Unknown signal is a fail.** A test run plays test files only, so audio that
  belongs to no recognised file is never ignored, even when other files of the
  run were recognised.
- **No search for inverted X-syncs.** An inverted, rounded, dithered or
  resampled stream may give `UNKNOWN_SIGNAL`.
- **DoP markers** 0x05 and 0xFA must alternate in every body frame, the same in
  all channels; either may come first.
- **Allowed silence.** `allow_silence` decides what silence outside every
  file's extent gives. Off, it is `UNKNOWN_SIGNAL`. On, it gives no result,
  wherever it is (before, between and after files). Files mark their own start
  and end, so silence outside them is told apart; silence inside a file's body
  is a difference and fails under either setting. The device defaults to on,
  because hosts pad their streams with silence: an iPad sends about 0.2 s of
  zeros before every file, which with the setting off gave an
  `UNKNOWN_SIGNAL` at every stream. The software checker defaults to off.
  The cost: a file muted entirely to zeros gives no result, so a script
  compares the number of `BIT_PERFECT` results with the files played.
- **A difference before an overrun.** A file whose first difference came before
  the hardware checker's buffer overflowed is `NOT_BIT_PERFECT`, since the
  overflow cannot undo a proven difference. `DEVICE_OVERRUN` is for an overflow
  before any difference. It is never reported as a fault of the host.
- **What a verdict detects.** Changes that players, mixers, operating systems
  and transports make, not a stream crafted to pass. The format is public and
  carries no secret, and in v1 a forged early TAIL X-sync cannot be told from a
  valid file with other section lengths (section 12.6).
- **A resampled stream or DSD received as PCM** gives no special verdict. In
  the software checker's diagnosis they are reasons (`RESAMPLED`,
  `DSD_TO_PCM`), not verdicts.

## 5. Test files and releases

### The set

- The project ships its own downloadable test files, generated by
  `python3 -m generator`.
- **Formats.** PCM (16- and 24-bit, ten rates) as WAV, FLAC, AIFF and ALAC.
  DSD64, DSD128 and DSD256 as DSF, DFF, and DoP in a 24-bit WAV; the DoP files
  also as FLAC, AIFF and ALAC. The regular set is 122 files, about 1.75 GB; the
  code-sweep set is 88 files, about 5.4 GB; the gapless set is 36 files,
  about 57 MB (bpc1.md, sections 11.1, 11.6 and 11.7).
- **Every format a player plays is tested.** A player can decode each format
  through a different path, so the WAV set is where a test starts, not where it
  ends. FLAC, AIFF and ALAC are made from the WAV of the same name and hold
  the same samples.
- **Negative files.** In a folder `negative/` of every zip, deliberately faulty
  copies show that a checker catches faults. In the regular set: PCM at 44.1
  and 48 kHz 24-bit and DSD64 as DoP, each altered and each with frames cut
  from the middle of the body, and each must give `NOT_BIT_PERFECT`. Their
  names end in `_altered` and `_incomplete`, which name the fault built into
  the file. In the sweep set, one code near full scale is changed, a fault only
  a sweep finds.
- **Warnings.** Every file carries `TEST - DO NOT PLAY ON SPEAKERS` in its
  title and comment tags. Each zip holds an empty file whose name is the
  warning, repeated in its `README.txt`. The audio file names carry no
  warning.

### FLAC

The `flac` tool encodes the files (`-8 --verify`), and the generator decodes
each result and compares it with the WAV. FLAC is made at every rate. Above
655,350 Hz, FLAC's subset limit, the files (PCM at 705.6 and 768 kHz, and
DSD256 as DoP) are encoded with `--lax`, and need a decoder based on
libFLAC 1.4.0 or later. They are included because players with their own
decoders play them. The version of `flac` therefore must be 1.4.0 or later.
The bytes of a FLAC file depend on the `flac` version.

### ALAC

The ALAC files are encoded, and the software checker decodes ALAC, with Apple's
open-source ALAC codec ([macosforge/alac](https://github.com/macosforge/alac),
Apache-2.0), a git submodule pinned at one commit. The generator writes the
`.m4a` container itself. The codec's packets are byte-identical to those of
Apple's `afconvert`.

- **ffmpeg is not used.** Its ALAC encoder and decoder clamp the history of
  the adaptive Golomb coder differently from Apple's after a run of zeros.
  Apple's decoders (an iPad, `afconvert`) decode ffmpeg's files wrongly, and
  ffmpeg decodes some of Apple's files wrongly. Reported as
  [ffmpeg issue 25727](https://code.ffmpeg.org/FFmpeg/FFmpeg/issues/25727)
  (bpc1.md, section 9.7).
- **Apple's own ALAC decoders refuse ALAC above 384 kHz** (measured with
  `afconvert` on macOS). The ALAC files at 705.6 and 768 kHz and DSD256 as DoP
  are therefore for players with their own ALAC decoder; the WAV and AIFF files
  of the same name hold the same samples.
- The bytes of an ALAC file depend on the pinned codec and on how the helper
  was built. Build the ALAC helper with GCC 15.2, as the release did, or
  compare `dist/SHA256SUMS` with the release's. Another compiler or other
  flags may change the ALAC bytes, because Apple's encoder has undefined
  behaviour (left shifts of negative values in `codec/dp_enc.c`, lines 109,
  137 and 230, and misaligned 32-bit accesses in `codec/ag_enc.c`, lines 196
  to 246). The decoded samples must still equal the WAV's; the generator
  decodes every ALAC file it writes and compares it with the WAV.

### AIFF

The generator writes AIFF itself, from the samples of the WAV.

### The gapless set

A player that plays tracks gaplessly joins them in one stream. Between two
regular files that join falls in the tail of one and the lead-in of the
next, which no checker compares, so the regular files cannot show a join
that drops, inserts or repeats samples. A gapless pair is one regular file
cut in two inside its body: the join falls in compared units, and the pair
gives one result.

- **One pair per format.** Each format reaches the join through its own
  decoder and container. FLAC and ALAC parts end on a short frame or packet
  that a decoder must not pad or drop; ALAC has no priming and no edit list,
  so its frame count is the last `stts` entry. A DSF part ends inside a
  4096-byte block, zero-padded as Sony's specification asks: a player that
  plays whole blocks inserts the padding. DFF has no blocks. WAV and AIFF
  are the plain case. The pairs are 44.1 and 48 kHz at 16 and 24 bits, the
  rates and depths of most albums played gaplessly, and DSD64; one zip of
  about 57 MB holds them all.
- **The cut, one unit after 7.5 s.** It lies in the body, away from both
  ends, in the payload of a block without an R-header, and at the same time
  at every rate, so a change at the join gives `diff` 7.500 for every format.
  The cut (for DSD, its count of DoP frames) is odd and not a multiple of 3,
  5 or 7: it falls on no block, no 4096-frame FLAC or ALAC packet, no DSF
  block, no buffer of 2^k frames and no 6-frame USB packet counted from the
  file's start, so a player that rounds the join to any of these is seen.
  The units at the cut differ from zero and from their neighbours, so a
  unit inserted, dropped or repeated there changes the first unit compared
  after the join. Part 1 of a DSD pair holds an odd number of DoP frames,
  so DoP markers that start again with part 2 repeat a marker.
- **The regular file's identity.** The parts carry the identity of the file
  they come from, so the format, both checkers and the firmware need no
  change: a pair played gaplessly is that file. Each part alone is
  `NOT_BIT_PERFECT`; the software checker's `--join` judges the parts of a
  pair as one stream.
- **After the regular set.** The pairs test the join only. They assume that
  samples arrive unchanged, which the regular files of the same player and
  format show first. A gapless pass is `BIT_PERFECT` with `lost=0`: a pause
  in which the host sends no audio carries no units and leaves the verdict
  unchanged, but a DAC plays it as a dropout.

### Determinism

The same generator gives byte-identical files and zips on a re-run, given the
same tool versions. No writer puts a time, a host name or a user name into a
file. The zips list entries sorted by name, with fixed dates, permissions and
no comments (`generator/package.py`). `tests/test_repeatability.py` proves it.
The bytes of FLAC files depend on the `flac` version, and the bytes of the
deflated WAV zip on the zlib version.

### Release

- **Names.** The test files are named after the format with the file-set
  release: `bpc1-r1-wav.zip`, `-flac.zip`, `-aiff.zip` and `-alac.zip`, and
  `bpc1-r1-sweep-*.zip` for the sweep set. BPC1 names the format and its
  version (an incompatible format would be BPC2); r1 is the first release of
  the files, and changed files become r2. The tag is `bpc1-r1`. Changed files
  are a new release, not new assets on an old one.
- **Firmware** is named after the project, with its own version:
  `bit-perfect-checker-firmware-<version>.zip`, under its own tag
  (section 8).
- **Downloads** are GitHub release assets of this repository. GitHub allows each
  release file up to 2 GiB; the largest zips are about 1.4 GB. Packaging treats
  a zip of 2 GiB (2^31 bytes) or more as an error and does not write it.
- Releases are rare and published by hand with `gh release create`, without a
  release workflow. A published firmware binary is the tested binary,
  unchanged.

## 6. USB bandwidth and host behaviour

### Bandwidth

Stereo throughput and bytes per USB transfer interval: 125 us microframes at
High-Speed, 1 ms frames at Full-Speed. The interval figures include one extra
frame of headroom for asynchronous rate adaptation, as the endpoint's
`wMaxPacketSize` does (`firmware/app/usb_descriptors.h`).

| Stream | Slot | Throughput | HS bytes per microframe | FS bytes per frame |
|---|---|---|---|---|
| 96 kHz PCM | 4 bytes | 0.77 MB/s | 104 | 776 (fits) |
| 192 kHz PCM | 2 bytes | 0.77 MB/s | 100 | 772 (fits) |
| 192 kHz PCM | 4 bytes | 1.54 MB/s | 200 | 1544 (no) |
| 176.4 kHz DoP (DSD64) | 4 bytes | 1.41 MB/s | 192 | 1424 (no) |
| 705.6 kHz DoP (DSD256) | 4 bytes | 5.64 MB/s | 720 | no |
| 768 kHz PCM | 4 bytes | 6.14 MB/s | 776 | no |
| 1411.2 kHz DoP (DSD512, out of scope) | 4 bytes | 11.3 MB/s | 1424 | no |

- **Full-Speed** allows at most 1023 bytes per frame for one isochronous
  endpoint. That tops out at 96 kHz with 4-byte slots or 192 kHz with 2-byte
  slots, so **High-Speed is required.**
- **High-Speed:** every format in scope fits one 1024-byte transaction per
  microframe.

### Host behaviour

The device is an instrument for what a host does to audio. The effects it can
show:

- **Mixing and resampling.** A host's shared-mode mixer converts the file to a
  mixer format, so a file at another rate is resampled, and applies software
  volume. Bit-perfect playback needs the player's exclusive or bit-perfect
  output, with no volume control, EQ, ReplayGain or normalization.
- **Volume.** The device has no Feature Unit, so the host cannot hand volume to
  the device. Any volume change happens in the host's software, where the
  checker sees it as a body that is not bit-perfect.
- **Linux.** An ALSA `hw:` device passes data without mixing or conversion
  (`hw:CARD=Checker` reaches the device without PipeWire). For 16-, 24- and
  32-bit files, snd-usb-audio picks alt 1, alt 4 (`S24_3LE`) and alt 2
  respectively. Alt 2 and 3 are both `S32_LE` with the same packet size, and
  Linux takes the first, so it never selects alt 3; all 32 bits still arrive.
  A Linux stream carries 17 to 20 ms more than the file, inside the file's extent ([measurements](measurements.md#27-frames-beyond-the-file)). A desktop session
  through a sound server may resample.
- **iPadOS.** The iPad joins files of the same rate into one stream and sends
  about 0.2 s of zeros before each file.
- **Packet placement.** Where a host places isochronous packets inside the
  microframe differs between hosts, and at the highest rates it decides
  whether a device with a single re-armed OUT transfer misses a packet (section
  8; the measurements are in [measurements.md](measurements.md#22-lost-packets-at-7056-and-768-khz)).

## 7. Hardware

### Controller

The checker needs a USB High-Speed device with an on-chip PHY at 768 kHz and
enough CPU to check every frame. The choice is the **STM32U5A5** (Cortex-M33
at 160 MHz, 2.5 MB of RAM, 4 MB of flash, an on-chip High-Speed PHY), with
[TinyUSB](https://github.com/hathach/tinyusb) (MIT), on the **NUCLEO-U5A5ZJ-Q**
board. TinyUSB supports the controller's USB core (Synopsys DWC2) at
High-Speed, has UAC2 examples, and has an STM32U5 board file that the
firmware's board files follow. The firmware's USB descriptor and receive path
take every rate to 768 kHz.

Alternatives, and why not:

- **Raspberry Pi Pico (RP2040, RP2350):** a USB 1.1 controller and PHY,
  Full-Speed only, so at most 96 kHz with 4-byte slots or 192 kHz with 2-byte
  slots, and no ULPI interface for an external High-Speed PHY
  ([RP2040](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf),
  [RP2350](https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf)). A
  Linux USB audio gadget on a Raspberry Pi computer is not used.
- **STM32F723 and F733:** a Cortex-M7 at 216 MHz with 256 KB of SRAM and an
  on-chip High-Speed PHY (the F722 and F732 need an external ULPI PHY). The
  STM32F723E-DISCO board has an LCD.
- **STM32H7R3 and H7S3:** plenty of CPU, but 64 KB of flash, so the
  application runs from external flash.
- **STM32N6:** BGA only, no flash, complex power: more than a checker needs.
- **STM32H723 and H730 with an external ULPI PHY:** adds a 12-signal 60 MHz
  bus and a second chip.
- **XMOS xcore.ai (XU316):** its USB audio reference firmware (`lib_xua`) lists
  rates only up to 384 kHz, its licence is not an OSI licence, and it needs
  extra supply rails, a boot flash and a different toolchain.
- Other microcontrollers with a High-Speed PHY (NXP i.MX RT, WCH CH32, Artery
  AT32, HPMicro, Espressif ESP32-P4) give no gain over STM32 here: external
  flash or less proven UAC2 stacks.

The firmware uses TinyUSB, not ST's own USB middleware.

### Boards and displays

- The first board reports through its LEDs and a log on the ST-LINK's virtual
  COM port.
- The firmware separates the board-independent code from the MCU family and the
  board, so another board, a board with a built-in display, or a board with a
  display shield can be added (firmware README, "Adding a board"). The interface
  a display must implement is `firmware/include/display.h`; the first board has
  none.
- The device's USB product name is "Bit-Perfect Checker" and its manufacturer
  "Mete Balci". The names carry no version, so they stay right if a later
  signal format version comes. The test signal and its files keep the name
  BPC1.
- **Flashing** is done with ST's STM32CubeProgrammer (or the ST-LINK's
  drag-and-drop drive), not OpenOCD: OpenOCD 0.12.0 cannot program the
  STM32U5A5, whose device ID 0x481 its flash driver does not know.

## 8. Firmware

### USB function

- A UAC2 High-Speed sink, asynchronous, with an explicit feedback endpoint that
  always sends the constant nominal rate (16.16). There is no audio clock to
  follow, so the host's USB timing paces the stream (`firmware/app/uac2.c`).
- Four streaming alt settings: 16-bit in 2-byte slots, 24-bit in 4-byte
  slots, 32-bit in 4-byte slots and 24-bit in 3-byte slots (`16/2`, `24/4`,
  `32/4`, `24/3`), all integer PCM.
- **The formats the device offers are configurable,** as a list in alt-setting
  order: a build option sets the boot default (all four) and a console command
  changes the list until the next reset. A host picks one alt setting, often the
  first or the deepest it supports, so offering one format lets a tester choose
  what the host sends.
- No Feature Unit (section 6).
- **USB ID.** The default is the pid.codes test PID 1209:0001, which is for
  in-house testing only and not for a device that is distributed. The build
  warns about it at configure time, and the boot line shows the ID in use. A
  build option sets another VID:PID, which anyone distributing a device must
  do. A different USB ID changes only the IDs in the descriptors.

### TinyUSB

The submodule stays at release 0.21.0. The build applies one local patch,
`firmware/lib/patches/tinyusb-audio-out-zlp.patch`, to a copy of
`src/class/audio/audio_device.c` in the build folder. The submodule is not
modified, and a patch that no longer applies fails the build.

The patch re-arms the audio driver's OUT endpoint after a zero-length
packet. USB Audio Data Formats 2.0 allows a zero-length packet in place of any
packet. In DMA mode the driver returned before re-arming, and the device
received nothing more until the host selected an alt setting again.

The USB controller runs in DMA mode. Its isochronous OUT endpoint holds one
transfer at a time and is re-armed after each packet, so a host that sends a
packet late in one microframe and the next early in the following one can make
the device miss a packet. This is a limit of the host and the controller
together, and TinyUSB is otherwise unmodified (firmware README, "What it
does").

The receive FIFO between the USB interrupt and the main loop holds 32,760
bytes, 5.3 ms at 768 kHz in 4-byte slots; TinyUSB's FIFO is limited to 32 KB.

### The checker in the firmware

- The portable C engine (`firmware/bpc1/`) gets every frame as 32-bit
  left-justified words with the host's rate and the slot's bits. It finds each
  file in the stream by its X-syncs, PCM or DoP, and gives each file its own
  verdict, since hosts join files of the same rate into one stream. The host
  tests build it too (`tests/test_bpc1_c.py`).
- It implements the exact layer; the robust layer is not on the device.

### LEDs

Blue shows the stream: blinking while waiting, steady while a stream plays.
Green and red show a **run**: every result since the user button was pressed,
the console command `mark` was sent or the board was reset, whichever came
last. They show the first of these that holds: red steady for any
`NOT_BIT_PERFECT`, red blinking for any `UNKNOWN_SIGNAL`, red and green
blinking together for any `DEVICE_OVERRUN`, and green when every result of the
run was `BIT_PERFECT`. Red stays on until the next run, so the LEDs do not
depend on how the host splits files into streams. The full table is in the
firmware README.

### Log and automation

- The serial log is one line per event, readable by scripts as well as
  people. The device prints one `result` line per file, and a stream's
  `stream stop` line follows its results. The fields are fixed before scripts
  depend on them; new fields may be added, existing ones keep their meaning.
  JSON comes from `bpcctl`, which converts the lines.
- The device takes commands on the same port, so a script can play a file and
  read the verdict without looking at the LEDs (`bpcctl run`).
- **The result queue holds 16 reports,** and the `stream stop` line ends with
  `results_lost=<n>`, so a dropped result can never pass for a file that did
  not arrive (`BPC1_MAX_REPORTS` in `firmware/bpc1/bpc1_checker.h`): `bpcctl
  run` exits 4 (not fully judged, play again) when `results_lost` is above
  0, when fewer result lines arrived than `results=`, or when a stream has
  not stopped by its timeout, unless a result failed (exit 1).
- **`lost` and `ovf` are charged by frame position.** Each loss and each overflow
  goes to the result line of the file whose frames it falls in, and the lines
  add up to the `stream stop` line. A result line is printed once the checker
  has read past the file's extent (about one block after its tail), when the
  next result arrives, or when the stream stops, whichever comes first.
- The log keeps room in its queue that only `result` and `stream stop` lines
  may use.

### Versions

Firmware releases are tagged `firmware-v<major>.<minor>.<patch>`, the first
`firmware-v1.0.0`. The device reports the version without the prefix (`fw=1.0.0`
at the tag), and the zip is `bit-perfect-checker-firmware-<version>.zip`. The
build takes the version from `git describe` on tags that match `firmware-v*`;
the test files' tags do not change it (`firmware/cmake/version.cmake`).

## 9. Tested hosts

The project tests with a Linux PC (snd-usb-audio, raw `hw:` playback) and an
iPad. macOS, Windows and Android are not tested.

- **Linux.** Up to 384 kHz, and every 16-bit stream, arrived complete on the
  host tested. With 3- and 4-byte slots at 705.6 and 768 kHz, packets were
  missed, about one in 4,000 to 17,000, in holes of one or two microframes.
- **iPad.** No packet was lost at any rate.
- Two behaviours are checked against the code and the specification only:
  zero-length audio packets, which the tested hosts never send, and a rate
  change while a stream stays open, which the tested hosts avoid by closing the
  stream (alt setting 0) first. The firmware ends such a stream with
  `reason=rate` and starts a new one at the new rate.

The figures behind these results, with the firmware stage of each, and the
load and receive-FIFO margins at 768 kHz are in [measurements.md](measurements.md).

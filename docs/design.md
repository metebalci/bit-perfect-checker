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
- **The header holds only what a checker needs to rebuild the reference:**
  the kind (PCM, DSD or the PCM code sweep), rate, depth, channels and seed.
  A signal is the same in every container: the WAV, FLAC, AIFF and ALAC
  files of a PCM signal hold the same samples, and the DSF, DFF and DoP
  files of a DSD rate the same DSD bits. The container and the set are in
  the numbered file names, not in the signal.
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
  24-bit at all ten rates. A path is proven bit-perfect only when both the
  regular and the sweep files pass; the regular files are the quick first
  check, which already finds any processing that touches all samples.
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
| `OVERRUN` | hardware checker only: its own receive buffer overflowed before any difference, so it could not judge the file; play it again |

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
  overflow cannot undo a proven difference. `OVERRUN` is for an overflow
  before any difference. It is never reported as a fault of the host.
- **One span per file.** A file is judged on the units its result holds, so
  no unit belongs to two results, and where an overflow or a lost packet
  falls decides which result counts it. A file that completes its LAST block
  ends there, and what comes after belongs to the next result. A file cut
  before that holds the units up to the next file's first X-sync, at most to
  its extent's end: only an X-sync shows where the next file's units start,
  so an overflow right at the cut is counted on the file that did not arrive
  whole. Being cut does not fail a file by itself: a file cut in its tail,
  after its body and the tail X-sync after it, passes.
- **The declared size.** A file's size is its first R-header's count, and the
  file's blocks are checked against it: an intact LAST X-sync at another
  index, an X-sync whose block index is the declared size or higher, or a body
  R-header with another count means the data were changed, and the file is
  `NOT_BIT_PERFECT`. A count disagrees in either direction, too large or too
  small. The first R-header read decides and no later one replaces it, so the
  size does not depend on which R-headers arrived; a count too small gives
  the file no count, so its extent follows its X-syncs. The LAST
  block gives the size only where no R-header does, so short files without an
  R-header, and files whose R-headers did not arrive, still have one. A file
  whose LAST block's index + 1 is not its R-header's count is cut, not
  complete.
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
  also as FLAC, AIFF and ALAC. The regular set is 102 files, about 1.65 GB;
  the code-sweep set is 81 files, about 5.0 GB; the gapless set is 38 files,
  about 59 MB (bpc1.md, sections 11.1, 11.6 and 11.7), the negative files
  included.
- **Every format a player plays is tested.** A player can decode each format
  through a different path, so the WAV set is where a test starts, not where it
  ends. FLAC, AIFF and ALAC are made from the WAV of the same name and hold
  the same samples.
- **Numbered names.** Every file name starts with a five-digit number,
  type, output, format and position (bpc1.md, section 11.2), for example
  `00033-bpc1_pcm_096k_24bit.wav` and `00133-bpc1_pcm_096k_24bit.flac`. The
  files of every archive merged into one folder play in a fixed order:
  regular PCM, regular DSD, code sweep, gapless pairs, negative files. The
  rest of the name is the same in every format, so files of the same name
  are files whose names agree after the number. A released number never
  changes. The title tag carries the number and the format too, so a player
  that shows only titles still shows which file plays.
- **Negative files.** In a zip of their own, `bpc1-r1-negative.zip`,
  deliberately faulty copies show that a checker catches faults. They are
  for developers, to see a negative fail, not for testing a player. Most
  tests play only the files that should pass and look for green; the format
  zips hold only those, so a whole folder played as a playlist must give
  only `BIT_PERFECT`. The checker does not depend on the container, so the
  negatives are WAV only, one per fault, seven files: PCM 44.1 kHz 24-bit
  and DSD64 as DoP, each altered and each with frames cut from the middle
  of the body; the 44.1 kHz 16-bit sweep with one code near full scale
  changed, a fault only a sweep finds; and a gapless pair with 10 ms of
  silence before part 2. Each must give `NOT_BIT_PERFECT` (the pair: played
  gaplessly). Their names end in `_altered`, `_incomplete` or `_gap`, which
  name the fault built into the file, and their numbers start with 9.
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
  of the same name (after the number) hold the same samples.
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
  rates and depths of most albums played gaplessly, and DSD64 as DSF and
  DFF; one zip of about 57 MB holds them all. DoP has no pair: its DSD
  bits are those of the DSF and DFF files, and as PCM it takes the path the
  PCM pairs test.
- **A negative pair.** The 44.1 kHz 16-bit WAV pair with 10 ms of silence
  before part 2, the commonest fault of a player that is not gapless, is in
  the negative zip: played gaplessly it gives one `NOT_BIT_PERFECT` with
  `diff` 7.500, so a checker is seen to catch a gap.
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
  release: `bpc1-r1-wav.zip`, `-flac.zip`, `-aiff.zip` and `-alac.zip`,
  `bpc1-r1-sweep-*.zip` for the sweep set, `bpc1-r1-negative.zip` for the
  negative files of all three and `bpc1-r1-gapless.zip` for the gapless
  pairs. BPC1
  names the format and its version (an incompatible format would be BPC2);
  r1 is the first release of the files, and changed files become r2. The
  tag is `bpc1-r1`. Changed files are a new release, not new assets on an
  old one.
- **Firmware** is named after the project, with its own version:
  `bit-perfect-checker-firmware-<version>.zip`, under its own tag
  (section 8).
- **Downloads** are GitHub release assets of this repository. GitHub allows each
  release file up to 2 GiB; the largest zips are about 1.3 GB. Packaging treats
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
  microframe differs between hosts. A packet that completes late in its
  microframe makes a device with a single re-armed OUT transfer miss the
  next one unless the re-arm targets the microframe after the packet
  (section 8, u2ps; the measurements are in
  [measurements.md](measurements.md#210-late-completions-and-the-re-arm-patch)).

## 7. Hardware

### Controller

The checker needs a USB High-Speed device with an on-chip PHY at 768 kHz and
enough CPU to check every frame. The choice is the **STM32U5A5** (Cortex-M33
at 160 MHz, 2.5 MB of RAM, 4 MB of flash, an on-chip High-Speed PHY), with
[u2ps](https://github.com/metebalci/uac2-playback-stack) (MIT) as the USB
device stack, on the **NUCLEO-U5A5ZJ-Q** board. u2ps is a USB Audio Class 2
playback stack for the controller's USB core (Synopsys DWC2) at High-Speed,
with ports for the STM32U5A5 and the STM32F723 (section 8, u2ps). The
firmware's USB description and receive path take every rate to 768 kHz.

Alternatives, and why not:

- **Raspberry Pi Pico (RP2040, RP2350):** a USB 1.1 controller and PHY,
  Full-Speed only, so at most 96 kHz with 4-byte slots or 192 kHz with 2-byte
  slots, and no ULPI interface for an external High-Speed PHY
  ([RP2040](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf),
  [RP2350](https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf)). A
  Linux USB audio gadget on a Raspberry Pi computer is not used.
- **STM32F723 and F733:** a Cortex-M7 at 216 MHz with 256 KB of SRAM and an
  on-chip High-Speed PHY (the F722 and F732 need an external ULPI PHY). The
  firmware also runs on the STM32F723E-DISCO board, which has an LCD (Boards
  and displays).
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

The firmware uses u2ps, not ST's own USB middleware.

### Boards and displays

- Every board reports through its LEDs and a log on the ST-LINK's virtual COM
  port. There are three boards: the NUCLEO-U5A5ZJ-Q (STM32U5A5, LEDs only), the
  STM32F723E-DISCO (STM32F7, a Cortex-M7 at 216 MHz with an on-chip
  High-Speed PHY; a 1.54" 240x240 screen with touch) and the STM32U5G9J-DK2
  (the NUCLEO's family and clock tree; a 5" 800x480 screen with touch and no
  blue LED).
- The firmware separates the board-independent code from the MCU family and the
  board, so another board, a board with a built-in display, or a board with a
  display shield can be added (firmware README, "Adding a board"). The interface
  a display must implement is `firmware/include/display.h`; the NUCLEO has
  none, and the F723 and the DK2 implement it with one shared status screen
  (`firmware/app/screen_model.c`, `firmware/app/screen.c`) over a small panel
  interface (`firmware/include/panel.h`).
- **The screen is a nicer LED.** It shows the state and the verdict, the file
  being checked as its header gives it (kind, rate, depth) and the firmware
  version, in large text, with no counters and no detail rows: the log has
  them. The F723's screen is the layout; the DK2 shows it at twice the size on
  its left 480x480 and keeps the right 320x480 black, reserved for more.
- **The frame's colour is the run's state, as the LEDs show it,** in blue,
  green and red only: blue before a result, green while every result is
  `BIT_PERFECT`, red at once after any failure, so an overrun is a failure like
  the others and the labels `UNKNOWN` and `OVERRUN` under the frame tell the
  kinds apart. The word in the frame says what is going on (`NOT CONNECTED`,
  `WAITING`, `CHECKING`), so in-progress states and results are told apart by
  their words, not by the frame. `NOT_BIT_PERFECT` has no label, since the red
  frame already shows it. The screen writes names without the log's
  underscores, and BIT-PERFECT hyphenated as in the project's name.
- **At Full-Speed the screen says what to change.** The device offers no
  audio there, so instead of a state it shows `NEEDS` `HIGH-SPEED` `USB PORT`
  in a tall red frame over the run's texts, and the blue LED double-blinks,
  a pattern of its own beside the fast and slow blinks. Green and red stay
  off, since nothing can be checked.
- **An hourglass shows that the device is waiting or checking.** It blinks
  with the LEDs' slow blink; during `CHECKING` it blinks only while packets
  arrive, so a stream that stalls shows as a steady hourglass. Nothing else on
  the screen blinks.
- **A board without a blue LED** (the DK2) shows the connection and stream
  state on its screen. Green means bit-perfect, so green is not reused for
  waiting; red and green show the run as on the other boards.
- **A tap anywhere on a touch screen is the user button;** nothing else uses
  touch.
- **A splash screen** at start-up shows the name, the project's address, the
  version and three squares, red, green and blue, that light as the start-up
  LED test lights its LEDs, in the same order and timing. It does not delay
  USB.
- **Drawing never holds up USB.** The device's buffers hold as little as
  7 ms of audio (the STM32F723E-DISCO in 4-byte slots, section 8, u2ps), so
  the screen is drawn from the main loop in units of about 0.2 ms at most (a
  strip of the band, one character), only while less than 0.5 ms of
  received audio waits to be read, and only the characters that changed
  (`firmware/app/screen.h`).
- **The DK2's frame buffer has one byte per pixel and a fixed RGB332
  palette,** 384,000 bytes instead of 768,000 for RGB565; the screen uses only
  black, white, red, green and blue. The palette holds black, white, red and
  blue exactly. The panel shows full green lighter than the F723's panel, so
  the palette scales its pure greens, and full green is (0, 208, 0)
  (`PANEL_GREEN_MAX`, `firmware/boards/stm32u5g9j_dk2/clut_rgb332.h`). The
  buffer is alone in a RAM bank that no other bus master uses, so the display
  controller's reads never wait for the USB DMA.
- **The F723 runs with the data cache off** and the instruction cache on, so
  that its USB DMA needs no cache maintenance or special buffer placement.
- **A fault** lights every LED the board has and switches a screen's backlight
  off, so that no screen keeps showing a result.
- The device's USB product name is "Bit-Perfect Checker" and its manufacturer
  "Mete Balci". The names carry no version, so they stay right if a later
  signal format version comes. The test signal and its files keep the name
  BPC1.
- **Flashing** is done with ST's STM32CubeProgrammer on every board: it
  programs every STM32 the firmware runs on, and it names the ST-LINK by its
  serial number, so that with several boards connected a flash never reaches
  the wrong one. The NUCLEO's and the F723's ST-LINK also show a
  drag-and-drop drive; the DK2's shows none. The F723's
  image is linked at the flash's ITCM alias, where code and read-only data go
  through the flash accelerator, so it is flashed as a `.bin` to the flash's
  own address (firmware README, "Flash").

## 8. Firmware

### USB function

- A UAC2 High-Speed sink, asynchronous, with an explicit feedback endpoint that
  always sends the constant nominal rate (16.16). There is no audio clock to
  follow, so the host's USB timing paces the stream (`firmware/app/u2ps_glue.c`).
  The data endpoint declares no lock delay, as UAC2 requires of an
  asynchronous endpoint (4.10.1.2).
- Four streaming alt settings: 16-bit in 2-byte slots, 24-bit in 4-byte
  slots, 32-bit in 4-byte slots and 24-bit in 3-byte slots (`16/2`, `24/4`,
  `32/4`, `24/3`), all integer PCM.
- **The formats the device offers are configurable,** as a list in alt-setting
  order: a build option sets the boot default (all four) and a console command
  changes the list until the next reset. A host picks one alt setting, often the
  first or the deepest it supports, so offering one format lets a tester choose
  what the host sends.
- No Feature Unit (section 6).
- **Audio at High-Speed only.** At Full-Speed the device enumerates, as
  USB 2.0 requires of a High-Speed device (5.3.1.1, 9.1.1.3), but offers no
  audio, so it needs a High-Speed port, hub and cable to play. A Full-Speed
  configuration could not carry most of BPC1 (section 6: above 96 kHz only
  16-bit up to 192 kHz, and no DoP), and a checker that takes some formats
  and not others at one speed would be harder to read than one that takes
  none. The Full-Speed configuration holds the audio function with only its
  AudioControl interface, as UAC2 allows (3.16.2: zero or more
  AudioStreaming interfaces), and no clock or terminals, since a USB
  streaming terminal is one that a streaming interface links to. The device
  qualifier and the other-speed configuration describe the other speed
  (USB 2.0 9.6.2, 9.6.4). u2ps gives this Full-Speed configuration by
  default.
- **Terminals.** The audio function's input terminal is USB streaming, and
  its output terminal is a digital audio interface (0x0602, USB Audio 2.0
  Terminal Types, Table 2-6). The checker drives no transducer, and a
  speaker or headphones type could make a host offer speaker or headphone
  features.
- **Connection state.** The USB stack does not sense VBUS (u2ps runs in
  its VBUS mode `app`, in which the firmware reports VBUS), so on its own it
  cannot tell a pulled cable from a host that suspended the bus. Each board
  reads its user USB port's VBUS in the main loop instead (`board_vbus()`)
  and reports it to u2ps; VBUS gone for 50 ms ends the
  configuration as a controller with VBUS sensing would, and the device
  reports not connected (`firmware/app/main.c`, `firmware/app/vbus.c`). A
  host that only suspends the bus leaves VBUS on, and the device stays
  connected and waiting. Without VBUS the device keeps its D+ pull-up off
  (u2ps puts it on only while VBUS is reported present), from boot too,
  since USB 2.0 (7.1.5.1) forbids pulling D+ up without VBUS and a host may
  refuse a device that does; VBUS back for 50 ms turns it on again. A
  detected disconnect also starts a new run. A pulled
  cable shows as NOT CONNECTED on a screen and as a fast blue blink.
- **USB ID.** The device uses Objective Development's shared VID/PID pair for
  audio class devices, 16C0:27E0, under the serial-number rules of its
  [USB-IDs-for-free.txt](https://github.com/obdev/v-usb/blob/master/usbdrv/USB-IDs-for-free.txt):
  the serial string starts with the project's domain and a colon
  (`bpc.metebalci.com:`), followed by the chip's unique ID. Host software
  matches the serial number as well as the IDs. A build option,
  `USB_SERIAL_PREFIX`, sets the start of the serial string; anyone who
  distributes a modified firmware sets it to a domain or e-mail address of
  their own. Other build options set another VID:PID, which changes only the
  IDs in the descriptors.

### u2ps

The USB device stack is u2ps (uac2-playback-stack), release 0.1.0, a pinned
submodule used unmodified (`firmware/lib/u2ps`). It is a USB Audio Class 2
playback stack and nothing more: one audio function, with no class table and
no generic endpoint API. The figures in [measurements.md](measurements.md)
come from builds up to firmware 1.1.1, all on TinyUSB. What the checker
takes from u2ps:

- **Each packet in its own microframe, each gap with its cause.** u2ps
  reports every packet with the microframe it came in, and every microframe
  without one with its cause: the host sent none, the device re-armed too
  late, or no buffer was free. The hole counter counts these as they come,
  so a late completion leaves no hole, `doubled` is 0, and the `stream stop`
  line tells the device's own misses (`late`) apart from what the host did
  not send. The controller of these boards drops a packet with a CRC error
  unseen, so it counts as a microframe without a packet.
- **Re-arm for the next microframe.** The controller receives an
  isochronous OUT packet only in a microframe whose parity the endpoint is
  armed for, and the endpoint is re-armed from the interrupt of each packet.
  When a packet completes late in its microframe P and the re-arm runs into
  P+1, u2ps still arms for P+1 while P+1 has not passed
  (`port/dwc2/dcd/u2ps_dwc2_iso.c` in u2ps), so the host's packet in P+1 is
  received; a re-arm for the microframe after the current one would wait
  for P+2 and lose it. A re-arm that lands after the host's packet of P+1
  waits for P+3 and loses 2 packets instead of 1. Of the hosts measured,
  late completions came from one host path only, a virtual machine with a
  USB 3 card passed through and the board behind a hub; there this rule
  removed the device's own misses, so every lost packet was one the host
  skipped. On the bare-metal hosts measured no packet completed late, and
  the rule arms what the other would.
- **No copy.** u2ps never copies audio. The firmware lends it the stream's
  buffers, one packet each; the controller's DMA writes each packet into
  one, and the main loop checks it there and gives the buffer back. The
  depth therefore counts in packets, one microframe of audio each at every
  rate: on the STM32U5 boards 1 MB of class memory holds 1351 packets
  (169 ms) in 4-byte slots and 2702 (338 ms) in 2-byte slots, on the
  STM32F723E-DISCO 44 KB in DTCM (zero wait states for the CPU, reached by
  the USB DMA, not cached) hold 56 (7 ms) and 108 (13.5 ms). u2ps keeps one
  of them armed, so the firmware holds one fewer.
- **An overflow is a packet with no buffer.** A packet that comes while the
  firmware holds every buffer it may hold is not received. u2ps reports it
  in its own microframe, and the firmware counts it as an overflow (`ovf`),
  not as lost, so a file it falls in is `OVERRUN` unless a difference came
  before it (section 4).
- **Zero-length packets.** USB Audio Data Formats 2.0 allows a zero-length
  packet in place of any packet. u2ps delivers it as a packet of no frames:
  it keeps the stream open, and the firmware counts it as a microframe
  without a packet.
- **Stream starts and rate changes in the main loop.** u2ps holds the
  host's request until the firmware answers it from the main loop
  (`firmware/app/u2ps_glue.c`). For a rate change, the firmware answers the
  request and sets the stream boundary together, with interrupts off, and
  only if the answer went out, so no packet of the old rate comes after the
  boundary and none of the new rate before it, and a cancelled request
  leaves the rate and the stream as they were; the old rate's packets are
  checked over the next passes. For a stream start, the firmware sets the
  new format, which ends the old stream, and opens the new one once the old
  stream's packets are read, at most 20 ms after the request, since u2ps
  holds it for at most 30 ms; the packets still unread then count as
  overflows.

The controller's "incomplete isochronous OUT" interrupt stays masked, and
the misses come from the microframe numbers. Re-arming from that interrupt
was measured: it comes at 80 % of the microframe, so it lost the packets
that arrive later in the microframe, and the device missed more packets,
not fewer.

### The checker in the firmware

- The portable C engine (`firmware/bpc1/`) gets every frame as 32-bit
  left-justified words with the host's rate and the slot's bits. It finds each
  file in the stream by its X-syncs, PCM or DoP, and gives each file its own
  verdict, since hosts join files of the same rate into one stream. The host
  tests build it too (`tests/test_bpc1_c.py`).
- It implements the exact layer; the robust layer is not on the device.

### LEDs

Blue shows the stream: blinking while waiting, steady while a stream is open,
a double blink when a host set the device up at Full-Speed, where it offers
no audio (a board without a blue LED shows this on its screen).
Green and red show a **run**: every result since the user button was pressed,
the console command `mark` was sent, the USB cable was pulled or the board
was reset, whichever came last. A pull during playback cuts a file; its
result stays in the run before, so the run that starts at the pull is not
red. They show the first of these that holds: red steady for any
`NOT_BIT_PERFECT`, red blinking slowly for any `UNKNOWN_SIGNAL`, red blinking
fast for any `OVERRUN`, and green when every result of the run was
`BIT_PERFECT`. A run is either green or red, and a lower verdict never
changes what a higher one set. Red stays on until the next run, so the LEDs
do not depend on how the host splits files into streams. The full table is in
the firmware README.

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
- The log keeps room in its queue that only `result`, `stream stop` and
  `run start` lines may use.

### Versions

Firmware releases are tagged `firmware-v<major>.<minor>.<patch>`, the first
`firmware-v1.0.0`. The device reports `v<version>-<commit>` with git's short
commit hash in every build, a release's included (`fw=v1.2.0-6b94cda`), so
that the screen and the log name the exact source; `-dirty` marks
uncommitted changes and `-unknown` source outside git. The zip and its
images keep the plain version: `bit-perfect-checker-firmware-<version>.zip`.
The build takes the version from `firmware/VERSION` and stops at a firmware
tag of another number; the test files' tags do not change it
(`firmware/cmake/version.cmake`).

## 9. Tested hosts

The project tests with Linux (snd-usb-audio, raw `hw:` playback) and an
iPad. macOS, Windows and Android are not tested.

- **Linux, a virtual machine with a USB 3 card passed through.** Up to
  192 kHz no packet was lost in any stream; at 352.8 and 384 kHz a stream
  lost a few packets now and then. At 705.6 and 768 kHz streams lost a few
  packets each with 3- and 4-byte slots, about one in 3,800 to 17,000, in
  holes of one or two microframes, and sometimes with 2-byte slots too. With
  a re-arm for the next microframe, as u2ps re-arms (section 8, u2ps), every
  lost packet was one the host's controller skipped.
- **Linux, a bare-metal laptop with an Intel controller.** No packet was lost
  in 15 streams at 705.6 and 768 kHz.
- **Linux, a bare-metal PC with an AMD controller.** No packet was lost in
  30 streams at 705.6 and 768 kHz, nor in the published WAV set.
- **iPad.** No packet was lost at any rate, 768 kHz included. The iPad is
  therefore the reference test for the highest rates; whether every packet
  reaches the device depends on the host.
- **Virtual machines are not recommended as hosts.** With the USB
  controller passed through, the host's periodic schedule sometimes starts
  late in a microframe or skips it; with an emulated controller, bursts of
  packets were lost. Either way the checker reports losses that belong to
  the virtualisation, not to the player under test, so a test host drives
  its USB controller itself.
- Two behaviours the tested hosts never show are tested with a test player
  that drives the device through libusb: zero-length audio packets, and a
  rate change while a stream stays open (the tested hosts close the stream,
  alt setting 0, first). The firmware counts a zero-length packet as a
  microframe without a packet, and ends a stream whose rate changes with
  `reason=rate` and starts a new one at the new rate.

The figures behind these results, with the firmware stage of each, and the
load margins at 768 kHz are in [measurements.md](measurements.md).

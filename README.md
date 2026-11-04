# Bit-Perfect Checker

Bit-Perfect Checker proves whether an audio playback chain (player, operating
system, USB audio stack) delivers a file bit-perfect. It consists of:

- the BPC1 test signal ([signal format](docs/bpc1.md)) and a
  generator for its test files: WAV, FLAC, AIFF and ALAC for PCM (16- and
  24-bit, 44.1 to 768 kHz, and 32-bit WAV on request), and DSF, DFF and DoP
  (in WAV, FLAC, AIFF and ALAC) for DSD64 to DSD256, plus a separate
  code-sweep set;
- firmware that turns a development board into a class-compliant USB Audio
  Class 2 output device (the "DAC" from the host's point of view) that checks
  every sample it receives, the hardware checker;
- a reference software checker, which also gives an informative diagnosis of
  what changed;
- `bpcctl`, a client for test automation.

You play a test file to the board with any player on any operating system,
and the board gives a verdict per file. A pass proves that every sample
arrived unchanged. A fail almost always means a real change; rarely, a USB
packet the device missed fails a file the host sent intact.

The board shows its verdicts on its LEDs and in a serial log, with one result
line per file; see [firmware](firmware/README.md).

To use the checker, see <https://bpc.metebalci.com>: what it is, the
downloads, how to play the files and read the LEDs, the serial log and
`bpcctl`. This README is for the repository: building and flashing the
firmware, generating the test files, the software checker, tests and
rebuilding a release. The design and its reasons are in
[design](docs/design.md).

## Layout

| Path | Contents |
|---|---|
| `bpcctl/` | client for the hardware checker's serial log, for test automation |
| `checker/` | reference software checker (Python) |
| `docs/design.md` | [design](docs/design.md): the choices behind the project and why |
| `docs/bpc1.md` | [BPC1 signal format](docs/bpc1.md), the specification |
| `docs/measurements.md` | [measurements](docs/measurements.md): what was measured on hardware with Linux and an iPad |
| `firmware/` | firmware for the hardware checker, NUCLEO-U5A5ZJ-Q first; see [firmware](firmware/README.md) |
| `generator/` | test-file generator (Python) |
| `generator/third_party/alac/` | Apple's ALAC codec, a git submodule |
| `tests/` | tests for the Python packages and the firmware's portable C checker |
| `vectors/` | golden test vectors for ports to other languages |

## Use

The hardware checker needs a NUCLEO-U5A5ZJ-Q, the firmware on it and the test
files.

1. **The board.** It has two USB connections: the ST-LINK USB port, for power,
   flashing and the serial log, and the user USB port (USB-C), which goes to
   the host under test. Connect both. The board's details are in the
   [firmware README](firmware/README.md).
2. **The firmware.** Build and flash it yourself with ARM GCC, CMake, Ninja
   and ST's STM32CubeProgrammer, following the Build and Flash sections of the
   [firmware README](firmware/README.md): a few commands, ending in the
   `flash` target. Copying the binary to the ST-LINK's USB drive works too. No
   ready-made binary is published yet: the firmware uses the pid.codes test
   USB ID 1209:0001, which is for private use only and may not be
   distributed.
3. **The test files.** Download them from the project's releases, one zip
   per format: `bpc1-r1-wav.zip`, `bpc1-r1-flac.zip`, `bpc1-r1-aiff.zip`
   and `bpc1-r1-alac.zip`, and the code-sweep files in
   `bpc1-r1-sweep-wav.zip`, `-sweep-flac.zip`, `-sweep-aiff.zip` and
   `-sweep-alac.zip` (each about 1.3 to 1.4 GB). Or generate them
   ([Generate the test files](#generate-the-test-files)). The FLAC and ALAC
   files at 705.6 and 768 kHz, and the DSD256 DoP files, need a decoder that
   takes these rates (see
   [How the test files are made](#how-the-test-files-are-made)). The files
   contain full-scale bursts, and the sweep files are full-scale noise for
   their whole length: **do not play them on speakers or headphones.**
4. **Play and read the result.** See the website above. For test automation,
   see [Automate with bpcctl](#automate-with-bpcctl). What to expect from
   Linux and from an iPad, such as packets a PC loses at 705.6 and 768 kHz, is
   in [measurements](docs/measurements.md).

## Build

Run the commands from the repository root. The firmware builds as the
[firmware README](firmware/README.md) says; the rest of this section is the
Python tools.

### Requirements

- Python 3 with numpy.
- For FLAC files, the `flac` command-line tool, version 1.4.0 or later.
- For ALAC files, a C++ compiler (`c++`, `g++` or `clang++`, or `$CXX`) and
  Apple's ALAC codec, a git submodule (`git submodule update --init
  generator/third_party/alac`). The generator and the checker build a small
  helper from the codec into `~/.cache/bit-perfect-checker/` on first use.

Without `flac` or the ALAC tools the generator skips those files and says so.
`--no-flac`, `--no-aiff` and `--no-alac` skip a format on request.

### Generate the test files

Into `out/`: 122 files, about 1.75 GB. Twenty-four of them, in `out/negative/`,
are deliberately faulty copies, altered or incomplete, that a checker must
report as `NOT_BIT_PERFECT` (see the [signal format](docs/bpc1.md),
section 11.5):

```
python3 -m generator                 # the full set, with SHA256SUMS
python3 -m generator --32bit         # also the ten 32-bit PCM WAVs (not released)
python3 -m generator --list          # list the files only
python3 -m generator --only 096k_24bit --out /some/dir
```

The files contain full-scale bursts. They are test signals for the checker,
not for listening: **do not play them on speakers or headphones.**

### How the test files are made

The generator writes every WAV first. FLAC, AIFF and ALAC are made from the
WAV of the same name, so all formats of a file hold the same samples. The
formats in the [signal format](docs/bpc1.md), section 9, are written
as follows:

| Format | Written by |
|---|---|
| WAV, DSF, DFF | the generator itself (sections 9.1, 9.3, 9.4) |
| AIFF | the generator itself, from the samples of the WAV (section 9.6) |
| FLAC | the `flac` tool (section 9.2) |
| ALAC (`.m4a`) | Apple's ALAC codec, through a small helper; the generator writes the MP4 (section 9.7) |
| DoP | a 24-bit WAV, written by the generator; FLAC, AIFF and ALAC are made from it like any PCM WAV (section 9.5) |

**FLAC.** The generator runs `flac --silent --force --no-preserve-modtime -8
--verify -o <file.flac>`, with the tags `TITLE`, `ARTIST`, `ALBUM` and
`COMMENT` (`-T`), on the WAV, in `LC_ALL=C.UTF-8`. `--no-preserve-modtime`
stops flac from giving the FLAC file the WAV's modification time. Above
655 350 Hz, the largest rate of FLAC's subset, it adds `--lax`; that is the
PCM files at 705.6 and 768 kHz and the DSD256 DoP file. The version of `flac`
must be 1.4.0 or later; with an older one the generator skips FLAC and says
so. After encoding, the generator decodes the file with `flac -d` and
compares the samples with the WAV's. The bytes of a FLAC file depend on the
`flac` version. The files above 655 350 Hz need a decoder based on libFLAC
1.4.0 or later and may not play in older players.
(`generator/containers.py`: `FLAC_MIN_VERSION`, `encode_flac`.)

**ALAC.** The codec is Apple's open-source ALAC encoder and decoder
([macosforge/alac](https://github.com/macosforge/alac), Apache-2.0), a git
submodule in `generator/third_party/alac` pinned at commit `c38887c`. The
helper, `generator/alac_tool.cpp`, driven by `generator/alac.py`, is built
from the codec's sources with the host C++ compiler on first use (`-std=c++11
-O2`). It encodes the WAV's samples in packets of 4096 frames, the last one
partial. The generator then writes the MP4 container itself, with no times
(creation and modification are 0) and no edit list, in the order `ftyp`,
`moov`, `mdat`. The tags are `©nam` (the title), `©too` (the encoder, `Apple
ALAC encoder, macosforge/alac c38887c`) and `©cmt` (the comment); there is
no artist or album tag. Finally the generator decodes the file with Apple's
decoder and compares the samples with the WAV's. Built with GCC 15.2, the
helper writes the same bytes on every run, and its packets are
byte-identical to those of `afconvert` on macOS (signal format, section
9.7). Other compilers were not measured: Apple's encoder has undefined
behaviour (left shifts of negative values in `codec/dp_enc.c`, lines 109,
137 and 230, and misaligned 32-bit accesses in `codec/ag_enc.c`, lines 196
to 246), so a helper built by another compiler or version may write other
bytes. The decode and compare still checks the samples.

Apple's own ALAC decoders refuse ALAC above 384 kHz (the one in macOS does,
and so presumably does iOS's built-in playback), so the ALAC files at 705.6
and 768 kHz and the DSD256 DoP file are for players with their own ALAC
decoder. The WAV and AIFF files of the same name hold the same samples and
play everywhere.

**ffmpeg is not used.** Neither the generator nor the checker runs it. Its
ALAC encoder and decoder differ from Apple's (see Known issues), so ALAC is
encoded and decoded with Apple's codec only.

**Tool versions of the published release bpc1-r1:**

| Tool | Version |
|---|---|
| Python | 3.14.4 |
| numpy | 2.3.5 |
| `flac` | 1.5.0 |
| C++ compiler (`c++`) | GCC 15.2.0 (Ubuntu 15.2.0-16ubuntu1) |
| zlib (in Python, for the WAV zip) | 1.3.1 |
| ALAC codec commit | `c38887c5c5e64a4b31108733bd79ca9b2496d987` |

The bytes of the FLAC files depend on the `flac` version, and the bytes of
the WAV zip, the only deflated one, on the zlib version. The bytes of the
ALAC files depend on the codec commit and, through the encoder's undefined
behaviour, possibly on the compiler: they were measured with GCC 15.2 only.
The compiler is part of the key under which the helper is cached
(`generator/alac.py`), so another compiler builds its own helper.

### Determinism

The same generator gives byte-identical files and zips on a re-run, given
the same tool versions. No writer puts a time, a host name or a user name in
a file. The zips (`generator/package.py`):

- list the entries sorted by name;
- date every entry 2026-11-02 00:00:00;
- give files the Unix permissions 0644 and folders 0755;
- carry no comments and no extra fields;
- store every zip except the regular WAV zip, which is deflated at level 9
  (the empty warning file and the folders are stored in it too).

`tests/test_repeatability.py` proves it. By default it generates a sample
of the files (one or more of every container, FLAC, AIFF and ALAC included)
twice through the command line and compares every byte. With
`BPC1_FULL_SET=1` it generates the whole set, with the 32-bit WAVs, twice,
packages each into its own dist folder, and compares every byte of the files
and of the four zips. It expects all four zips, so it needs `flac` and the C++
compiler. It does not cover the sweep set.

### Package a release

From `out/` into `dist/`: `bpc1-r1-wav.zip` (PCM WAV, DSD, negative WAV;
about 402 MB), `bpc1-r1-flac.zip` (PCM, DoP and negative FLAC; about
311 MB), `bpc1-r1-aiff.zip` (AIFF; about 495 MB) and `bpc1-r1-alac.zip`
(ALAC in `.m4a`; about 324 MB). Each holds an empty file whose name is the
warning, a README.txt, a SHA256SUMS of its files and the negative tests in a
folder `negative/`. Packaging first checks every file against
`out/SHA256SUMS`. It stops with an error, and does not write the zip, when
a zip reaches 2 GiB (2^31 bytes), GitHub's limit per release file.

```
python3 -m generator package
```

The code-sweep set (88 files, about 5.4 GB, a separate set: see the
[signal format](docs/bpc1.md), sections 7.4 and 11.6) is written and
packaged on request. Use an output folder of its own, so that it does not
mix with the regular files:

```
python3 -m generator --sweep --out out-sweep
python3 -m generator package --sweep --out out-sweep   # the four bpc1-r1-sweep-*.zip, all stored
```

### Rebuild a release and check it

With the tool versions of the table above, the steps give zips that are
byte-identical to the release's.

1. Clone the release's tag with the submodules:
   `git clone --recurse-submodules --branch bpc1-r1
   https://github.com/metebalci/bit-perfect-checker`. In an existing clone,
   check out the tag and run `git submodule update --init` (or only
   `git submodule update --init generator/third_party/alac`, which is all
   the generator needs). Check that `git -C generator/third_party/alac
   rev-parse HEAD` prints the codec commit of the table.
2. Generate the regular and the sweep set, each into an empty folder:

   ```
   python3 -m generator --out out
   python3 -m generator --sweep --out out-sweep
   ```

3. Package both into one empty dist folder:

   ```
   python3 -m generator package --out out --dist dist
   python3 -m generator package --sweep --out out-sweep --dist dist
   ```

   The folder must be empty because `dist/SHA256SUMS` keeps the entries of
   zips that are already in the folder and lists them next to the new ones. After
   the two commands it lists all eight zips.
4. Compare `dist/SHA256SUMS` with the `SHA256SUMS` of the release, for
   example with `diff`. No difference means the zips are identical.

If the lines of some zips differ, look at each zip's own `SHA256SUMS`,
the checksums of its audio files, to find the files that differ. With a
`flac` version other than the release's, the FLAC files and the two FLAC zips
differ in their bytes but not in the decoded samples: the generator decodes
every FLAC file and compares it with its WAV, so the samples are those of the
WAV, and the checker gives `BIT_PERFECT` on the file.
With another zlib version only the WAV zip differs, but not the checksums of
the files inside it.

### Check a file or a capture

```
python3 -m checker out/bpc1_pcm_096k_24bit.wav
python3 -m checker --json out/*.dsf
python3 -m checker --raw s32le --rate 96000 --channels 2 capture.raw
```

It reads WAV, FLAC, AIFF, ALAC (`.m4a`, decoded with Apple's ALAC decoder),
DSF, DFF and DoP (in WAV, FLAC, AIFF or ALAC), and raw captures (`s16le`,
`s24_3le`, `s24le`, `s32le`, `f32le`, `dsd_u8`, `dsd_u16_be`, `dsd_u32_be`,
`dsd_u32_le`), and gives one verdict per file, with a `diagnosis` that says what
changed (informative; the verdict does not depend on it):

| Verdict | Meaning |
|---|---|
| `BIT_PERFECT` | the file arrived at its rate with the whole body as one run of consecutive units, every unit equal to the expected one in every channel, and the X-syncs before and after the body intact |
| `NOT_BIT_PERFECT` | the file was found and that does not hold |
| `UNKNOWN_SIGNAL` | audio that belongs to no BPC1 file, silence included: not a BPC1 test signal, or a stream in which inversion, rounding, dithering or resampling destroyed the X-syncs |
| `DEVICE_OVERRUN` | hardware checker only: its own receive buffer overflowed and it could not judge the file; play it again |

The exact rules are in section 12.6 of the [signal format](docs/bpc1.md#126-verdicts).
Only `BIT_PERFECT` is a pass. In the software checker, by default, silence
outside a file gives `UNKNOWN_SIGNAL` too; its `--allow-silence` lets silence
anywhere outside the files give no result, and a report states it. The
device allows that silence by default. A file that does not arrive at
all gives no result, so compare the number of `BIT_PERFECT` results with the files played.
A capture that holds several files (a host joins them, for example for
gapless playback) gives one report per file; `--json` prints one JSON object
per file, one per line. The exit status is 0 only when every file is
bit-perfect.

### Automate with bpcctl

`bpcctl` talks to the hardware checker over its serial port, the ST-LINK's
virtual COM port (115200 8N1); the port is `$BPC_PORT`, default
`/dev/ttyACM0`. `python3 -m bpcctl monitor` prints the log. `python3 -m
bpcctl run` plays a file and reads the results. For example, on Linux:

```
python3 -m bpcctl run -- aplay -D hw:CARD=Checker bpc1_pcm_096k_24bit.wav
```

Its exit status is the first of these that holds:

| Exit | When |
|---|---|
| 3 | the player command failed |
| 2 | no result: the device did not answer, or no stream arrived |
| 1 | any result is `NOT_BIT_PERFECT` or `UNKNOWN_SIGNAL`, or, with `--expect <n>`, fewer than n results are `BIT_PERFECT` or `DEVICE_OVERRUN` (a file that did not arrive at all) |
| 4 | not fully judged, play again: any result is `DEVICE_OVERRUN` (the device's receive buffer overflowed), or the reporting is incomplete: a stream's `results_lost` is above 0, fewer result lines arrived than its `stream stop` line's `results=`, or the timeout ended the wait before a stream stopped |
| 0 | at least one result arrived and all are `BIT_PERFECT` |

The device prints one result line per file and per stretch of unknown signal
(fields in the [firmware README](firmware/README.md#result-lines)); `--json`
prints each as a JSON object. A file that does not arrive at all gives no
result, so compare the number of `BIT_PERFECT` results with the files played
(`--expect <n>`). The device accepts silence before, between and after the
files, which hosts pad their streams with; a file muted to zeros then gives
no result either, so the count matters. Setting `allow_silence` to `false`
(see the [firmware README](firmware/README.md#allowed-silence)) makes any
silence outside a file `UNKNOWN_SIGNAL`. `bpcctl` also sets the formats the
device offers and `allow_silence`; see the [firmware README](firmware/README.md#formats).

### Checks

A change is done when the unit suite passes, a re-run generates
byte-identical files (the repeatability test), and the firmware builds
without warnings (see Build in the [firmware README](firmware/README.md)).
Before a release, all of these hold:

1. The unit suite passes (it includes the repeatability test on a sample of
   the files):

   ```
   python3 -m unittest discover -s tests -t .
   ```

2. The repeatability test passes on the full set: it generates and packages
   twice and compares every byte (see Determinism).

   ```
   BPC1_FULL_SET=1 python3 -m unittest tests.test_repeatability
   ```

3. The golden vectors in `vectors/` are up to date: the command prints
   `vectors match` and exits 0, or prints `vectors DIFFER` and exits 1.

   ```
   python3 -m generator.vectors --check
   ```

4. Every zip is under 2 GiB. Packaging stops with an error for a zip of
   2 GiB or more and does not write it (see Package a release), so all eight
   zips must build without an error.

## Known issues

- **DSD256 files longer than about 380 s.** The board's C checker and the
  generator compute the DSD payload differently once a file's position
  passes 2^32, which at DSD256 is after about 380 s. No published file is
  that long (the DSD files last under 13 s). Not resolved yet.
- **Zero-length audio packets: the fix is not tested on hardware.** USB
  Audio 2.0 lets a host send a zero-length packet in place of any packet.
  TinyUSB 0.21.0 then stopped receiving, and a local patch fixes that (see
  the [firmware README](firmware/README.md#tinyusb-patch)). The hosts tested
  so far never sent one, so the patch is checked against the code and the
  specification only.
- **A rate change during a stream is not tested on hardware.** The tested
  hosts close a stream (alt setting 0) before they set a new rate. If a host
  sets a new rate while its stream stays open, the firmware feeds the
  frames still in its buffer to the checker, ends the stream with
  `reason=rate` and starts a new one at the new rate; this is checked
  against the code only.
- **ffmpeg decodes some ALAC packets wrongly.** Decoders based on ffmpeg
  (8.0.1 and its current development version) and Apple's ALAC codec clamp
  the history of ALAC's adaptive Golomb coder on different values, so
  ffmpeg decodes some packets of Apple's encoder wrongly. Of the published
  files this hits the regular PCM files at 384, 705.6 and 768 kHz and the
  sweep files at 192 kHz 24-bit, 352.8 kHz 24-bit, 384, 705.6 and 768 kHz.
  It is an ffmpeg bug, reported as [ffmpeg issue 25727](https://code.ffmpeg.org/FFmpeg/FFmpeg/issues/25727).
  A player that decodes ALAC with ffmpeg fails those files; the WAV and AIFF
  of the same name hold the same samples. The project's files are made and
  checked with Apple's codec (see the
  [signal format](docs/bpc1.md#97-alac-mp4), section 9.7).

## Built on

The generator and the checker encode and decode ALAC with Apple's
[ALAC codec](https://github.com/macosforge/alac) (Apache-2.0), the
reference encoder and decoder, pinned at commit `c38887c` as a git
submodule in `generator/third_party/alac`.

The firmware uses:

- [TinyUSB](https://github.com/hathach/tinyusb) (MIT), by Ha Thach and
  contributors, release 0.21.0 with one local patch for zero-length audio
  packets (see the [firmware README](firmware/README.md))
- ST's [CMSIS device files for the STM32U5](https://github.com/STMicroelectronics/cmsis_device_u5)
  (Apache-2.0) and [STM32U5 HAL](https://github.com/STMicroelectronics/stm32u5xx_hal_driver)
  (BSD-3-Clause)
- Arm's [CMSIS 5](https://github.com/ARM-software/CMSIS_5) core headers
  (Apache-2.0)

`firmware/boards` and `firmware/families` follow TinyUSB's board files for
the STM32U5.

The payload hash of BPC1 uses the lowbias32 constants published by Chris
Wellons, and the channel keys are the first output of SplitMix64 (Sebastiano
Vigna), as the [signal format](docs/bpc1.md) describes.

## How it was written

Bit-Perfect Checker is written with [Claude Code](https://claude.com/claude-code), using Anthropic's Claude Opus and Claude Sonnet.

Occasional code reviews, for a second opinion, were done with OpenAI's
GPT-6 Astra in [Codex](https://github.com/openai/codex).

## Licence

MIT, see [LICENSE](LICENSE).

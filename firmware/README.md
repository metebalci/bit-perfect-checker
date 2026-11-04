# Firmware

Firmware for the hardware checker: a USB Audio Class 2 device that receives
the BPC1 test signal and reports whether it arrived bit-perfect. The first board is the
NUCLEO-U5A5ZJ-Q.

## Layout

| Path | Contents |
|---|---|
| `app/` | everything that is the same on every board: USB descriptors and audio class requests, the stream probe and checker, the log and console, LED patterns, load figures, the status a display shows |
| `bpc1/` | the BPC1 signal and the online checker engine in portable C, which host tests also build |
| `include/` | what `app/` needs from a board ([`board.h`](include/board.h)) and from a display ([`display.h`](include/display.h)) |
| `families/<family>/` | one MCU family: CPU flags, ST's CMSIS and HAL modules, HAL configuration, linker scripts, and the code all boards of the family share (USB controller and PHY, log UART, tick) |
| `boards/<board>/` | one board: `board.cmake` (family, MCU, crystal, linker script, sources) and `board.c` (clock tree, LEDs, which UART is the log) |
| `shields/<shield>/` | an add-on for a board, such as a display shield; none yet |
| `lib/` | pinned submodules: [TinyUSB](https://github.com/hathach/tinyusb) 0.21.0, ST's [CMSIS device files](https://github.com/STMicroelectronics/cmsis_device_u5) and [HAL](https://github.com/STMicroelectronics/stm32u5xx_hal_driver) for the STM32U5, Arm's [CMSIS 5](https://github.com/ARM-software/CMSIS_5) core headers, at the commits TinyUSB 0.21.0 tests with; `patches/` holds our fix to TinyUSB ([below](#tinyusb-patch)) |

| Board | Family | Display |
|---|---|---|
| `nucleo_u5a5zj_q` | `stm32u5` | none |

## What it does

The firmware is the USB audio function, the LEDs, the serial log and the BPC1
checker ([section 12.6](../docs/bpc1.md#126-verdicts)). It
enumerates as a USB Audio Class 2 output device at High-Speed, records what
the host sends, and checks every frame against the test signal:

- **Rates:** 44.1, 48, 88.2, 96, 176.4, 192, 352.8, 384, 705.6 and 768 kHz,
  stereo.
- **Formats:** 16-bit in 2-byte slots, 24-bit in 4-byte slots, 32-bit in
  4-byte slots and 24-bit in 3-byte slots (written `16/2`, `24/4`, `32/4`,
  `24/3`), all integer PCM; there is no floating-point format. By default
  the device offers all four, as alt settings 1 to 4 in that order. It can
  offer any of them, in any order, instead: see [Formats](#formats).
- **Clocking:** asynchronous, with an explicit feedback endpoint that always
  reports the nominal rate. The device has no audio clock, so the host's USB
  timing paces the stream.
- **No Feature Unit:** the host cannot hand volume to the device, so any
  volume change happens in the host's software, where the checker sees it as
  a body that is not bit-perfect.

For each stream it counts packets and frames, the average rate, packets lost
(microframes without a packet, and receive FIFO overflows), the bits in use,
and DoP markers. The checker ([`bpc1/bpc1_checker.h`](bpc1/bpc1_checker.h))
gets every frame as 32-bit left-justified words, with the host's rate and the
slot's bits, and starts over with each stream. It finds each BPC1 file in the
stream by its X-syncs, PCM or DoP, and gives each file its own verdict: hosts
such as iOS join files of the same rate into one stream. It proves that a
file arrived bit-perfect: a file that did not is `NOT_BIT_PERFECT`, and audio that belongs to no file, silence included, is
`UNKNOWN_SIGNAL`. It has no robust layer.

The USB controller runs in DMA mode. Its isochronous OUT endpoint holds one
transfer at a time and is re-armed after each packet, so **a host that sends
a packet late in one microframe and the next early in the following one can
make the device miss a packet.** On one Linux host's xHCI controller this
happened with 3- and 4-byte slots at 705.6 and 768 kHz, about one packet in
4 000 to 17 000, in holes of one or two microframes; everything up to
384 kHz, and every 16-bit stream, arrived complete. An iPad lost no packet at
any rate. A miss shows as `lost`, and the file is `NOT_BIT_PERFECT`.

Seen on Linux (snd-usb-audio, raw `hw:` playback): 16-bit files use alt 1,
24-bit files alt 4 (`S24_3LE`), and 32-bit files alt 2. Alt 2 and 3 are both
`S32_LE` with the same packet size, and Linux takes the first, so it never
selects alt 3; all 32 bits still arrive. A Linux stream carries 17 to
20 ms more than the file, inside the file's extent (see
[measurements](../docs/measurements.md#27-frames-beyond-the-file)).

### TinyUSB patch

The submodule stays at TinyUSB 0.21.0, unmodified. The build applies
[`lib/patches/tinyusb-audio-out-zlp.patch`](lib/patches/tinyusb-audio-out-zlp.patch)
to a copy of `src/class/audio/audio_device.c` in the build folder and
compiles that copy instead; a patch that no longer applies exactly fails the
build. The fix: with the DWC2 controller in DMA mode the audio driver copies
each received packet into its FIFO, and on a zero-length packet it returned
before re-arming the endpoint, so the device received nothing more until the
host selected an alt setting again. USB Audio Data Formats 2.0 (2.1 and
2.3.1.5) lets a host send a zero-length packet in place of any packet, and
the device must handle it. With the patch, a zero-length packet re-arms the
endpoint and counts like a microframe without a packet (`lost`).

## USB ID

The device reports the manufacturer "Mete Balci" and the product "Bit-Perfect
Checker"; on Linux the ALSA card is named `Checker`.

The default USB ID is **1209:0001, the [pid.codes](https://pid.codes/1209/0001/)
test PID**. It is not unique and is for in-house testing only: never use it on
a device that is distributed, sold or manufactured. CMake warns about it at
configure time and the boot line shows the ID in use. Set your own with:

```
cmake ... -DUSB_VID=0x1234 -DUSB_PID=0x5678
```

## Formats

Which formats the device offers, and in what order, decides what a host can
choose: a host picks one of the alt settings, often the first or the
deepest it supports. Offering only `24/3`, for example, makes the device
look like a 24-bit DAC with packed samples, and offering `16/2` alone like a
16-bit one.

- At boot, from the build: `-DUAC2_FORMATS=24/3,16/2` (default
  `16/2,24/4,32/4,24/3`).
- At run time, until the next reset: the console command
  `formats 24/3,16/2`, or `python3 -m bpcctl formats 24/3,16/2`. The device
  disconnects for 0.3 s and reconnects, so the host enumerates the new alt
  settings; `bpcctl` returns once a host has mounted it again.
  `formats default` goes back to the build's list.

## Allowed silence

By default (`allow_silence` `true`) silence outside every file's extent
gives no result, wherever it is: before the first file, between files and
after the last ([section 12.6](../docs/bpc1.md#126-verdicts)
defines silence). Hosts pad their streams with it; an iPad sends about
0.2 s of zeros before each file. Anything else outside a file gives
`UNKNOWN_SIGNAL`. With `allow_silence` `false`, every stretch outside a
file, silence included, gives `UNKNOWN_SIGNAL`. The setting never applies inside
a file: silence that replaces part of a file's body is a difference like any
other and gives `NOT_BIT_PERFECT` under either setting.

- At boot, from the build: `-DALLOW_SILENCE=OFF` (default `ON`).
- At run time, until the next reset: the console command
  `allow_silence true` or `allow_silence false`, or
  `python3 -m bpcctl allow-silence false`. It does not reconnect the device
  and applies to the next stream.

The setting in effect is on the `boot` line, the `status` answer and every
`stream start` line (`allow_silence=true` or `allow_silence=false`).

## Build

Needs `arm-none-eabi-gcc` with newlib, CMake 3.21 or later, Ninja and
`patch` ([TinyUSB patch](#tinyusb-patch)). From the repository root:

```
git submodule update --init
cmake -S firmware -B firmware/build/nucleo_u5a5zj_q -G Ninja -DBOARD=nucleo_u5a5zj_q
cmake --build firmware/build/nucleo_u5a5zj_q
```

`-DSHIELD=<shield>` adds a shield. The build writes
`bpc-<board>[-<shield>].elf`, `.bin` and `.hex` to the build folder. It must
finish without compiler or linker warnings: the firmware's own sources build
with `-Werror`.

## Flash

With ST's [STM32CubeProgrammer](https://www.st.com/en/development-tools/stm32cubeprog.html)
installed in its default folder:

```
cmake --build firmware/build/nucleo_u5a5zj_q --target flash
```

This runs `STM32_Programmer_CLI -c port=SWD mode=UR -w <bin> 0x08000000 -v -rst`.
Without it, copy the `.bin` to the ST-LINK's USB drive (`NOD_U5A5ZJ`).
OpenOCD 0.12.0 can connect to the STM32U5A5 but cannot program its flash: its
flash driver does not know the device ID 0x481.

## Release

The firmware is to be released as `bit-perfect-checker-firmware-<version>.zip`.
No binary is published yet (see [USB ID](#usb-id)).

Firmware releases are tagged `firmware-v<major>.<minor>.<patch>`, the first
`firmware-v1.0.0`. The build takes its version from
`git describe --tags --match 'firmware-v*' --always --dirty` without the
prefix, and the device reports it as `fw=` in its `boot` and `version` lines:
`1.0.0` at the tag, `1.0.0-3-gabc1234` three commits after it, the commit hash
alone without a firmware tag, each with `-dirty` when the tree has
uncommitted changes. Other tags, such as the test files', do not change it.

## Serial log and commands

The log is on the board's debug UART: the ST-LINK virtual COM port on the
Nucleo, 115200 8N1. It is meant to be read by scripts as well as people.

### Lines from the device

One line per event, `<seconds>.<milliseconds> <event> [word ...]`; a word is a
`key=value` field or a plain word. New fields may be added; existing ones keep
their meaning.

| Event | Meaning |
|---|---|
| `boot fw=<version> board=<board> usb=<vid>:<pid> allow_silence=<true/false>` | start-up, with the firmware version ([Release](#release)) and the [allowed silence](#allowed-silence) |
| `led <colour> <pin>` | the LED test at start-up: red, green, blue, each for 0.3 s |
| `formats <list>` | the formats offered, as alt settings 1 to n; at boot, and after a `formats` command, with `changed=0` or `changed=1` |
| `usb mounted speed=high` | enumerated; `speed=full` means the High-Speed link failed |
| `usb unmounted`, `usb suspended`, `usb resumed` | bus state |
| `usb reset` | the host reset the bus while a stream ran or an alt setting was open: the stream stops (`reason=reset`), and the host enumerates the device again |
| `usb disconnected`, `usb connecting` | the device leaves the bus to change its formats, and comes back |
| `rate <Hz>` | the host set the sample rate; at boot, the rate the device reports before a host sets one (48000) |
| `rate unsupported=<Hz>` | the host asked for a rate that is not offered (stalled) |
| `alt <n> slot=<bytes> res=<bits>` | the host selected a streaming alt setting; `alt 0` closes it |
| `stream start id=<n> rate=<Hz> alt=<n> slot=<bytes> res=<bits> allow_silence=<true/false>` | the first packet of stream `<n>`, counted from 1 since boot, with the allowed silence in effect |
| `stat id=<n> rate=<Hz> frames=<n> pkts=<n> bits=<n> dop=<0/1> lost=<n> ovf=<n> <load>` | every second while streaming: average rate so far, frames this second, packets so far, bits in use this second, DoP, losses so far, and the [load figures](#load-figures) of this second |
| `lock kind=<PCM/SWEEP/DSF/DFF/DOP> dop=<0/1> rate=<Hz> depth=<bits> seed=0x<hex> block=<n> frame=<n>` | the checker found a file: its kind and identity from the first X-sync it read, whether it came as DoP, (`rate` is the file's, the DSD bit rate for DSD kinds; `depth` is 1 for DSD), that X-sync's block, and the received frame it starts at |
| `depth bits=<n>` | the bits in use changed |
| `dop on`, `dop off` | more or fewer than half of the frames carry DoP markers |
| `lost packets=<n> total=<n>` | microframes passed without a packet (a zero-length packet counts as none) |
| `overflow packets=<n> total=<n>` | the receive FIFO was full, so data was overwritten |
| `result seq=<n> stream=<n> file=<n> verdict=<verdict> kind=<kind> ...` | **the outcome of one file**: see [Result lines](#result-lines) |
| `stream stop id=<n> reason=<why> dur=<s> frames=<n> pkts=<n> rate=<Hz> fpp=<min>-<max> lost=<n> missed=<n> holes=<n> maxhole=<n> doubled=<n> ovf=<n> outside=<frames> silent=<frames> results=<n> results_lost=<n>` | end of a stream, after its last `result` line: `alt` (alt setting changed), `idle` (no packet for 50 ms), `rate` (the host set another rate during the stream), `reset` or `pause`; `fpp` is frames per packet; `holes` counts the runs of microframes without a packet and `maxhole` is the longest; `outside` is the number of frames of the stream outside every file's extent ([12.6](../docs/bpc1.md#126-verdicts)), so host padding can be measured; `silent` is the number of those frames that were allowed silence, which gave no result (0 unless `allow_silence` is `true`); `results` is the number of result lines of the stream, and `results_lost` the number of results of the stream the checker dropped because its queue of finished results was full |
| `stall <ms>` | the [`stall` command](#commands-to-the-device) started its busy-wait of `<ms>` milliseconds |
| `run start` | a new run begins: the user button was pressed, the `mark` command arrived, or the board reset; green and red go off ([LEDs](#leds)) |
| `usb unsupported get/set entity=<id> ctrl=<n> req=<n>` | a class request the device does not answer |
| `log lost=<n>` | log lines dropped because the queue was full |

`lock` comes from the checker four times a second at most. The log keeps
room in its queue that only `result` and `stream stop` lines may use, so
these are never dropped when other lines fill it.

### Result lines

One `result` line per file the checker finds. The checker finishes a file
once it has passed the file's end (the block flagged last) or the next
file's first X-sync, or at the end of the stream for a file still open then;
its line comes when the checker has read past the file's extent (one block
after its end), the next file's first X-sync or the next result, or when the
stream stops, whichever is first, so that the stream's last line can take
its last `lost` and `ovf` (below). Each stretch of the stream
outside every file's extent (also at the stream's start and end) gets one
result line with verdict `UNKNOWN_SIGNAL`, except a stretch that is silence
only while silence is [allowed](#allowed-silence) (`allow_silence` `true`,
the default): it gets none, and a stream of silence only then has no result
line (`results=0`). With `allow_silence` `false`, a stretch of silence gets
`UNKNOWN_SIGNAL` like any other, and a stream of silence only gets one
result line. A file that
does not arrive at all gets no result line, so a script compares the number
of `BIT_PERFECT` results with the files played (`bpcctl run --expect`). The
stream's `stream stop` line follows its last result line, and its
`results=` says how many there were.

```
result seq=<n> stream=<n> file=<n> verdict=<verdict> kind=<kind> dop=<0/1>
  seed=0x<hex> nch=<n> file_rate=<Hz> depth=<bits> rate=<Hz> alt=<n>
  slot=<bytes> res=<bits> bits=<n> end=<end> diff=<s> lost=<n> ovf=<n>
  dur=<s>
```

(one line in the log, the fields always in this order). A number is 0 when
it is not known; an `UNKNOWN_SIGNAL` line has `kind=NONE`, `end=STOP`, `diff=-`
and 0 in the file's fields. The fields:

| Field | Meaning |
|---|---|
| `seq` | counts result lines since boot, from 1 |
| `stream` | the stream it belongs to: the `id=` of `stream start`, `stat`, `stream stop` and `status` |
| `file` | the result's place in its stream, from 1 |
| `verdict` | `BIT_PERFECT`, `NOT_BIT_PERFECT`, `UNKNOWN_SIGNAL` or `DEVICE_OVERRUN`, below |
| `kind` | the file's kind from its X-sync: `PCM`, `SWEEP` (the PCM code sweep, [signal format](../docs/bpc1.md#75-expected-word-pseudocode)), `DSF`, `DFF`, `DOP`, or `NONE` |
| `dop` | 1 when the file came as DoP: a DSF file played as DoP gives `kind=DSF dop=1` |
| `seed`, `nch` | the file's seed and channel count, from its X-sync |
| `file_rate`, `depth` | the file's rate (frames per second for PCM, DSD bits per second per channel for DSD kinds) and depth (16, 24, 32; 1 for DSD) |
| `rate`, `alt`, `slot`, `res` | what the host chose: the frame rate, alt setting, slot bytes, resolution |
| `bits` | the bits in use of the received words over the file's body, for PCM and DoP alike (DoP in 3- or 4-byte slots: 24 when the padding below it is zero) |
| `end` | how the file ended: `LAST` (its block flagged last arrived), `NEXT` (another file followed), `BACK` (the block index went back), `STOP` (the stream stopped first) |
| `diff` | the position in the file of the first body unit that did not arrive as expected, in seconds from the file's start as generated, 3 decimals, rounded half up (PCM: frame index / rate; DSD kinds: byte index x 8 / DSD rate): the earliest of the body's first unit, when the rate or channel count is wrong or the first body block's X-sync or the lead-in X-sync before it is missing; the first body unit that differs or did not arrive; and the first unit after the body, when the X-sync after the body is missing. Where a limit X-sync is broken or cut off, the body runs from after the last lead-in X-sync received to the first tail X-sync received. `-` for `BIT_PERFECT`, `UNKNOWN_SIGNAL` and `DEVICE_OVERRUN` (12.6) |
| `lost`, `ovf` | packets lost and receive FIFO overflows that fall in the result's frames, by their place in the stream: a lost packet counts in the result of the first frame after it (a doubled microframe takes back the hole before it), an overflow in the result of the frame where the checker learns of it, normally the one it marks `DEVICE_OVERRUN`. Those between two results count in the later one; the stream's last result line also takes those after every result and those in frames not read before the stream stopped. A stream's result lines add up to its `stream stop` line's `lost` and `ovf`, except with `allow_silence` `true` when the stream ends in allowed silence after its last result: the losses there are on no result line, only on the `stream stop` line. The verdict follows the frames, not these counts |
| `dur` | a received duration, unlike `diff`, which is a position in the file: the file's received frames, from its first X-sync to its end, at most to the end of its extent ([12.6](../docs/bpc1.md#126-verdicts)), divided by `rate`; seconds, 3 decimals |

The limits of `lost`, `ovf` and the frames checked:

- The device keeps up to 1024 loss events not yet charged to a result, and
  its USB interrupt up to 64 not yet taken by the main loop. Only when one
  queue is full, which takes more than 64 holes while the main loop is held
  up (as by `stall`) or more than 1024 in files not yet finished, do later
  events merge into the newest one of their stream, so that their counts
  can land on the result line of an earlier file.
- A doubled microframe takes back the hole before it: the USB interrupt
  keeps a hole until its microframe has passed, so the two always meet. A
  stream whose first packet is read in the next microframe has no hole
  before the doubled microframe that follows, which then shows as
  `lost=-1`, on the stream's first result line and its `stream stop` line.
- When the host selects another alt setting, alt 0 included, TinyUSB clears
  the receive FIFO before the firmware hears of it
  (`lib/tinyusb/src/class/audio/audio_device.c:1123`), so the frames still
  queued, at most the FIFO's fill (about 0.25 ms of audio, measured at
  768 kHz), do not reach the checker. For an intact file these are frames of
  its tail, and the verdict is unaffected.

None of these changes the totals: a stream's result lines still add up to
its `stream stop` line, except for the allowed silence at its end (above).

For example, a 24-bit 48 kHz file that overran the receive FIFO after a
`stall 200` (time stamps left out; the `stream stop` line follows the
`result` line):

```
result seq=30 stream=30 file=1 verdict=DEVICE_OVERRUN kind=PCM dop=0 seed=0x42504331 nch=2 file_rate=48000 depth=24 rate=48000 alt=4 slot=3 res=24 bits=24 end=LAST diff=- lost=0 ovf=684 dur=12.458
stream stop id=30 reason=alt dur=12.649 frames=607164 pkts=101194 rate=48000.00 fpp=6-6 lost=0 missed=0 holes=0 maxhole=0 doubled=0 ovf=684 outside=0 silent=0 results=1 results_lost=0
```

The verdicts, from the BPC1 specification
([section 12.6](../docs/bpc1.md#126-verdicts)) and the device, in
their order of precedence; only `BIT_PERFECT` is a pass:

| Verdict | Meaning |
|---|---|
| `UNKNOWN_SIGNAL` | the units belong to no file: outside every file's extent, silence included unless [allowed](#allowed-silence) (a DoP file received without markers has no readable X-sync and gives it) |
| `DEVICE_OVERRUN` | the device's own receive FIFO overflowed between the file's first received unit and its end (or, with no file open, over the stretch), which overwrites frames not read yet, and no difference of the file came before the overflow: the file is not judged; play it again. A file whose first difference came before the overflow is `NOT_BIT_PERFECT`, with that difference's `diff` |
| `BIT_PERFECT` | the received rate and channel count are the file's, the body arrived as one run of consecutive units, every unit equal to the expected one in every channel, and the X-syncs before and after the body arrived intact (12.6 a to d) |
| `NOT_BIT_PERFECT` | the file was found and 12.6 (a), (b), (c) or (d) does not hold; `diff` says where the first difference was. Packets lost on the bus show here, with `lost` above 0 |

The device does not search for inverted X-syncs, so an inverted, rounded,
dithered or resampled stream may give `UNKNOWN_SIGNAL`.

The verdict is exact: every unit of the body is compared.

### Load figures

From the CPU's cycle counter (DWT), in every `stat` line for that second:

| Field | Meaning |
|---|---|
| `isr_pct` | time in the USB interrupt, percent |
| `isr_max` | CPU cycles of the longest USB interrupt |
| `ck_cpf` | checker cycles per frame |
| `ck_pct` | time in the checker, percent |
| `loop_max` | microseconds of the longest main-loop iteration, interrupts included |
| `fifo_max` | the fullest the receive FIFO got, as microseconds of audio |

The CPU runs at 160 MHz. The receive FIFO holds 32 760 bytes, 5.3 ms at
768 kHz in 4-byte slots; `fifo_max` near that means the main loop is close
to falling behind.

The average rate is measured against the host's microframe clock, so it
shows the rate the host actually sends at. The bits in use are counted from
the most significant bit of the slot down to the lowest bit set in any
sample: 16-bit content in a 4-byte slot shows `bits=16`.

`lost` is `missed - doubled`. `missed` counts microframes that passed without
a packet; `doubled` counts packets read back in the same microframe as the
one before. If a packet's completion is ever read in the following
microframe, it adds one to each, and the net stays right.

### Commands to the device

One command per line, ended by CR or LF. Each answer is one line:

| Command | Answer |
|---|---|
| `status` | `status usb=<state> speed=<high/full> rate=<Hz> alt=<n> slot=<bytes> res=<bits> streaming=<0/1> id=<n> lost=<n> ovf=<n> formats=<list> allow_silence=<true/false>` |
| `result` | the last `result` line again, or `result none` |
| `mark <text>` | `mark <text>`, to tie a script's steps to the log; it also starts a new run (`run start`, [LEDs](#leds)) |
| `formats [<list> \| default]` | `formats <list>`; with a list, offer those formats from now on (`changed=1`, then the device reconnects) |
| `allow_silence true\|false` | `allow_silence <value>`; sets the [allowed silence](#allowed-silence) for the next stream |
| `version` | `version fw=<version> board=<board> usb=<vid>:<pid>` |
| `stall <ms>` | test command: `stall <ms>` and then the main loop busy-waits for `<ms>` milliseconds (1 to 2000) with interrupts on, so the USB interrupt keeps filling the receive FIFO while nothing drains it; used to force `DEVICE_OVERRUN` on purpose. Other values answer `error stall=<value>` |
| `help` | `help commands=status,result,mark,formats,allow_silence,version,help test=stall` |

Anything else gets `error unknown-command=<word>` or `error line-too-long`.
Send a newline before the first command after opening the port, since the
line may hold noise from a reset.

### From a script

[`bpcctl`](../bpcctl) does this with the Python standard library on Linux
and macOS, and with pyserial on Windows:

```
python3 -m bpcctl run -- aplay -D hw:CARD=Checker out/bpc1_pcm_096k_24bit.wav
```

`python3 -m bpcctl allow-silence true|false` sets the allowed silence the
same way; the JSON carries `"allow_silence"` as `true` or `false`.

To test a format, choose it first: `python3 -m bpcctl formats 24/3` returns
once the host has the device back, and `run` then plays.

`run` sends a unique `mark`, runs the player command, waits for the
`result` lines of the streams that started after the mark (a stream is done
at its `stream stop` line, which says how many results it had), prints
them, and exits with 3 if the player failed, else 2 if no result arrived,
else 1 when any result is `NOT_BIT_PERFECT` or `UNKNOWN_SIGNAL` (or fewer
than N results are `BIT_PERFECT` or `DEVICE_OVERRUN` with `--expect N`),
else 4 when any is `DEVICE_OVERRUN` or the reporting is incomplete (a
`stream stop` line with `results_lost` above 0, fewer result lines than its
`results=`, or a stream unfinished at the timeout), else 0: at least one
result arrived and all are `BIT_PERFECT`. `--json` prints each line as one JSON object. The port
is `$BPC_PORT`, else `/dev/ttyACM0`; `--port` overrides it. Without
`bpcctl`, the same steps by hand: open the port, send `mark <id>`, wait
for the echo, play, then wait for the `stream stop` line after the
`result` lines.

On Linux, `hw:CARD=Checker` (case-sensitive) reaches the device without PipeWire or any
format conversion.

## LEDs

| LED | Meaning |
|---|---|
| blue, fast blink | not enumerated |
| blue, slow blink | enumerated, waiting for a stream |
| blue, steady | a stream runs |
| red, steady | a file of the run is `NOT_BIT_PERFECT`, from its first difference on |
| red, blinking | the run has an `UNKNOWN_SIGNAL` result |
| red and green, blinking together | the run has a `DEVICE_OVERRUN` result |
| green, steady | the run's results are all `BIT_PERFECT` |
| green and red off | nothing judged yet |
| all three on | fault, stopped |

Green and red show the current **run**: every result since the last press of
the user button (B1 on the Nucleo), the console command `mark`, or a reset.
They do not depend on how the host splits files into streams, and they keep
the result after a stream stops, until the next run starts. They show the
first of these that holds, from red steady down. Once red is on, it stays red
until the next run; it can only change from blinking to steady. With
`allow_silence` `true` a file muted to zeros gives no result, so green can
stay on; only `bpcctl run --expect` catches it. A new run is
logged as `run start` and turns green and red off. A stack that grows past
its 16 KB also stops the firmware as a fault.

## Adding a board

Each case builds on the one before.

**Another board of a family that exists** (another STM32U5 with the HS PHY):

1. Copy `boards/nucleo_u5a5zj_q/` to `boards/<board>/`.
2. In `board.cmake`, set `MCU_VARIANT` (it picks the CMSIS header and
   startup file), `HSE_VALUE` and `LINKER_SCRIPT`. Add a linker script to
   `families/stm32u5/linker/` if the memory differs.
3. In `board.c`, write the clock tree, the LED pins (`board_led_name()`
   returns NULL for a colour the board lacks), the log UART's pins and its
   `<UART>_IRQHandler`, and the USB PHY's clock.
4. Build with `-DBOARD=<board>`.

**A board of another family** (for example the STM32F723E-DISCO, an
STM32F7): add `families/<family>/` like `families/stm32u5/`, with
`family.cmake` (CPU flags, TinyUSB's `CFG_TUSB_MCU`, the HAL modules),
`family.c` (USB controller and PHY start-up, `board_usb_microframe()`, the
log UART, tick, interrupts), the HAL configuration and linker scripts; add
that family's CMSIS and HAL to `lib/` as submodules, pinned to the commits
TinyUSB's `tools/get_deps.py` lists. Then add the board as above.

**A board with a display** (a Discovery or evaluation kit): implement
[`display.h`](include/display.h) in the board's folder and list the file in
`board.cmake`:

```
list(APPEND DISPLAY_SOURCES ${CMAKE_CURRENT_LIST_DIR}/display.c)
```

`display_update()` gets the [status](app/status.h) after every change; it
runs in the main loop and must return within a millisecond or so, because the
receive FIFO holds about 5 ms of audio at 768 kHz.

**A board with a display shield** (the Nucleo with an Arduino-header TFT):
add `shields/<shield>/shield.cmake`, which appends the display driver to
`DISPLAY_SOURCES` the same way, and build with
`-DBOARD=<board> -DSHIELD=<shield>`. The shield uses the bus and pins the
board's Arduino header provides; the board names them in its `board.c` or a
header next to it.

## How it was written

See [How it was written](../README.md#how-it-was-written) in the main README.

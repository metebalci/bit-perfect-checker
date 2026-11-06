# Firmware

Firmware for the hardware checker: a USB Audio Class 2 device that receives
the BPC1 test signal and reports whether it arrived bit-perfect. The boards
are the NUCLEO-U5A5ZJ-Q, the STM32F723E-DISCO and the STM32U5G9J-DK2
([Boards](#boards)); the last two have a screen.

## Layout

| Path | Contents |
|---|---|
| `app/` | everything that is the same on every board: USB descriptors and audio class requests, the stream probe and checker, the log and console, LED patterns, load figures, the status, and the status screen's content and drawing |
| `bpc1/` | the BPC1 signal and the online checker engine in portable C, which host tests also build |
| `include/` | what `app/` needs from a board ([`board.h`](include/board.h)) and from a display ([`display.h`](include/display.h)), and what the BPC1 generator needs from a board with a USB host port ([`board_host.h`](include/board_host.h)) |
| `families/<family>/` | one MCU family: CPU flags, ST's CMSIS and HAL modules, HAL configuration, linker scripts, and the code all boards of the family share (USB controller and PHY, log UART, tick) |
| `boards/<board>/` | one board: `board.cmake` (family, MCU, crystal, linker script, sources) and `board.c` (clock tree, LEDs, button, VBUS, which UART is the log), and for a board with a display its panel driver, screen layout and touch driver |
| `shields/<shield>/` | an add-on for a board, such as a display shield; none yet |
| `bpc1-generator/` | the BPC1 generator, a USB host that is to play the BPC1 test signal to a checker: its own CMake project on the same boards and families ([its README](bpc1-generator/README.md)) |
| `cmake/` | the toolchain file, the version stamp, the flash target, and `common.cmake`, which the checker and the BPC1 generator share |
| `tools/` | `fontgen.py`, which turns [Spleen](https://github.com/fcambus/spleen) bitmap fonts into C arrays at build time, and `package.py`, which builds the release zip ([Release](#release)) |
| `lib/` | pinned submodules: [TinyUSB](https://github.com/hathach/tinyusb) 0.21.0, ST's CMSIS device files ([STM32U5](https://github.com/STMicroelectronics/cmsis_device_u5), [STM32F7](https://github.com/STMicroelectronics/cmsis_device_f7)) and HAL ([STM32U5](https://github.com/STMicroelectronics/stm32u5xx_hal_driver), [STM32F7](https://github.com/STMicroelectronics/stm32f7xx_hal_driver)), Arm's [CMSIS 5](https://github.com/ARM-software/CMSIS_5) core headers, at the commits TinyUSB 0.21.0 tests with, and Spleen 2.2.0 for the screens' fonts; `patches/` holds our fix to TinyUSB ([below](#tinyusb-patch)) |

| Board | Family | Display |
|---|---|---|
| `nucleo_u5a5zj_q` | `stm32u5` | none |
| `stm32f723e_disco` | `stm32f7` | 1.54" 240x240 with touch |
| `stm32u5g9j_dk2` | `stm32u5` | 5" 800x480 with touch |

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
- **Connection:** each board reads its user USB port's VBUS in the main loop,
  since the USB controller does not sense it. When VBUS has been gone for
  50 ms the device reports not connected, so a pulled cable is told apart
  from a host that only suspends the bus, and a new run starts. Without
  VBUS the device keeps its D+ pull-up off, as USB 2.0 (7.1.5.1) requires,
  also from boot; VBUS back for 50 ms turns it on (`usb connecting`), and
  the host enumerates the device again. The pins: PC2 (VBUS_SENSE, VBUS
  divided by 7.61, read with ADC1) on the NUCLEO-U5A5ZJ-Q, PB13 (VBUS of
  CN19) on the STM32F723E-DISCO, and PA1 (USBC_DETECT) on the
  STM32U5G9J-DK2.

For each stream it counts packets and frames, the average rate, packets lost
(microframes without a packet, and receive FIFO overflows), the bits in use,
and DoP markers. The checker ([`bpc1/bpc1_checker.h`](bpc1/bpc1_checker.h))
gets every frame as 32-bit left-justified words, with the host's rate and the
slot's bits, and starts over with each stream. It finds each BPC1 file in the
stream by its X-syncs, PCM or DoP, and gives each file its own verdict: hosts
such as iOS join files of the same rate into one stream. It proves that a
file arrived bit-perfect: a file that did not is `NOT_BIT_PERFECT`, and audio that belongs to no file, silence included, is
`UNKNOWN_SIGNAL`. It has no robust layer.

The device takes every rate up to 768 kHz. Whether every USB packet reaches
it depends on the host. An iPad lost no packet at any rate, 768 kHz included,
so the iPad is the reference test for the highest rates. On one Linux host's
xHCI controller a few packets went missing. Up to 192 kHz no packet was lost
in any stream; at 352.8 and 384 kHz a stream lost a few packets now and then;
at 705.6 and 768 kHz streams lost a few packets each with 3- and 4-byte slots
(about one in 3 800 to 17 000, in holes of one or two microframes) and
sometimes with 2-byte slots too.

The USB controller runs in DMA mode. Its isochronous OUT endpoint holds one
transfer at a time and is re-armed after each packet, so **a host that sends
a packet late in one microframe and the next early in the following one can
make the device miss a packet.** A miss shows as `lost`, so it is told apart
from an altered sample. A miss in a file's body makes the file
`NOT_BIT_PERFECT`; a miss in its lead-in or tail is counted in `lost` but does
not change the verdict ([12.6](../docs/bpc1.md#126-verdicts)).

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

## Boards

Each board has the ST-LINK's USB port, which flashes it and carries the serial
log, and a user USB port, which goes to the host under test; connect both.
The user port is a High-Speed device port with the MCU's on-chip PHY. The
NUCLEO-U5A5ZJ-Q's user port is a USB-C connector.

### STM32F723E-DISCO

- **Ports:** the user port is CN19 (micro-AB, USB OTG HS). The checker
  leaves the port's VBUS switch off, since it is a device on that port; the
  [BPC1 generator](bpc1-generator/README.md) is a host there and switches it
  on. The ST-LINK port is CN1, the board's default supply. The log is on USART6
  (PC6, PC7), behind the ST-LINK's virtual COM port.
- **LEDs:** red LD5 (PA7), green LD6 (PB1) and blue LD1 (PA5, through the
  solder bridge SB14), as in [LEDs](#leds).
- **Button and touch:** B1 (PA0) starts a new run. So does a tap anywhere on
  the touch screen, a FocalTech FT6x36 or FT3x67 on I2C3 that the firmware
  finds at start-up; nothing else uses touch.
- **Screen:** the 1.54" 240x240 panel (ST7789H2, which holds the frame itself)
  on the FMC's 16-bit bus, drawn by the CPU; see [Screen](#screen). A fault
  switches its backlight off.
- **MCU:** a Cortex-M7 at 216 MHz from the board's 25 MHz oscillator. The data
  cache is off and the instruction cache on, so the USB DMA needs no cache
  maintenance or special buffer placement. The image is linked at the flash's
  ITCM alias ([Flash](#flash)).

### STM32U5G9J-DK2

- **Ports:** the user port is CN2 (USB-C, USB OTG HS). The log is on USART1
  (PA9, PA10), behind the ST-LINK's virtual COM port.
- **LEDs:** red LD2 (PD2) and green LD3 (PD4), both lit by a low pin. There is
  no blue LED: its row in [LEDs](#leds) does not apply, red and green show
  the run as on the other boards, and the screen shows whether the device is
  connected, waiting or checking. A fault lights red and green steady.
  The boot LED test prints no line for blue.
- **Button and touch:** B2 (PC13) starts a new run (B1 is the board's reset).
  So does a tap anywhere on the touch screen, a Goodix GT911 on I2C2; the
  firmware only reads the controller and never writes its configuration.
- **Screen:** the 5" 800x480 panel (RK050HR18, parallel RGB, no command
  interface) on the LTDC, drawn by the CPU into a frame buffer in SRAM6, a
  bank that no other bus master uses, so the LTDC's reads never wait for the
  USB DMA. The buffer has one byte per pixel and a fixed RGB332 palette, so
  black, white, red and blue are exact; see [Screen](#screen). The panel
  shows full green lighter than the F723's panel does, so the palette scales
  its pure greens: full green is (0, 208, 0). The build option
  `PANEL_GREEN_MAX` sets the 208 (0 to 255; 255 is no correction), and a
  build with a correction logs `display green=<n>` at start-up. A fault switches its backlight off.
- **MCU:** the STM32U5G9, with the same clock tree and USB set-up as the
  NUCLEO-U5A5ZJ-Q.
- **ST's demonstration firmware** is replaced when the board is flashed;
  restoring it needs ST's demonstration package for the board.

## Screen

The F723's 240x240 screen and the left 480x480 of the DK2's screen show the
same, the DK2's at twice the size. The right 320x480 of the DK2's screen is
reserved and black. The content is built in
[`app/screen_model.c`](app/screen_model.c) from the [status](app/status.h),
and [`app/screen.c`](app/screen.c) draws it through the
[panel interface](include/panel.h). The screen follows the run, as green and
red do ([LEDs](#leds)). White text on black, in Spleen's fonts:

- **Band:** one line in a thick frame, with a word for what is going on:
  `NOT CONNECTED` (no host has set the device up, or the cable is out),
  `WAITING` (connected, no stream) and `CHECKING` (a stream is open).
- **Frame colour:** blue until the run has a result, green while every
  result is `BIT_PERFECT`, red as soon as anything failed (a file's first
  difference, `NOT_BIT_PERFECT`, `UNKNOWN_SIGNAL` or `OVERRUN`). It turns red
  at once, also while a stream is open. Only blue, green and red are used.
- **Hourglass:** right of `WAITING` and `CHECKING`, an outline that blinks
  with the slow blink of the LEDs, so in `WAITING` it lights with the blue
  LED. In `CHECKING` it blinks while packets arrive and stays lit when none
  has arrived for 20 ms. `NOT CONNECTED` has none.
- **Labels:** under the band, in red and steady, each only if it occurred in
  the run: `UNKNOWN` on the left (for `UNKNOWN_SIGNAL`) and `OVERRUN` on the
  right. `NOT_BIT_PERFECT` has no label: the red frame shows it.
- **File lines:** two lines, the file as its header gives it: its kind
  (`PCM`, `PCM SWEEP` or `DoP`), then its depth and rate in kHz without the
  unit (`24/96`, `16/44.1`) or, for DoP, its DSD rate (`DSD64`, `DSD128`,
  `DSD256`). While a stream is open they show the file the checker locked
  onto; otherwise the last result's file while it belongs to the current run.
  A new run clears them.
- **Version:** at the bottom, the firmware version as `fw=` shows it
  ([Release](#release)), without the label.
- **Splash:** for about 2 s after start-up, `BIT-PERFECT`, `CHECKER`, the
  project's web address, the version, and three squares, red, green and blue,
  that light in the order and timing of the boot LED test and stay lit; then
  the status screen. The splash does not hold up the USB start.
- **Fault:** the backlight goes off.

The screen has no counters and no detail rows; the serial log has them.

**Drawing never holds up USB.** The receive FIFO holds about 5 ms of audio at
768 kHz, and the main loop must empty it. Drawing is cut into small units
(a strip of the band, one character), and `display_task()` runs on every
pass of the main loop and starts a unit only if it fits in about 0.2 ms with
the USB interrupts that come meanwhile. It draws only while the FIFO holds
less than 0.5 ms of audio, and redraws only the characters that changed.
Each unit's time is measured once at start-up. The hourglass's blink is one
such unit.

The build options `-DDISPLAY_LOG=ON` and `-DDISPLAY_STRESS=ON` log a line
per redraw and redraw the whole screen continuously, to measure this
([`app/screen.h`](app/screen.h)).

## USB ID

The device reports the manufacturer "Mete Balci" and the product "Bit-Perfect
Checker"; on Linux the ALSA card is named `Checker`.

The device uses **16C0:27E0, Objective Development's shared VID/PID pair for
audio class devices**, under the serial-number rules of its
[USB-IDs-for-free.txt](https://github.com/obdev/v-usb/blob/master/usbdrv/USB-IDs-for-free.txt).
Devices that share the pair tell themselves apart by their serial number
string, which must start with an Internet domain or e-mail address of their
maker, ended by a colon. This firmware's serial number is
`bpc.metebalci.com:` followed by the chip's unique ID as 24 hex digits, and
host software that looks for the device matches the serial number as well as
the IDs. The boot line shows the ID in use.

Anyone who distributes a modified firmware sets `USB_SERIAL_PREFIX` to a
domain or e-mail address of their own:

```
cmake ... -DUSB_SERIAL_PREFIX=example.org:
```

`USB_VID` and `USB_PID` set another VID:PID.

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
  disconnects for 0.3 s and reconnects (not without VBUS), so the host
  enumerates the new alt settings; `bpcctl` returns once a host has mounted it again.
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
`patch` ([TinyUSB patch](#tinyusb-patch)). A board with a display also needs
Python 3, which converts the fonts at build time. From the repository root:

```
git submodule update --init
cmake -S firmware -B firmware/build/nucleo_u5a5zj_q -G Ninja -DBOARD=nucleo_u5a5zj_q
cmake --build firmware/build/nucleo_u5a5zj_q
```

`<board>` is a folder in `boards/`: `nucleo_u5a5zj_q`, `stm32f723e_disco` or
`stm32u5g9j_dk2`. `-DSHIELD=<shield>` adds a shield. The build writes
`bpc-<board>[-<shield>].elf`, `.bin` and `.hex` to the build folder. It must
finish without compiler or linker warnings: the firmware's own sources build
with `-Werror`.

## Flash

With ST's [STM32CubeProgrammer](https://www.st.com/en/development-tools/stm32cubeprog.html)
installed in its default folder, name the board's ST-LINK by its serial
number, which `STM32_Programmer_CLI -l st-link` lists, and flash:

```
cmake -DSTLINK_SN=<serial number> firmware/build/nucleo_u5a5zj_q
cmake --build firmware/build/nucleo_u5a5zj_q --target flash
```

This runs `STM32_Programmer_CLI -c port=SWD sn=<serial number> mode=UR -w <bin> 0x08000000 -v -rst`.
Without `STLINK_SN` the target refuses to run, so that it never writes to
another board that happens to be connected.
Without STM32CubeProgrammer, copy the `.bin` to the ST-LINK's USB drive:
`NOD_U5A5ZJ` on the NUCLEO-U5A5ZJ-Q, `DIS_F723IE` on the STM32F723E-DISCO.
The STM32U5G9J-DK2's ST-LINK shows no drive, so flash it with
STM32CubeProgrammer.

Flash the `.bin`, not the `.elf` or `.hex`, on the STM32F723E-DISCO: the
linker places its image at the flash's ITCM alias, 0x00200000, so the `.elf`
and the `.hex` carry addresses there, while the `.bin` is the image for the
flash's address 0x08000000, which the `flash` target writes it to and the
USB drive writes at the flash's start.

## Release

The firmware is to be released as `bit-perfect-checker-firmware-<version>.zip`.
The first published binary is to come with 1.1.0. The zip holds, in one
folder of its name, `bpc-<board>-<version>.bin` for every board (the flash
image for 0x08000000), a `README.txt` on flashing and the USB ID, a
`NOTICES.txt` with the licences and copyright notices of everything in the
images, and a `SHA256SUMS` of the `.bin` files.

[`tools/package.py`](tools/package.py) writes it. It clones the repository
at a commit or tag into a scratch folder, with the firmware's submodules at
their pinned commits, builds every board with the build's defaults, and
stops on a build warning, on a `-dirty` or `-unknown` version, on boards
built with different USB IDs or serial number prefixes, on a file of the zip
that holds a path of the machine it ran on, and on a file in a build folder
that has copyright lines. README.txt's paragraph on the USB ID comes from the
builds' `USB_VID`, `USB_PID` and `USB_SERIAL_PREFIX`. NOTICES.txt takes the
copyright lines from the files the builds read (Ninja's dependency log) and
each licence text from its submodule's own licence file. For the routines
the images take from the toolchain's C library, newlib, it names each one
with its source file (from the build's map file and the objects' debug
information), adds the notice each of those source files carries (or the
default notice of newlib's `COPYING.NEWLIB` for a file without one), and
then the licence list that newlib's package ships, by default Debian's
copyright file of `libnewlib-arm-none-eabi` (`--newlib-licence` and
`--newlib-version` name another). The notices come from newlib's own
sources at the release the toolchain was built from: `--newlib-source`
names a folder that holds `COPYING.NEWLIB` and each routine's source file
at its path in the
[newlib repository](https://sourceware.org/git/newlib-cygwin.git), and
`--newlib-tag` names that release as `newlib-X.Y.Z`, where X.Y.Z must begin
the version of the toolchain's newlib package. `--newlib-sha256 <file>`
(optional) names a `sha256sum` list of that folder's files: every file in the
list must match, and every file the script reads must be in it. From the
repository root:

```
python3 -I firmware/tools/package.py --rev firmware-v1.1.0 --work <empty folder> \
    --newlib-source <folder> --newlib-tag newlib-4.6.0
```

The zip and a `SHA256SUMS` of the zips go to `firmware/build/release`
(`--dist` sets another folder); `--ninja` names the `ninja` to build with.
The zip's entries are sorted and carry the commit's date, so a re-run from
the same commit with the same toolchain gives the same bytes.

Firmware releases are tagged `firmware-v<major>.<minor>.<patch>`, the first
`firmware-v1.0.0`. `firmware/VERSION` holds the version being developed (now
`1.1.0`), and the device reports its version as `fw=` in its `boot` and
`version` lines and on a display: `1.1.0` at the tag `firmware-v1.1.0`,
`1.1.0-abc1234` at any other commit (a pre-release of 1.1.0, with the commit
hash), each with `-dirty` when the tree has uncommitted changes, and
`1.1.0-unknown` for source outside git. At a firmware tag whose number is not
the one in `firmware/VERSION`, the configure stops. Other tags, such as the
test files', and pre-release tags `firmware-v<version>-<suffix>` do not
change the version: a build at a pre-release tag reports `<version>-<hash>`. Right after tagging a release, raise
`firmware/VERSION` to the next version; until then every later commit
reports itself as a pre-release of the version just released.

## Serial log and commands

The log is on the board's debug UART, the ST-LINK's virtual COM port,
115200 8N1. It is meant to be read by scripts as well as people.

### Lines from the device

One line per event, `<seconds>.<milliseconds> <event> [word ...]`; a word is a
`key=value` field or a plain word. New fields may be added; existing ones keep
their meaning.

| Event | Meaning |
|---|---|
| `boot fw=<version> board=<board> role=bit-perfect-checker usb=<vid>:<pid> allow_silence=<true/false>` | start-up, with the firmware version ([Release](#release)), which firmware this is (the [BPC1 generator](bpc1-generator/README.md) says `role=bpc1-generator`) and the [allowed silence](#allowed-silence) |
| `led <colour> <pin>` | the LED test at start-up: red, green, blue, each for 0.3 s; no line for a colour the board lacks |
| `display st7789h2 id=0x<hex> fill_us=<n>` | STM32F723E-DISCO, at start-up: the panel answered and the screen was cleared in `fill_us` microseconds; `display st7789h2 id=0x<hex> missing` if the panel did not answer (no screen is drawn) |
| `display ltdc <w>x<h> l8 fb=0x<hex> fill_us=<n>` | STM32U5G9J-DK2, at start-up: the panel runs at that size from an 8-bit frame buffer at that address, and the screen was cleared in `fill_us` microseconds; `display ltdc missing` if the panel did not start (no screen is drawn); `display sram6 clock was off` if the frame buffer's RAM bank had no clock and the firmware switched it on |
| `display green=<n>` | STM32U5G9J-DK2, after its `display ltdc` line: the panel's full green is (0, `<n>`, 0) (`PANEL_GREEN_MAX`, [Boards](#stm32u5g9j-dk2)); not logged when it is 255 |
| `touch <controller> id=0x<hex> g_mode=0x<hex>` | STM32F723E-DISCO, at start-up: the touch controller found, `ft6x36` or `ft3x67`; `touch none id=0x<hex> g_mode=0x<hex>` if no controller was found. STM32U5G9J-DK2: `touch gt911 id=<text> cfg=0x<hex> sw1=0x<hex> rate=<n> int=<0/1>`, the registers read from the controller, or `touch none` |
| `display drawn ...`, `display units strip_us=<n> band_glyph_us=<n> text_glyph_us=<n> clear_us=<n>`, `display hourglass ...`, `display ltdc fuif=<n> terrif=<n> touch_reads=<n>` | only in a build with `-DDISPLAY_LOG=ON` ([Screen](#screen)): what drawing cost, and on the DK2 the LTDC's FIFO underruns and transfer errors |
| `touch off after errors` | STM32U5G9J-DK2: three failed transfers to the touch controller in a row; touch stays off until the next reset. After a failed transfer the firmware waits 1 s before the next, and it reads the controller only while the receive FIFO holds less than 0.5 ms of audio |
| `formats <list>` | the formats offered, as alt settings 1 to n; at boot, and after a `formats` command, with `changed=0` or `changed=1` |
| `usb mounted speed=high` | a host has set the device up; `speed=full` means the High-Speed link failed |
| `usb disconnected` | the cable was pulled (VBUS gone for 50 ms): once per pull, after the lines of a stream it cut and before `run start`, also when a host reset the bus first (`usb reset`) or never set the device up; or the host deconfigured the device while it was set up |
| `usb suspended`, `usb resumed` | the host suspended or resumed the bus; with VBUS still present the device stays connected. A pulled cable logs `usb suspended` first, since the USB controller sees only the bus going idle, and `usb disconnected` once VBUS has been gone for 50 ms: measured 45 to 49 ms later on the NUCLEO-U5A5ZJ-Q and the STM32U5G9J-DK2, about 0.3 s later on the STM32F723E-DISCO. The stream the pull cut stops with `reason=idle` before `usb disconnected` |
| `usb reset` | the host reset the bus while the device was set up: an open stream stops (`reason=reset`), and the host enumerates the device again |
| `usb disconnected`, `usb connecting` | the device leaves the bus to change its formats (the D+ pull-up goes off for 0.3 s), and comes back when that time has passed and VBUS is present |
| `usb connecting` | VBUS is back for 50 ms after a pull, or after a boot without VBUS: the device turns its D+ pull-up on, and the host enumerates it (`usb mounted`) |
| `rate <Hz>` | the host set the sample rate; at boot, the rate the device reports before a host sets one (48000) |
| `rate unsupported=<Hz>` | the host asked for a rate that is not offered (stalled) |
| `alt <n> slot=<bytes> res=<bits>` | the host selected a streaming alt setting; `alt 0` closes it |
| `stream start id=<n> rate=<Hz> alt=<n> slot=<bytes> res=<bits> allow_silence=<true/false>` | the first packet of stream `<n>`, counted from 1 since boot, with the allowed silence in effect |
| `stat id=<n> rate=<Hz> frames=<n> pkts=<n> bits=<n> dop=<0/1> lost=<n> ovf=<n> <load>` | every second while streaming: average rate so far, frames this second, packets so far, bits in use this second, DoP, losses so far, and the [load figures](#load-figures) of this second |
| `lock kind=<PCM/SWEEP/DSD> dop=<0/1> rate=<Hz> depth=<bits> seed=0x<hex> block=<n> frame=<n>` | the checker found a file: its kind and identity from the first X-sync it read, whether it came as DoP, (`rate` is the file's, the DSD bit rate for DSD; `depth` is 1 for DSD), that X-sync's block, and the received frame it starts at |
| `depth bits=<n>` | the bits in use changed |
| `dop on`, `dop off` | more or fewer than half of the frames carry DoP markers |
| `lost packets=<n> total=<n>` | microframes passed without a packet (a zero-length packet counts as none) |
| `overflow packets=<n> total=<n>` | the receive FIFO was full, so data was overwritten |
| `result seq=<n> stream=<n> file=<n> verdict=<verdict> kind=<kind> ...` | **the outcome of one file**: see [Result lines](#result-lines) |
| `stream stop id=<n> reason=<why> dur=<s> frames=<n> pkts=<n> rate=<Hz> fpp=<min>-<max> lost=<n> missed=<n> holes=<n> maxhole=<n> doubled=<n> ovf=<n> outside=<frames> silent=<frames> results=<n> results_lost=<n>` | end of a stream, after its last `result` line: `alt` (alt setting changed), `idle` (no packet for 50 ms, as after a cable pull), `rate` (the host set another rate during the stream), `reset` (the device stopped being set up: a bus reset, the host deconfiguring the device, a cable pull judged before the 50 ms of idle have passed, or a `formats` change) or `pause`; `fpp` is frames per packet; `holes` counts the runs of microframes without a packet and `maxhole` is the longest; `outside` is the number of frames of the stream outside every file's extent ([12.6](../docs/bpc1.md#126-verdicts)), so host padding can be measured; `silent` is the number of those frames that were allowed silence, which gave no result (0 unless `allow_silence` is `true`); `results` is the number of result lines of the stream, and `results_lost` the number of results of the stream the checker dropped because its queue of finished results was full |
| `stall <ms>` | the [`stall` command](#commands-to-the-device) started its busy-wait of `<ms>` milliseconds |
| `run start` | a new run begins: the user button was pressed or the touch screen tapped, the `mark` command arrived, the USB cable was pulled (after `usb disconnected`), or the board reset; green and red go off ([LEDs](#leds)). It follows any `result` line still waiting at that moment, whose result belongs to the run before |
| `usb unsupported get/set entity=<id> ctrl=<n> req=<n>` | a class request the device does not answer |
| `error formats=<text>` | the list given to the `formats` command is not valid; the offered formats stay as they were |
| `error allow_silence=<text>` | the argument of the `allow_silence` command is neither `true` nor `false`; the setting stays |
| `error formats-default=<list>` | at boot: the build's `UAC2_FORMATS` is not valid, so the device offers all four formats |
| `log lost=<n>` | log lines dropped because the queue was full |

`lock` comes from the checker four times a second at most. The log keeps
room in its queue that only `result`, `stream stop` and `run start` lines may
use, so these are never dropped when other lines fill it.

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
| `verdict` | `BIT_PERFECT`, `NOT_BIT_PERFECT`, `UNKNOWN_SIGNAL` or `OVERRUN`, below |
| `kind` | the file's kind from its X-sync: `PCM`, `SWEEP` (the PCM code sweep, [signal format](../docs/bpc1.md#75-expected-word-pseudocode)), `DSD` (from a DSF, DFF or DoP file alike), or `NONE` |
| `dop` | 1 when the file came as DoP: a DSD file, DSF, DFF or DoP, played as DoP gives `kind=DSD dop=1` |
| `seed`, `nch` | the file's seed and channel count, from its X-sync |
| `file_rate`, `depth` | the file's rate (frames per second for PCM, DSD bits per second per channel for DSD) and depth (16, 24, 32; 1 for DSD) |
| `rate`, `alt`, `slot`, `res` | what the host chose: the frame rate, alt setting, slot bytes, resolution |
| `bits` | the bits in use of the received words over the file's body, for PCM and DoP alike (DoP in 3- or 4-byte slots: 24 when the padding below it is zero) |
| `end` | how the file ended: `LAST` (its block flagged last arrived), `NEXT` (another file followed), `BACK` (the block index went back), `STOP` (the stream stopped first) |
| `diff` | the position in the file of the first body unit that did not arrive as expected, in seconds from the file's start as generated, 3 decimals, rounded half up (PCM: frame index / rate; DSD: byte index x 8 / DSD rate): the earliest of the body's first unit, when the rate or channel count is wrong or the first body block's X-sync or the lead-in X-sync before it is missing; the first body unit that differs or did not arrive (in the R-header chips of r5 and r6, as the R-header rule of [12.6](../docs/bpc1.md#126-verdicts) places it); and the first unit after the body, when the X-sync after the body is missing. Where a limit X-sync is broken or cut off, the body runs from after the last lead-in X-sync received to the first tail X-sync received. `-` for `BIT_PERFECT`, `UNKNOWN_SIGNAL` and `OVERRUN` (12.6) |
| `lost`, `ovf` | packets lost and receive FIFO overflows that fall in the result's frames, by their place in the stream: a lost packet counts in the result of the first frame after it (a doubled microframe takes back the hole before it), an overflow in the result of the frame where the checker learns of it, normally the one it marks `OVERRUN`. Those between two results count in the later one; the stream's last result line also takes those after every result and those in frames not read before the stream stopped. A stream's result lines add up to its `stream stop` line's `lost` and `ovf`, except with `allow_silence` `true` when the stream ends in allowed silence after its last result: the losses there are on no result line, only on the `stream stop` line. The verdict follows the frames, not these counts |
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
result seq=30 stream=30 file=1 verdict=OVERRUN kind=PCM dop=0 seed=0x42504331 nch=2 file_rate=48000 depth=24 rate=48000 alt=4 slot=3 res=24 bits=24 end=LAST diff=- lost=0 ovf=684 dur=12.458
stream stop id=30 reason=alt dur=12.649 frames=607164 pkts=101194 rate=48000.00 fpp=6-6 lost=0 missed=0 holes=0 maxhole=0 doubled=0 ovf=684 outside=0 silent=0 results=1 results_lost=0
```

The verdicts, from the BPC1 specification
([section 12.6](../docs/bpc1.md#126-verdicts)) and the device, in
their order of precedence; only `BIT_PERFECT` is a pass:

| Verdict | Meaning |
|---|---|
| `UNKNOWN_SIGNAL` | the units belong to no file: outside every file's extent, silence included unless [allowed](#allowed-silence) (a DoP file received without markers has no readable X-sync and gives it) |
| `OVERRUN` | the device's own receive FIFO overflowed between the file's first received unit and its end (or, with no file open, over the stretch), which overwrites frames not read yet, and no difference of the file came before the overflow: the file is not judged; play it again. A file whose first difference came before the overflow is `NOT_BIT_PERFECT`, with that difference's `diff` |
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

The CPU runs at 160 MHz on the NUCLEO-U5A5ZJ-Q and the STM32U5G9J-DK2 and at
216 MHz on the STM32F723E-DISCO; `isr_max` and `ck_cpf` count CPU cycles. The
receive FIFO holds 32 760 bytes, 5.3 ms at 768 kHz in 4-byte slots;
`fifo_max` near that means the main loop is close to falling behind.

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
| `status` | `status usb=<mounted/suspended/not-connected> speed=<high/full> rate=<Hz> alt=<n> slot=<bytes> res=<bits> streaming=<0/1> id=<n> lost=<n> ovf=<n> formats=<list> allow_silence=<true/false>`; `usb=not-connected` while no host has the device set up or the cable is out (no VBUS); `lost` and `ovf` are those of stream `id`: as on its last `stat` line while the stream is open, as on its `stream stop` line once it stopped, with a negative `lost` shown as 0 |
| `result` | the last `result` line again, or `result none` |
| `mark <text>` | `mark <text>`, to tie a script's steps to the log; it also starts a new run (`run start`, [LEDs](#leds)). The echo comes at once, so a `result` line still waiting from the run before can come between `mark` and `run start`; `bpcctl` follows stream ids and is not affected |
| `formats [<list> \| default]` | `formats <list>`; with a list, offer those formats from now on (`changed=1`, then the device reconnects; `changed=0` if they are the formats already offered); a list that is not valid answers `error formats=<text>` |
| `allow_silence [true\|false]` | `allow_silence <value>`; with an argument, sets the [allowed silence](#allowed-silence) for the next stream; without one, only shows the setting; another argument answers `error allow_silence=<text>` |
| `version` | `version fw=<version> board=<board> role=bit-perfect-checker usb=<vid>:<pid>` |
| `stall <ms>` | test command: `stall <ms>` and then the main loop busy-waits for `<ms>` milliseconds (1 to 2000) with interrupts on, so the USB interrupt keeps filling the receive FIFO while nothing drains it; used to force `OVERRUN` on purpose. Other values answer `error stall=<value>` |
| `help` | `help commands=status,result,mark,formats,allow_silence,version,help test=stall` |

Anything else gets `error unknown-command=<word>` or `error line-too-long`.
Send a newline before the first command after opening the port, since the
line may hold noise from a reset.

### From a script

[`bpcctl`](../bpcctl) does this with the Python standard library on Linux
and macOS, and with pyserial on Windows:

```
python3 -m bpcctl run -- aplay -D hw:CARD=Checker out/00033-bpc1_pcm_096k_24bit.wav
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
than N results are `BIT_PERFECT` or `OVERRUN` with `--expect N`),
else 4 when any is `OVERRUN` or the reporting is incomplete (a
`stream stop` line with `results_lost` above 0, fewer result lines than its
`results=`, or a stream unfinished at the timeout), else 0: at least one
result arrived and all are `BIT_PERFECT`. `--json` prints each line as one JSON object. The port
is `$BPC_CHECKER_PORT`, else `/dev/ttyACM0`; `--port` overrides it. Without
`bpcctl`, the same steps by hand: open the port, send `mark <id>`, wait
for the echo, play, then wait for the `stream stop` line after the
`result` lines.

On Linux, `hw:CARD=Checker` (case-sensitive) reaches the device without PipeWire or any
format conversion.

## LEDs

The DK2 has no blue LED, so its screen shows what blue shows
([Boards](#boards)). A fault lights every LED a board has.

| LED | Meaning |
|---|---|
| blue, fast blink | not connected: the cable is out, or no host has set the device up |
| blue, slow blink | set up by a host (mounted or suspended), waiting for a stream |
| blue, steady | a stream is open |
| red, steady | a file of the run is `NOT_BIT_PERFECT`, from its first difference on |
| red, slow blink | the run has an `UNKNOWN_SIGNAL` result |
| red, fast blink | the run has an `OVERRUN` result |
| green, steady | the run's results are all `BIT_PERFECT` |
| green and red off | nothing judged yet |
| all on | fault, stopped |

Green and red show the current **run**: every result since the last press of
the user button (B1 on the NUCLEO-U5A5ZJ-Q and the F723, B2 on the DK2, or
a tap on a touch screen), the console command `mark`, a pulled USB cable (VBUS gone for 50 ms), or a reset. A pull
during playback cuts a file: its result stays in the run before, and while
the cable is out green and red are off.
They do not depend on how the host splits files into streams, and they keep
the result after a stream stops, until the next run starts. They show the
first of these that holds, from red steady down: green only when every result
is `BIT_PERFECT`, red for every other verdict. A result never changes what a
result higher in the table set: once red is on, it stays red until the next
run, and it can only change from a fast blink to a slow blink to steady. With
`allow_silence` `true` a file muted to zeros gives no result, so green can
stay on; only `bpcctl run --expect` catches it. A new run is
logged as `run start` and turns green and red off. A `result` line that is
still waiting when the run starts comes before `run start`, since its result
belongs to the run before. A stack that grows past
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
   `<UART>_IRQHandler`, the USB PHY's clock, and `board_vbus()`, which reads
   the user USB port's VBUS.
4. Build with `-DBOARD=<board>`.

**A board of another family** (the STM32F723E-DISCO's `families/stm32f7/` was
added this way): add `families/<family>/` like `families/stm32u5/` or
`families/stm32f7/`, with
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

`display_update()` gets the [status](app/status.h) after every change and
does not draw. `display_task()` is called on every pass of the main loop and
draws at most about 0.2 ms at a time, because the receive FIFO holds about
5 ms of audio at 768 kHz. `display_led_test()` (optional) is called as the
start-up LED test lights each LED, for a splash that shows the same colours.
The fault handler calls `board_fault()` before it stops; a board with a
display turns its backlight off there. A board without a display builds
`app/display_none.c` instead.

To use the [status screen](#screen) of the F723 and the DK2, the board's
folder holds:

1. A panel driver that implements [`panel.h`](include/panel.h):
   `panel_init()` (bus, reset, controller set-up, screen cleared to black,
   backlight on; false if no panel answers), `panel_width()` and
   `panel_height()`, `panel_window()` for the rectangle the next writes fill,
   `panel_fill()` for pixels of one colour and `panel_bits()` for a bitmap.
   Colours are `0xRRGGBB`; the driver turns them into the panel's format.
2. A `screen_layout_t` and a table of `screen_place_t` ([`app/screen.h`](app/screen.h)):
   where the band, the labels, the file lines and the version go, in which
   font, and the splash. The DK2's is in `screen_layout.h`, doubled from the
   F723's.
3. A `display.c` whose `display_init()` calls `panel_init()` and
   `screen_init(&layout)`, and whose `display_update()`, `display_task()` and
   `display_led_test()` call `screen_update()`, `screen_task()` and
   `screen_splash_led()`.
4. In `board.cmake`, the sources (`display.c`, the panel driver,
   `app/screen.c` and `app/screen_model.c`) in `DISPLAY_SOURCES`, and the
   fonts in `DISPLAY_FONTS`, as `<name>=<file>` pairs naming BDF files in
   `lib/spleen`:
   ```
   set(DISPLAY_FONTS 8x16=spleen-8x16.bdf 12x24=spleen-12x24.bdf 16x32=spleen-16x32.bdf)
   ```
   The build converts them and gives the layout `font_<name>` (`font_8x16`)
   from `fonts.h`. A size Spleen lacks can be made from a smaller one at
   start-up with `screen_font_double()`, as the DK2 does.
5. For touch, `board_button()` returns true while a finger is down, besides
   the button, as the F723's and the DK2's do.

**A board with a display shield** (the NUCLEO-U5A5ZJ-Q with an Arduino-header TFT):
add `shields/<shield>/shield.cmake`, which appends the display driver to
`DISPLAY_SOURCES` the same way, and build with
`-DBOARD=<board> -DSHIELD=<shield>`. The shield uses the bus and pins the
board's Arduino header provides; the board names them in its `board.c` or a
header next to it.

## Licences

The firmware is MIT. Third-party code comes through the pinned submodules,
under their own licences. The two panel drivers,
`boards/stm32f723e_disco/panel_st7789h2.c` and
`boards/stm32u5g9j_dk2/panel_rk050hr18.c`, each hold a table taken from
ST's code (the ST7789H2 set-up, the RK050HR18 timing and LTDC settings)
under ST's BSD-3-Clause notice in the file; their SPDX identifier is
`MIT AND BSD-3-Clause`. The screens' fonts are
[Spleen](https://github.com/fcambus/spleen) (BSD-2-Clause), converted at
build time.

## How it was written

See [How it was written](../README.md#how-it-was-written) in the main README.

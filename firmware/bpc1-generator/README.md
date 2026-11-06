# BPC1 generator

Firmware for a USB host that is to play the BPC1 test signal
([docs/bpc1.md](../../docs/bpc1.md)) to a USB Audio Class 2 device, such as
the checker. It is work in progress. So far it sets up the High-Speed USB
host on the board's user USB port and the port's VBUS switch, reads a
device's formats and rates when it is mounted, and has the serial log with
its commands; it plays no audio yet.

The board is the STM32F723E-DISCO, with CN19 as the host port. The BPC1
generator shares the boards, families, `app/` modules and the version
(`firmware/VERSION`, [Release](../README.md#release)) with the checker, and
is released with it.

## Build and flash

From the repository root, as for the checker ([Build](../README.md#build),
[Flash](../README.md#flash)):

```
cmake -S firmware/bpc1-generator -B firmware/build/bpc1-generator-stm32f723e_disco -G Ninja -DBOARD=stm32f723e_disco
cmake --build firmware/build/bpc1-generator-stm32f723e_disco
cmake -DSTLINK_SN=<serial number> firmware/build/bpc1-generator-stm32f723e_disco
cmake --build firmware/build/bpc1-generator-stm32f723e_disco --target flash
```

The build writes `bpc1-generator-<board>.elf`, `.bin` and `.hex`, and must
finish without compiler or linker warnings. A board can be used when its
`board.cmake` sets `HOST_BOARD_SOURCES` and `HOST_LINKER_SCRIPT`, and its
family's `family.cmake` sets `FAMILY_HOST_SOURCES`.

TinyUSB's DWC2 host driver gets
`lib/patches/tinyusb-host-reserved-channels.patch`, applied to a copy in the
build folder, so the submodule stays at the release: host channels 14 and
15 are kept for the BPC1 generator's isochronous endpoints, and TinyUSB
neither allocates them nor handles their interrupts.

`-DENUM_CHECK=ON` builds a test image: after each mount it sets every rate
the device offers at 24 bits, then 16 and 32 bits at 48 kHz, DoP at
176.4 kHz and 32 kHz (which no BPC1 file has, so the device should refuse
it), each followed by alt 0.

## VBUS

The BPC1 generator powers the port: it switches VBUS on through the board's
VBUS switch (U13 on the STM32F723E-DISCO, enabled by PH12). On ST-LINK power
the port gives at most 100 mA (UM2140 7.9).

- The switch goes on after the USB host is set up, and only while the port
  sees no VBUS (PB13). LD8 shows VBUS on CN19, whichever side supplies it.
- `vbus fault` means that the switch is off because of a fault, of one of
  two kinds:
  - VBUS was already present: another host drives the cable (CN19 is
    plugged into a computer or a hub, say). The fault is logged once. The
    switch goes on once the port has seen no VBUS for 1 s without a break,
    however long that takes, so moving the cable from a computer to a
    device needs no reset.
  - The switch reported overcurrent (PH10, LD7 lit). Overcurrent turns the
    switch off at once, in its interrupt. The switch is tried again 1 s
    later, at most three times since start. After the last overcurrent the
    log shows `vbus off`, and the switch stays off until the next reset.

## Serial log and commands

The log is on the same UART as the checker's (USART6 behind the ST-LINK's
virtual COM port, 115200 8N1), with the same line format and command rules
([Serial log and commands](../README.md#serial-log-and-commands)).

| Event | Meaning |
|---|---|
| `boot fw=<version> board=<board> role=bpc1-generator` | start-up, with the firmware version |
| `vbus on` | the VBUS switch is on |
| `vbus fault` | the VBUS switch is off: VBUS was already present, or overcurrent ([VBUS](#vbus)) |
| `vbus off` | after the last overcurrent: the VBUS switch stays off until reset |
| `usb mounted speed=<high/full/low> usb=<vid>:<pid> serial=<text>` | a device is set up; `serial=` is its serial number string (empty without one), with characters other than printable ASCII shown as `?` |
| `formats <list>` | the device's formats, `<bits>/<bytes>` as on the checker, in the order of its alt settings |
| `rates <list>` | the BPC1 rates that the device's clock offers (its RANGE of the sampling frequency) |
| `error device=<reason>` | the device cannot play, or a request failed: see [Devices](#devices) |
| `rate <Hz>` | the rate is set on the device's clock and read back |
| `rate unsupported=<Hz>` | the device does not offer the rate, refused it (stalled), or read back another one |
| `alt <n> slot=<bytes> res=<bits>`, `alt 0` | the alt setting selected for a file; `alt 0` after it |
| `usb disconnected` | the device has gone |

| Command | Answer |
|---|---|
| `status` | `status usb=<mounted/not-connected> vbus=<on/fault/off>`; `usb=mounted` while the host has a device set up; `vbus` is the switch: `on`, `fault` (off after a fault, waiting until VBUS has gone or for the next try) or `off` (off until reset) |
| `device` | the device's lines again: `usb mounted ...`, then `formats ...` and `rates ...`, or its `error device=...`; `error device=not-connected` without a device, `error device=not-ready` while it is still being read |
| `version` | `version fw=<version> board=<board> role=bpc1-generator` |
| `help` | `help commands=status,device,version,help` |

Anything else gets `error unknown-command=<word>` or `error line-too-long`.

## Devices

When a device is mounted, the BPC1 generator reads its serial number
string, its configuration descriptor and the RANGE of its clock's sampling
frequency. It plays to a USB Audio Class 2 device with a USB streaming input
terminal, whose clock is a clock source or a clock selector with one input,
and whose streaming interface has only stereo Type I PCM formats in slots
of 2, 3 or 4 bytes, each on an isochronous OUT endpoint served every
microframe. An asynchronous endpoint needs an explicit feedback endpoint.
Otherwise the log shows `error device=<reason>`:

| Reason | Meaning |
|---|---|
| `full-speed`, `low-speed` | the device runs below High-Speed |
| `config` | the configuration descriptor could not be read |
| `cut`, `length` | the configuration descriptor ends early, or holds a descriptor that is too short |
| `not-config` | the configuration descriptor is not one |
| `not-audio` | no audio interface |
| `uac1`, `version` | USB Audio Class 1.0, or another version than 2.0 |
| `no-terminal` | no USB streaming input terminal |
| `no-streaming` | no streaming interface for that terminal, or one without a zero-bandwidth alt setting 0 |
| `clock` | the terminal's clock is not a clock source, nor a clock selector with one input |
| `no-alt`, `too-many-alts`, `alt-repeated` | no alt setting with a format, more than 8, or one alt setting twice |
| `format` | a format other than Type I PCM, or a slot or resolution it cannot use |
| `channels` | a format with other than two channels |
| `endpoint` | no isochronous OUT data endpoint, or a feedback endpoint that is not isochronous IN of at least 4 bytes |
| `high-bandwidth` | an endpoint with more than one transaction per microframe |
| `interval` | a data endpoint not served every microframe |
| `implicit-feedback` | an asynchronous endpoint without an explicit feedback endpoint |
| `range` | the RANGE request failed, or its reply holds no subrange |
| `alt` | the device refused an alt setting |
| `no-fit` | no alt setting fits the file |

For each file the BPC1 generator sets the rate (SET_CUR, then GET_CUR to
read it back), then selects the alt setting with the smallest slot that
holds the file's depth, with a resolution at least the depth (DoP: a slot
of 3 or 4 bytes) and a `wMaxPacketSize` that holds the largest packet, and
selects alt 0 after the file.

## How it was written

See [How it was written](../../README.md#how-it-was-written) in the main
README.

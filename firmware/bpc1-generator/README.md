# BPC1 generator

Firmware for a USB host that is to play the BPC1 test signal
([docs/bpc1.md](../../docs/bpc1.md)) to a USB Audio Class 2 device, such as
the checker. It is work in progress. So far it sets up the High-Speed USB
host on the board's user USB port, the port's VBUS switch, and the serial
log with its commands; it plays no audio yet.

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

| Command | Answer |
|---|---|
| `status` | `status usb=<mounted/not-connected> vbus=<on/fault/off>`; `usb=mounted` while the host has a device set up; `vbus` is the switch: `on`, `fault` (off after a fault, waiting until VBUS has gone or for the next try) or `off` (off until reset) |
| `version` | `version fw=<version> board=<board> role=bpc1-generator` |
| `help` | `help commands=status,version,help` |

Anything else gets `error unknown-command=<word>` or `error line-too-long`.

## How it was written

See [How it was written](../../README.md#how-it-was-written) in the main
README.

# BPC1 test-signal format, version 1

Status: **normative**, version 1 (the design choices around it are in
[design](design.md)). The reference implementation is the generator in
[`generator/`](../generator) and the checker in [`checker/`](../checker).
Golden test vectors are in
[`vectors/bpc1-v1-vectors.json`](../vectors/bpc1-v1-vectors.json).

BPC1 ("bit-perfect check, format 1") defines test signals that let a receiver
tell whether audio reached it unchanged. A receiver that knows this document
can regenerate every expected sample of a BPC1 file from a few header fields
carried in the signal itself. The signal also lets a diagnosis tool say what
changed (the gap search and inverted search of 12.2, and 12.3 to 12.5); that is
not needed for the verdict.

Contents:

1. [Conventions](#1-conventions)
2. [Overview](#2-overview)
3. [Codes and parameters](#3-codes-and-parameters)
4. [Primitives](#4-primitives)
5. [File structure](#5-file-structure)
6. [Header words](#6-header-words)
7. [PCM signal](#7-pcm-signal)
8. [DSD signal](#8-dsd-signal)
9. [Containers](#9-containers)
10. [Levels and safety](#10-levels-and-safety)
11. [The published test set](#11-the-published-test-set)
12. [Checking](#12-checking)
13. [Test vectors](#13-test-vectors)
14. [Design notes](#14-design-notes)

## 1. Conventions

- **MUST**, **MUST NOT**, **SHOULD** and **MAY** are used as in RFC 2119.
  Sections 3 to 9 and 11 are normative for files. Section 12.1, the Lock and
  Comparison paragraphs of 12.2, and 12.6 (but for its paragraphs marked
  informative) are normative for the verdict of a conforming checker. The
  rest of section 12 is informative: how the reference checker finds the
  verdict, and its diagnosis of what changed.
- All arithmetic is on unsigned integers modulo 2^32 or 2^64 unless stated
  otherwise. `^` is XOR, `<<` and `>>` are shifts (`>>` is logical on
  unsigned values), `&` and `|` are bitwise AND and OR. `asr(x, s)` is the
  arithmetic right shift of the two's-complement 32-bit value x.
- `a / b` on integers truncates. `ceil(x)` rounds up.
- Bit 0 is the least significant bit. A 16-bit word is serialised most
  significant byte first ("big-endian") wherever this document turns words
  into bytes or bits.
- Hexadecimal numbers are written `0x...`.
- A **frame** is one sample per channel at one instant. A PCM sample of
  depth D bits is a two's-complement integer s with
  -2^(D-1) <= s < 2^(D-1). Its **word** W is the 32-bit value s << (32 - D):
  the sample left-justified, bits below the depth zero. Full scale is 2^31
  in W.

## 2. Overview

Every BPC1 stream is cut into **blocks** of about 90 ms. Each block starts
with an **exact sync** (X-sync) that names the file and the block, so a
receiver can lock anywhere in the stream within one block. After the X-sync
comes a pseudo-random **payload** that the receiver regenerates from the
frame index: any difference shows up at the frame where it happens.

The signal carries two layers from the start:

- **Exact layer.** X-sync, walking-bit burst (PCM) and payload. It is
  compared frame by frame and bit by bit. It detects every change. A
  diagnosis tool can also name exact changes with it (truncation, a float
  path, channel swap, polarity, dropped, inserted, repeated and zero-filled
  frames) and locate gaps.
- **Robust layer.** Every 16th block (about 1.4 s) the payload is replaced by
  a **robust header** (R-header): a Barker-13 sync and the header fields,
  Manchester-coded as large, slow square-wave chips of about 0.25 ms. It
  survives volume change, dither, truncation, resampling and DSD-to-PCM
  conversion. A diagnosis tool reads the rate ratio from its chip timing, the
  gain from its level, the polarity from its sign and the channel order from
  its channel field.

The verdict (section 12.6) uses the exact layer only: it compares every unit
of the body, R-header blocks included, needs no robust decoding, and reads the
block count only as the R-header rule of 12.6 says. The robust layer serves diagnosis. Every file carries both layers.

## 3. Codes and parameters

### 3.1 Kind

| Code | Kind | Payload | Carried in |
|---|---|---|---|
| 1 | PCM | low-level noise (7.4) | WAV, FLAC, AIFF, ALAC |
| 2 | DSD | paired bits (8.4) | DSF, DFF, and DoP in a 24-bit WAV, FLAC, AIFF or ALAC |
| 5 | PCM code sweep | every code of the depth (7.4) | WAV, FLAC, AIFF, ALAC |
| 0, 3, 4, 6-15 | reserved | | |

The header words (section 6) carry what a checker needs to regenerate the
expected units: the kind, which selects the payload, the depth, rate,
channels and seed, and each block's index and flags. They do not name the
container. A signal is the same in every file that carries it: the WAV,
FLAC, AIFF and ALAC files of a PCM signal hold the same samples (FLAC and
ALAC are lossless encodings of the WAV's samples, AIFF holds them in another
byte order), and the DSF, DFF and DoP files of a DSD signal hold the same
DSD bits (section 9). A receiver tells the transport (PCM, DoP or native
DSD, 12.1), not the file. A code sweep (kind 5) is a PCM signal in every
respect but its payload (7.4): it needs depth 16 or 24 and a PCM rate.

### 3.2 Depth

| Code | Depth D | Used by |
|---|---|---|
| 1 | 1 bit | DSD |
| 2 | 16 bits | PCM |
| 3 | 24 bits | PCM |
| 4 | 32 bits | PCM |
| 0, 5-15 | reserved | |

### 3.3 Rate

| Code | Rate (Hz) | Family base | M |
|---|---|---|---|
| 0 | 44 100 | 44 100 | 1 |
| 1 | 48 000 | 48 000 | 1 |
| 2 | 88 200 | 44 100 | 2 |
| 3 | 96 000 | 48 000 | 2 |
| 4 | 176 400 | 44 100 | 4 |
| 5 | 192 000 | 48 000 | 4 |
| 6 | 352 800 | 44 100 | 8 |
| 7 | 384 000 | 48 000 | 8 |
| 8 | 705 600 | 44 100 | 16 |
| 9 | 768 000 | 48 000 | 16 |
| 10 | 2 822 400 (DSD64) | 44 100 | 64 |
| 11 | 5 644 800 (DSD128) | 44 100 | 128 |
| 12 | 11 289 600 (DSD256) | 44 100 | 256 |
| 13-15 | reserved | | |

For PCM the rate is the frame rate; for DSD it is the bit rate per channel.
The **multiplier** M is rate / base. Codes 0 to 9 are PCM only, 10 to 12
DSD only.

### 3.4 Flags

| Bit | Mask | Name | Set on |
|---|---|---|---|
| 0 | 0x01 | LEAD_IN | every lead-in block |
| 1 | 0x02 | TAIL | every tail block |
| 2 | 0x04 | LAST | the last block of the file |
| 3-7 | | reserved | MUST be 0; a checker does not use an X-sync with one set (a v1 checker does not judge a file from a format version it does not know) |

### 3.5 Seed and channels

- The **seed** is a 32-bit file parameter that keys the payload. The
  published set (section 11) uses seed 0x42504331 (ASCII "BPC1") for every
  file; files of different kind, rate or depth still differ, because the
  channel keys depend on them too, while the files of one signal in
  different containers hold the same units (3.1). Other seeds are allowed and make a different,
  equally valid file.
- A file has `nch` channels, 1 <= nch <= 15, numbered 0 to nch - 1 in
  container order (channel 0 is left in a stereo file). Every channel carries
  its own complete signal, with its own channel index. The published set is
  stereo.

## 4. Primitives

### 4.1 lowbias32

The payload generator is the 32-bit integer hash "lowbias32" (constants
published by Chris Wellons), applied to the frame or word index XOR a key:

```
lb32(key, n):
    x = n ^ key
    x = x ^ (x >> 16)
    x = (x * 0x7FEB352D) mod 2^32
    x = x ^ (x >> 15)
    x = (x * 0x846CA68B) mod 2^32
    x = x ^ (x >> 16)
    return x
```

It is a bijection on 32 bits and needs no state, so a checker can compute the
expected value at any position directly (two multiplications per sample).

### 4.2 Channel keys

Each channel c has a 32-bit key derived once per file with the first output
of SplitMix64:

```
splitmix64_first(v):                     (all mod 2^64)
    z = v + 0x9E3779B97F4A7C15
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9
    z = (z ^ (z >> 27)) * 0x94D049BB133111EB
    return z ^ (z >> 31)

key[c] = splitmix64_first((seed << 32) | (kind << 24) | (rate_code << 16)
                          | (depth_code << 8) | c)  mod 2^32
```

### 4.3 CRC-16

All CRCs in BPC1 are CRC-16/IBM-3740 (also known as CRC-16/CCITT-FALSE):
polynomial 0x1021, initial value 0xFFFF, input and output not reflected, no
final XOR. Check value: the ASCII bytes `123456789` give 0x29B1.

```
crc16(bytes):
    crc = 0xFFFF
    for each byte b:
        crc = crc ^ (b << 8)
        repeat 8 times:
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc = crc & 0xFFFF
    return crc
```

A CRC over words is the CRC over their big-endian bytes.

### 4.4 lbD and the sweep key

The payload of a code sweep (kind 5, section 7.4) uses a mix that is a
bijection on D bits (D = 16 or 24), the lowbias32 steps scaled to D bits:

```
lbD(D, key, s):                          (key, s and the result are D-bit values)
    m = 2^D - 1 ; h = D / 2
    x = (s ^ key) & m
    x = x ^ (x >> h)
    x = (x * (0x7FEB352D & m)) & m
    x = x ^ (x >> (h - 1))
    x = (x * (0x846CA68B & m)) & m
    x = x ^ (x >> h)
    return x
```

Every step is invertible on D bits, so the code for s = 0 to 2^D - 1 takes
each D-bit value exactly once. The **sweep key** of channel c is the low D
bits of its channel key (section 4.2, with kind 5 in the key):

```
sweep_key[c] = key[c] & (2^D - 1)
```

## 5. File structure

### 5.1 Units and blocks

The signal of each channel is a sequence of **units**: frames for PCM, DSD
bits for DSD. The unit index from the start of the file is n (PCM) or m
(DSD), starting at 0. A file MUST have fewer than 2^32 units per channel.

A **block** is B = 4096 x M units:

| Rate | B (units) | Block duration |
|---|---|---|
| 44.1, 88.2, 176.4, 352.8, 705.6 kHz | 4096, 8192, 16384, 32768, 65536 frames | 92.88 ms |
| 48, 96, 192, 384, 768 kHz | 4096, 8192, 16384, 32768, 65536 frames | 85.33 ms |
| DSD64, DSD128, DSD256 | 262144, 524288, 1048576 bits | 92.88 ms |

Block k covers units k x B to (k + 1) x B - 1. A file consists of whole
blocks only; the block index k is carried modulo 2^16, so a file MUST have
fewer than 65 536 blocks.

### 5.2 Lead-in, body and tail

A file is made of three sections, each a whole number of blocks:

1. **Lead-in**, flagged LEAD_IN. It absorbs what players and receivers do at
   the start: rate switching, muting, fades, DoP detection.
2. **Body.** Only the body counts for a verdict.
3. **Tail**, flagged TAIL. It absorbs fades and cuts at the end.

All sections carry the same block structure; only the flags differ. The
number of blocks for a duration of t seconds is `ceil(t x base / 4096)`. A
standard file has a 2 s lead-in, a 10 s body and a 0.5 s tail at every rate:

| Section | 44.1 kHz family and DSD | 48 kHz family |
|---|---|---|
| Lead-in, 2 s | 22 blocks (2.04 s) | 24 blocks (2.05 s) |
| Body, 10 s | 108 blocks (10.03 s) | 118 blocks (10.07 s) |
| Tail, 0.5 s | 6 blocks (0.56 s) | 6 blocks (0.51 s) |

A code-sweep file (kind 5) has the same lead-in and tail. Its body is 10 s
(108 or 118 blocks) at 16 bits. At 24 bits it is the fewest blocks that hold
at least 2^24 sweep units (section 7.4) per channel, counting the blocks that
are not R-header blocks:

| Rate (kHz) | 44.1 | 48 | 88.2 | 96 | 176.4 | 192 | 352.8 | 384 | 705.6 | 768 |
|---|---|---|---|---|---|---|---|---|---|---|
| Body blocks | 4448 | 4448 | 2205 | 2205 | 1098 | 1098 | 549 | 549 | 274 | 275 |
| Body (s) | 413.1 | 379.6 | 204.8 | 188.2 | 102.0 | 93.7 | 51.0 | 46.9 | 25.5 | 23.5 |

(with the standard lead-in, which fixes which blocks of the body are
R-header blocks)

Other lengths are valid BPC1 files: a checker MUST take the sections from
the flags and the block count (`total` in the R-header), not from this table.

### 5.3 R-header blocks

Block k is an **R-header block** if `k mod 16 = 8`. In it the payload is
replaced as described in sections 7.6 and 8.5. The first R-header block is
block 8, inside the lead-in, so that a checker sees one early in playback.

## 6. Header words

Five 16-bit words describe the file and the block. They are the same in the
X-sync and the R-header; only the channel index differs between channels.

| Word | Bits 15-12 | Bits 11-8 | Bits 7-4 | Bits 3-0 |
|---|---|---|---|---|
| h0 | version = 1 | kind | depth code | rate code |
| h1 | channel index | nch | flags (bits 7-4) | flags (bits 3-0) |
| h2 | seed bits 31-16 | | | |
| h3 | seed bits 15-0 | | | |
| h4 | block index k mod 2^16 | | | |

The **X-sync** of channel c in block k is eight words:

```
w0 = 0xB1C0
w1 = 0x5EED
w2..w6 = h0..h4
w7 = crc16(w0..w6)
```

The **R-header** of channel c in block k is seven words:

```
r0..r4 = h0..h4
r5 = total number of blocks in the file, mod 2^16
r6 = crc16(r0..r5)
```

A checker MUST reject an X-sync or R-header whose CRC fails, whose version is
not 1, or whose kind, depth or rate code is reserved, whose kind and depth
disagree (depth 1 if and only if kind 2), whose kind is 5 and whose
depth is not 16 or 24, whose rate code does not fit the kind (kinds 1 and
5: codes 0 to 9; kind 2: codes 10 to 12), whose nch is 0, or whose
channel index is nch or more.

## 7. PCM signal

### 7.1 Block layout

For channel c, frame n, block k = n / B, offset i = n mod B, and
h = lb32(key[c], n):

| Frames i | Normal block | R-header block (k mod 16 = 8) |
|---|---|---|
| 0 to 7 | X-sync | X-sync |
| 8 to 71 | walking-bit burst | walking-bit burst |
| 72 to B - 1 | payload | zero, except the chip span |
| 512M to 512M + 237L - 1 | (payload) | R-header chips |

L is the chip length (section 7.6).

### 7.2 X-sync frames

Frame i (0 to 7) carries X-sync word w_i in the top 16 bits of its word W.
Below them the payload hash continues:

```
W = ((w_i << 16) | (h >> 16))  with the bits below the depth cleared
s = asr(W, 32 - D)
```

So in a 16-bit file the sample is exactly w_i; in a 24-bit file it is
`(w_i << 8) | (h >> 24)`; in a 32-bit file `(w_i << 16) | (h >> 16)`.

### 7.3 Walking-bit burst

Frames 8 to 71 carry 64 full-scale bit patterns. Step j = i - 8 (0 to 63) of
channel c uses index `p = j` for even c and `p = 63 - j` for odd c. With
`mask = 2^D - 1` and `alt = 0x55555555 & mask`, the unsigned D-bit pattern u
is:

| p | u |
|---|---|
| 0 to D - 1 | `1 << p` (walking one, bit D-1 is the sign bit) |
| D to 2D - 1 | `mask ^ (1 << (p - D))` (walking zero) |
| 2D to 63 | `alt` if p is even, `mask ^ alt` if p is odd |

and `W = u << (32 - D)`. The burst sets and clears every bit at full scale,
shows the channel order (even channels ascend, odd channels descend) and
contains values that a 32-bit float path cannot carry exactly.

### 7.4 Payload

The payload is low-level noise with A active bits:

| | D = 16 | D = 24 | D = 32 |
|---|---|---|---|
| A | 10 | 16 | 24 |

```
s = asr(h, 32 - A)          (h taken as a signed 32-bit value)
W = s << (32 - D)
```

The sample is a sign-extended A-bit random value at the bottom of the D-bit
sample: every bit below the sign is exercised, and every bit of the file's
depth toggles. Bits below the depth are zero.

**Code sweep (kind 5).** In a code-sweep file the payload is full-scale noise
that carries every code of the depth. It replaces the low-level payload in
the same units: frames 72 to B - 1 of every block that is not an R-header
block, lead-in and tail included. The X-syncs, the walking-bit bursts and the
R-header blocks are those of any PCM file. The sweep units of a channel, in
file order, are numbered by the **sweep index** s, from block 0, frame 72
(R-header blocks hold no sweep units). For frame i >= 72 of block k:

```
s = ((k - (k + 7) / 16) * (B - 72) + (i - 72))  mod 2^D
u = lbD(D, sweep_key[c], s)
W = u << (32 - D)
```

(k + 7) / 16 is the number of R-header blocks before block k (blocks 8, 24,
40, ...). The sample is the D-bit code u as a two's-complement integer. Any
2^D consecutive sweep units of a channel hold each D-bit code exactly once,
so a body of at least 2^D sweep units per channel proves every code (section
5.2 has the body lengths). lbD and the sweep key are in section 4.4.

### 7.5 Expected word, pseudocode

```
expected_W(c, n):
    k = n / B ; i = n mod B ; h = lb32(key[c], n)
    if i < 8:
        return ((xsync(c, k)[i] << 16) | (h >> 16)) & ~(2^(32-D) - 1)
    if i < 72:
        return walk(c, i - 8) << (32 - D)
    if k mod 16 == 8:
        j = i - 512 * M
        if 0 <= j < 237 * L:
            return chip(c, k, j / L) * 2^29
        return 0
    if kind == 5:
        s = ((k - (k + 7) / 16) * (B - 72) + i - 72) mod 2^D
        return lbD(D, key[c] & (2^D - 1), s) << (32 - D)
    return asr(h, 32 - A) << (32 - D)
```

### 7.6 R-header chips

The **chip length** L is 11 x M frames in the 44.1 kHz family (0.2494 ms) and
12 x M frames in the 48 kHz family (0.2500 ms).

The chip sequence of channel c in block k has 237 chips, each +1 or -1:

1. The Barker-13 sequence `+1 +1 +1 +1 +1 -1 -1 +1 +1 -1 +1 -1 +1`.
2. The seven R-header words r0 to r6, most significant bit first,
   Manchester-coded: a 1 bit is the chip pair (+1, -1), a 0 bit is (-1, +1).

Chip q (0 to 236) covers frames 512M + qL to 512M + (q + 1)L - 1 of the
block. A chip of value v is the word `W = v x 2^29`, that is
v x 2^(D-3) in D-bit samples, or plus or minus a quarter of full scale. The
frames 72 to 512M - 1 and from 512M + 237L to the end of the block are zero.

The chip span lasts 59.1 ms (44.1 kHz family) or 59.25 ms (48 kHz family) and
ends at frame 3119M or 3356M. At least 9 ms of silence precede it and 15 ms
follow it, so that resampling filters settle around the burst and the chips.

## 8. DSD signal

### 8.1 Bitstream

A DSD channel is a sequence of bits b(m) at the DSD rate, m from 0. A bit
value 1 is the positive pulse. Rate codes 10 to 12 apply (M = 64, 128, 256)
and B = 4096 x M bits. DSD has no walking-bit burst.

### 8.2 Block layout

| Bits i of block k | Normal block | R-header block (k mod 16 = 8) |
|---|---|---|
| 0 to 127 | X-sync | X-sync |
| 128 to B - 1 | payload | idle pattern, except the chip span |
| 512M to 512M + 237L - 1 | (payload) | R-header chips |

### 8.3 X-sync bits

Bits 0 to 127 of a block are the eight X-sync words (section 6) of the
channel, each most significant bit first: bit i is bit `15 - (i mod 16)` of
word `w(i / 16)`.

### 8.4 Payload

Each hash bit is sent as a pair of opposite bits, which keeps the in-band
noise low (section 10): `q = m >> 6`,
`x = bit (31 - ((m >> 1) mod 32)) of lb32(key[c], q)`, `b(m) = x` for even
m and `1 - x` for odd m.

### 8.5 R-header blocks

The chip length is L = 11 x M bits (704 bits = 0.2494 ms at DSD64), the chip
sequence is that of section 7.6, and chip q covers bits 512M + qL to
512M + (q + 1)L - 1 of the block. Inside chip q, with j the bit offset from
the chip's start:

- chip +1: `b = 1` unless `j mod 4 = 3`, the pattern 1110 (75 % ones);
- chip -1: the complement, 0001 (25 % ones).

As bytes (most significant bit first) a +1 chip is 0xEE repeated, a -1 chip
0x11 repeated. A DSD-to-PCM converter turns the chips into levels of plus or
minus half of full modulation, so the R-header survives conversion. Every
other bit of the block from bit 128 on is the DSD idle pattern
`b(m) = bit (7 - (m mod 8)) of 0x69`, that is bytes 0x69.

Because both patterns are fixed, a checker regenerates an R-header block
exactly from the decoded words, without a modulator.

### 8.6 Expected bit, pseudocode

```
expected_bit(c, m):
    k = m / B ; i = m mod B
    if i < 128:
        return (xsync(c, k)[i / 16] >> (15 - i mod 16)) & 1
    if k mod 16 == 8:
        j = i - 512 * M
        if 0 <= j < 237 * L:
            p = 0 if (j - (j / L) * L) mod 4 == 3 else 1
            return p if chip(c, k, j / L) > 0 else 1 - p
        return (0x69 >> (7 - m mod 8)) & 1
    x = (lb32(key[c], m >> 6) >> (31 - (m >> 1) mod 32)) & 1
    return x ^ (m & 1)
```

## 9. Containers

All containers MUST be written deterministically: no dates, times, host or
user data, so that the same generator gives byte-identical files. Files of
the same name, below, are files whose names agree after the number (11.2).

### 9.1 WAV (PCM)

- RIFF/WAVE, little-endian, chunks in this order: `fmt `, `LIST` (INFO),
  `data`.
- `fmt `: 16-bit files with at most 2 channels use `WAVE_FORMAT_PCM`
  (format tag 1, 16-byte chunk). All others (24-bit, 32-bit, more than two
  channels) use `WAVE_FORMAT_EXTENSIBLE` (tag 0xFFFE, 40-byte chunk,
  cbSize 22) with wValidBitsPerSample = D, dwChannelMask 0x3 for stereo
  (0x4 for mono, 2^nch - 1 otherwise) and SubFormat
  KSDATAFORMAT_SUBTYPE_PCM, 00000001-0000-0010-8000-00AA00389B71.
- Samples are D/8 bytes, little-endian two's complement, channels
  interleaved by frame.
- `LIST`/`INFO` holds INAM (title), IART (artist), IPRD (album) and ICMT
  (comment) as NUL-terminated ASCII, each padded to an even length.

### 9.2 FLAC

A FLAC file of the set is the corresponding WAV encoded losslessly; decoding
it MUST give the WAV's samples exactly. The published set encodes with the
reference `flac` tool, version 1.4.0 or later, at `-8` with verification,
and tags TITLE, ARTIST, ALBUM and COMMENT as Vorbis comments. FLAC output is
byte-identical for the same `flac` version.

PCM FLAC files are published for the 16- and 24-bit files at all ten rates.
Every DoP-WAV file (section 9.5) is also published as FLAC, for DSD64, DSD128
and DSD256, at 176.4, 352.8 and 705.6 kHz.

Rates above 655 350 Hz are outside the FLAC subset: the PCM files at 705.6
and 768 kHz and the DSD256 DoP file are encoded with `--lax`, and need
libFLAC 1.4.0 or later to decode. Older decoders and players may not play
them; the WAV and AIFF files of the same name hold the same samples. The
published set has no 32-bit PCM files (section 11.1), so it has no 32-bit
FLAC, which also needs libFLAC 1.4.0.

### 9.3 DSF

Sony DSD Stream File, version 1, little-endian:

- `DSD ` chunk (28 bytes): total file size, offset of the metadata.
- `fmt ` chunk (52 bytes): format version 1, format ID 0 (DSD raw), channel
  type 2 (stereo; 1 for mono), channel count, sampling frequency (the DSD
  rate), bits per sample 1 (LSB first), sample count (DSD bits per channel),
  block size per channel 4096, reserved 0.
- `data` chunk: blocks of 4096 bytes per channel, channel 0's block first,
  then channel 1's, and so on. Within each byte the earliest DSD bit is the
  least significant bit. A BPC1 block is a whole number of DSF blocks, so a
  whole file needs no padding; a part of the gapless set (11.7) ends in a
  block padded with zero bytes. The sample count gives the bits that count;
  Sony's [DSF specification 1.01](https://dsd-guide.com/sites/default/files/white-papers/DSFFileFormatSpec_E.pdf)
  fixes the block size and asks to "fill ZERO(0x00) for unused sample data
  area in the block" (Annotation 3).
- Metadata: an ID3v2.3 tag at the end of the file (TIT2, TPE1, TALB, COMM;
  ISO-8859-1 text, here plain ASCII).

### 9.4 DFF

Philips DSDIFF 1.5, big-endian, 64-bit chunk sizes, chunks padded to an even
length:

- `FRM8` form of type `DSD ` holding, in order:
  - `FVER` 0x01050000;
  - `PROP` of type `SND ` with `FS  ` (the DSD rate), `CHNL` (channel count
    and IDs `SLFT`, `SRGT` for stereo), `CMPR` (`DSD `, "not compressed")
    and, for stereo, `LSCO` 0;
  - `DSD ` sound data: one byte per channel in turn, earliest DSD bit in the
    most significant bit;
  - `DIIN` with `DIAR` (artist) and `DITI` (title);
  - `ID3 ` with the same ID3v2.3 tag as the DSF file.

### 9.5 DoP in WAV, FLAC, AIFF and ALAC

The DoP-WAV file of a DSD rate carries the DSD bits of the DSF and DFF files
of that rate in a 24-bit WAV (`WAVE_FORMAT_EXTENSIBLE`) at
the carrier rate DSD / 16 (176.4, 352.8 or 705.6 kHz); the DoP-FLAC,
DoP-AIFF and DoP-ALAC files are the same samples in FLAC (section 9.2), AIFF
(9.6) and ALAC (9.7). Frame f carries DSD bits 16f to 16f + 15 of each
channel:

```
sample = (marker << 16) | (bits 16f..16f+7 as a byte, earliest bit as MSB) << 8
                        | (bits 16f+8..16f+15 as a byte, earliest bit as MSB)
marker = 0x05 for even f, 0xFA for odd f        (the same in every channel)
```

The BPC1 blocks of a DoP file therefore have 4096 x M / 16 frames, the same
as a PCM block at the carrier rate.

### 9.6 AIFF (PCM)

The generator writes AIFF files itself, from the samples of the WAV of the
same name (16 or 24 bits). Format: Apple AIFF (not AIFF-C), big-endian,
chunks padded to an even length. A `FORM` chunk of type `AIFF` holds, in this
order:

- `COMM` (18 bytes): the channel count (int16), the number of frames
  (uint32), the sample size D (int16) and the sample rate as an 80-bit
  IEEE 754 extended float: sign 0, exponent 16383 + e with e the bit length
  of the rate minus 1, and the 64-bit mantissa `rate << (63 - e)`, so that
  its leading 1 is bit 63. For example 44 100 Hz is
  `400E AC44 0000 0000 0000`;
- `NAME`: the title as ASCII (section 11.3);
- `ANNO`: the comment as ASCII (section 11.3);
- `SSND`: offset 0 and block size 0, then the samples, D / 8 bytes each,
  two's complement, most significant byte first, channels interleaved by
  frame.

AIFF carries no artist and no album tag here, except in the gapless set
(11.7). Decoding an AIFF file of the set
MUST give the samples of the WAV of the same name exactly; the generator's
tests do so. AIFF output is byte-identical on every run. No AIFF is made for
DSF, DFF or 32-bit files.

### 9.7 ALAC (MP4)

An ALAC file of the set is the corresponding WAV encoded as Apple Lossless
in MP4 (`.m4a`); decoding it MUST give the WAV's samples exactly. The
generator encodes with Apple's reference ALAC encoder, the open-source
codec of [macosforge/alac](https://github.com/macosforge/alac) (Apache-2.0)
at commit `c38887c`, a git submodule at `generator/third_party/alac`. A small
helper, `generator/alac_tool.cpp`, built from the submodule's `codec/`
sources with the host C++ compiler, encodes the WAV's samples in packets of
4096 frames, the last one partial, as afconvert does. It then decodes every
file with Apple's decoder and compares it with the WAV. Its packets are
byte-identical to those of afconvert on macOS; unlike afconvert, the
generator adds no trailing packet of 0 frames.

The magic cookie (the 24-byte `ALACSpecificConfig`) is the encoder's:
frame length 4096, compatible version 0, the bit depth, pb 40, mb 10, kb 14,
the channel count, maxRun 255 and the sample rate. Its maxFrameBytes is the
largest packet, and its avgBitRate is floor(8 x the bytes of all packets x
the rate / the frames), as afconvert fills them (afconvert's value also
counts its trailing packet).

The generator writes the MP4 itself, with no times (creation and
modification 0), so the file is byte-identical on every run. Its bytes
depend on the pinned codec, as measured with the helper built by GCC 15.2:
the encoder has undefined behaviour (left shifts of negative values in
`codec/dp_enc.c`, lines 109, 137 and 230, and misaligned 32-bit accesses in
`codec/ag_enc.c`, lines 196 to 246), so a helper built by another compiler
or with other flags may write other bytes, which must still decode to the
WAV's samples. It
holds, in this order:

- `ftyp`: major brand `M4A `, minor version 0, compatible brands `M4A `,
  `mp42`, `isom`;
- `moov`: `mvhd` (time scale the sample rate, duration the frames), one
  `trak` (`tkhd` with flags 3, `mdhd` with the same time scale and
  duration and language `und`, `hdlr` `soun` named `SoundHandler`, and in
  `minf` `smhd`, `dinf` and `stbl`), then `udta`/`meta`/`ilst` with the MP4
  tags `\xa9nam` (title), `\xa9too` (the encoder:
  `Apple ALAC encoder, macosforge/alac c38887c`) and `\xa9cmt` (comment),
  as UTF-8 `data` atoms of type 1. No artist or album tag is written,
  except in the gapless set (11.7);
- `mdat`: the packets back to back.

`stbl` holds `stsd` with one `alac` sample entry (data reference 1, the
channel count, the sample size D, the rate as 16.16 fixed point if it is
below 65 536 Hz, else 0, and an `alac` atom with the magic cookie), `stts`
(4096 frames per packet, the last one its own count), `stsc` with chunks
of 32 packets, `stsz` and `stco`. There is no edit list.

ALAC files are published at all ten PCM rates and for every DoP file,
DSD256 at 705.6 kHz included. Apple's reference encoder and decoder take
these rates, and the generator's round trip checks them, but Apple's own
ALAC decoders refuse ALAC above 384 kHz: on macOS 27.2, AudioToolbox
(`afconvert`) refuses every rate above 384 000 Hz with `'fmt?'`, and iOS's
built-in playback presumably does the same. The ALAC files at 705.6 and
768 kHz and the DSD256 DoP file are therefore for players with their own
ALAC decoder; the WAV and AIFF files of the same name hold the same
samples. No ALAC is made for DSF, DFF or 32-bit files.

Decoders based on ffmpeg (8.0.1 and its current development version) decode
some packets of Apple's encoder wrongly, and ffmpeg's encoder writes some
packets that Apple's decoder reads differently: the two clamp the history
of the adaptive Golomb coder on different values (ffmpeg on the coded value
plus the zero-run modifier, Apple on the coded value alone), which matters
when the value is exactly 0xFFFF right after a run of zeros. Of the
published files, ffmpeg 8.0.1 decodes the regular PCM files at 384, 705.6
and 768 kHz and the sweep files at 192 kHz 24-bit, 352.8 kHz 24-bit, 384,
705.6 and 768 kHz wrongly. The
checker therefore decodes ALAC with Apple's decoder too.

## 10. Levels and safety

| Content | Level |
|---|---|
| PCM payload, 16-bit | peak -36.1 dBFS, RMS -41.0 dBFS |
| PCM payload, 24- and 32-bit | peak -48.2 dBFS, RMS -53.0 dBFS |
| PCM code-sweep payload (kind 5) | full-scale noise: all codes equally often, peak 0 dBFS, RMS -4.8 dBFS |
| PCM X-sync and walking-bit burst | full scale, 72 frames per block (1.8 % of the time at M = 1, less at higher rates) |
| PCM R-header chips | square wave at plus or minus 0.25 of full scale (-12.0 dBFS) |
| DSD payload | in-band noise (0-20 kHz) -53 dB re full modulation at DSD64, -62 dB at DSD128, -71 dB at DSD256 |
| DSD R-header chips | plus or minus 50 % modulation |

The bursts and chips are loud clicks and tones. Every file MUST carry the
warning `TEST - DO NOT PLAY ON SPEAKERS` in its title and comment tags
(section 11.3), and every release archive carries it as a file name and in
its README (section 11.4). The audio file names carry no warning. The
code-sweep files are loud for their whole length: their archives carry a
stronger warning (section 11.6).

## 11. The published test set

### 11.1 Files

| Group | Files | Body |
|---|---|---|
| PCM WAV | 16- and 24-bit at all ten PCM rates (20 files) | 10 s |
| PCM FLAC, AIFF, ALAC | the PCM WAV files as FLAC, as AIFF and as ALAC (`.m4a`) (20 files each) | as the WAV |
| DSD | DSF, DFF and DoP-WAV for DSD64, DSD128 and DSD256 (9 files) | 10 s |
| DoP-FLAC, DoP-AIFF, DoP-ALAC | the three DoP-WAV files as FLAC, as AIFF and as ALAC (3 files each) | 10 s |
| Negative | deliberately faulty copies of PCM 44.1 kHz 24-bit and DSD64 DoP-WAV, each altered and incomplete, as WAV (4 files, section 11.5) | as the file they copy |

The set is 102 files, 1 650 670 139 bytes (about 1.65 GB). FLAC, AIFF and
ALAC files are made for the 16- and 24-bit PCM and DoP files only, not for
DSF, DFF, 32-bit or negative files. The FLAC files above 655 350 Hz need libFLAC 1.4.0
or later (section 9.2), and Apple's own ALAC decoders refuse the ALAC files
above 384 kHz (section 9.7). Depth 32 is part of the format but not of the
set: `python3 -m generator --32bit` also writes the ten 32-bit PCM WAVs
(about 289 MB, numbers 00060 to 00069), which are not published and not in
the release archives.

All files are stereo, use seed 0x42504331 and have a 2 s lead-in and a
0.5 s tail. The code-sweep set (section 11.6) and the gapless set (section
11.7) are separate and not part of these 102 files.

### 11.2 Names

Every file name starts with a five-digit number and a hyphen:

```
<n>-bpc1_pcm_<rate>_<depth>bit.wav|.flac|.aiff|.m4a
<n>-bpc1_<dsd>.dsf
<n>-bpc1_<dsd>.dff
<n>-bpc1_<dsd>_dop-<carrier>_24bit.wav|.flac|.aiff|.m4a
negative/<n>-<name>_altered|_incomplete.wav
```

`<rate>` and `<carrier>` are the rate in kHz with three integer digits and
the first decimal after a `k` when it is not zero: `044k1`, `048k`, `088k2`,
`096k`, `176k4`, `192k`, `352k8`, `384k`, `705k6`, `768k`. `<dsd>` is
`dsd064`, `dsd128` or `dsd256`. Examples: `00033-bpc1_pcm_096k_24bit.wav`,
`00130-bpc1_pcm_044k1_24bit.flac`, `00330-bpc1_pcm_044k1_24bit.m4a`,
`01400-bpc1_dsd064.dsf`, `01000-bpc1_dsd064_dop-176k4_24bit.wav`.

The number `<n>` is five decimal digits, T O F PP:

| Digit | Meaning |
|---|---|
| T | type: 0 regular (11.1), 1 code sweep (11.6), 2 gapless (11.7), 9 negative (11.5 to 11.7); 3 to 8 are free |
| O | output: 0 PCM, 1 DSD (DSF, DFF and DoP) |
| F | file format: 0 WAV, 1 FLAC, 2 AIFF, 3 ALAC, 4 DSF, 5 DFF |
| PP | position in the group T O F, 00 to 99 |

Let r be the rate's index in ascending order (PCM: 0 for 44.1 kHz to 9 for
768 kHz; DSD: 0 DSD64, 1 DSD128, 2 DSD256) and b the depth's base (16-bit
0, 24-bit 30, DSD 0). PP is b + r for a regular or code-sweep file, and
b + 2r + p - 1 for part p of a gapless pair, so the two parts of a pair have
consecutive numbers. The negative files take fixed positions in their
group instead, in this order: 90000 PCM 44.1 kHz 24-bit altered, 90001 its
incomplete copy, 90002 the 44.1 kHz 16-bit code sweep altered, 90003 and
90004 the gapless pair with a gap (part 1, part 2), 91000 DoP DSD64 altered,
91001 its incomplete copy. For example, 00037 is the regular 24-bit 384 kHz
WAV and 00137 its FLAC. Positions 10 to 29 and 40 to 99 hold no regular file
and leave room for more rates or another depth; the unpublished 32-bit WAVs
take b = 60.

All numbers have five digits, so names sort by number in byte order. A
folder that holds the files of several archives therefore plays regular
PCM, regular DoP, DSF and DFF, the code sweep, the gapless pairs, then the
negative files. A released number never changes; a file added later takes a
free position in its group (a negative file the next free one in its 9 O F
group), which need not follow these rules. Files whose numbers differ only
in F hold the same units (3.1): in this document, files of the same name are
files whose names agree after the number. The test vectors (13) name their
files with the number.

A negative test file is in the folder `negative` of a generated set, named
after the file it copies, e.g. `negative/90000-bpc1_pcm_044k1_24bit_altered.wav`;
the release puts it in the negative archive (11.4) instead. The names carry
no warning; the tags and the release archives do.

### 11.3 Tags

All tag text is ASCII:

| Tag | Value |
|---|---|
| Title | `TEST - DO NOT PLAY ON SPEAKERS - <n> BPC1 <description> <format>`, e.g. `... - 00130 BPC1 PCM 44.1 kHz 24-bit FLAC`, `... - 01400 BPC1 DSD64 DSF`, `... - 01000 BPC1 DSD64 DoP 176.4 kHz 24-bit WAV` |
| Artist | `bit-perfect-checker` |
| Album | `BPC1 v1 test signals` |
| Comment | `BPC1 v1 bit-perfect test signal, low-level payload with full-scale bursts. TEST - DO NOT PLAY ON SPEAKERS OR HEADPHONES.` |

The title carries the warning first, then the file's number `<n>` (11.2)
and its format, `WAV`, `FLAC`, `AIFF`, `ALAC`, `DSF` or `DFF`, so the file
name can be read off the title. `<description>` is `PCM <rate> <depth>-bit`
(the rate as in `44.1 kHz`), `<dsd>` (`DSD64`, `DSD128`, `DSD256`) for DSF
and DFF, or `<dsd> DoP <carrier> 24-bit`.

AIFF and ALAC files carry the title and the comment only (sections 9.6 and
9.7), except in the gapless set (11.7).

A negative test file's title ends in `- NEGATIVE TEST, DELIBERATELY ALTERED`
or `- NEGATIVE TEST, DELIBERATELY INCOMPLETE`, and its comment goes on with
`NEGATIVE TEST, deliberately <altered or incomplete>: <the fault>. A checker
must report NOT_BIT_PERFECT.`, the fault as in section 11.5, for example
`NEGATIVE TEST, deliberately incomplete: 1000 frames cut out, from frame 1024
of body block 4 (body blocks counted from 0). A checker must report
NOT_BIT_PERFECT.`

A `SHA256SUMS` file lists the SHA-256 of every file of a generated set,
negative tests under their `negative/` name.

### 11.4 Release archives

The regular set and the code-sweep set (11.6) are each released as four zip
files, one per format, which hold only files that should pass. The negative
test files of the three sets (11.5 to 11.7) are released as one archive, and
the gapless pairs (11.7) as one archive. In the name `bpc1-r1-<format>.zip`, BPC1
names the format and its version (an incompatible format would be BPC2), and
r1 is the release of the file set: a fix to the files under the same format
becomes r2.

| Archive | Contents | Method | Size |
|---|---|---|---|
| `bpc1-r1-wav.zip` | every PCM WAV (20) and every DSD file (DSF, DFF and DoP-WAV, 9) | deflate, level 9 | 380 959 472 bytes (about 381 MB; 65.8 % of its entries' 578 995 839 bytes) |
| `bpc1-r1-flac.zip` | every FLAC: PCM (20) and DoP (3) | stored | 286 412 997 bytes (about 286 MB) |
| `bpc1-r1-aiff.zip` | every AIFF: PCM (20) and DoP (3) | stored | 454 214 431 bytes (about 454 MB) |
| `bpc1-r1-alac.zip` | every ALAC (`.m4a`): PCM (20) and DoP (3) | stored | 297 675 322 bytes (about 298 MB) |
| `bpc1-r1-sweep-wav.zip` | the sweep WAVs (20) | stored | 1 271 464 720 bytes (about 1.27 GB) |
| `bpc1-r1-sweep-flac.zip` | the sweep FLACs (20) | stored | 1 204 256 583 bytes (about 1.20 GB) |
| `bpc1-r1-sweep-aiff.zip` | the sweep AIFFs (20) | stored | 1 271 463 591 bytes (about 1.27 GB) |
| `bpc1-r1-sweep-alac.zip` | the sweep ALACs (20) | stored | 1 228 869 129 bytes (about 1.23 GB) |
| `bpc1-r1-negative.zip` | every negative test file, as WAV: the regular set's (4, 11.5), the code-sweep set's (1, 11.6) and the gapless set's pair (2, 11.7), 7 files | stored | 37 882 144 bytes (about 38 MB) |
| `bpc1-r1-gapless.zip` | the gapless pairs (36 files) in six folders, one per format | stored | 56 857 601 bytes (about 57 MB) |

FLAC data does not compress further (deflate saves 1.0 %), so the FLAC archive
is stored, and so are the AIFF and ALAC archives; the WAV and DSD files
deflate to 45 to 78 %. Only the regular WAV archive holds DSF and DFF files.

Each archive holds one folder named like the archive, containing the
entries below, sorted by name (11.2): the warning file first, then the
numbered audio files, then `README.txt` and `SHA256SUMS`; in the gapless
archive, `README.txt` and `SHA256SUMS` come before the six folders.

1. an empty file named
   `!!! TEST SIGNALS - DO NOT PLAY ON SPEAKERS OR HEADPHONES !!!.txt` (the
   sweep archives: `!!! FULL-SCALE NOISE - NEVER PLAY ON SPEAKERS OR
   HEADPHONES !!!.txt`; the negative archive, which holds sweep files too:
   both, the full-scale one first);
2. `README.txt`, ASCII with CRLF line ends: the warning in full, what the
   files are, how to use them with a checker, how to run them for hours to
   find rare faults (play the files one after another continuously, or loop
   one file gaplessly so that it arrives as one continuous stream; every
   repetition gets its own result), a section on clipping and level
   indicators, and the MIT licence. The clipping section says that the files
   reach full scale, so a player or tool that measures levels may report
   clipping or peaks at 0 dBFS, which is expected and harmless when the
   player passes the samples unchanged, and to turn off ReplayGain, Sound
   Check, volume normalization, limiters and "prevent clipping" options,
   which change the samples and make the file fail. The AIFF and ALAC
   archives say how the title and comment are stored; the FLAC ones that the
   files at 705.6 and 768 kHz need libFLAC 1.4.0 or later, and the ALAC ones
   that Apple's own ALAC decoders refuse ALAC above 384 kHz, so those files
   are for players with their own ALAC decoder, and that the WAV and AIFF
   files of the same name hold the same samples. The format archives give
   example names and say that the numbers keep the files of all archives in
   playing order in one folder, and name the negative archive. The negative archive's `README.txt` has the
   warnings, says what the files are, that none may check as
   `BIT_PERFECT`, that they are WAV only because a checker does not depend
   on the container, and the fault in each and the verdict it gives
   (`NOT_BIT_PERFECT`, 11.5 to 11.7), when to use them (to see a device or
   a checker reject altered and incomplete files), and has the clipping
   section and the MIT licence;
3. `SHA256SUMS` of the audio files (the gapless archive: names with their
   folder);
4. the audio files (the gapless archive: in six folders, one per format;
   the negative archive: the seven WAV files of 11.5 to 11.7, named as in
   their generated sets but without `negative/`).

Every name inside is ASCII and valid on Windows, macOS and Linux: none of
`< > : " / \ | ? *`, no trailing space or dot.

The archives are deterministic: entries sorted by name, every entry dated
2026-11-02 00:00:00, Unix permissions 0644 for files and 0755 for the
folders, no comments or extra fields, no data descriptors. A re-run gives
byte-identical archives (for the deflated archive, with the same zlib
version). `dist/SHA256SUMS` lists the archives' SHA-256.

### 11.5 Negative test files

Four files of the set are deliberately faulty, to test checkers: a checker
reports `NOT_BIT_PERFECT` for each of them (section 12.6). Each is a copy of
a file of the set with one fault in its body; its lead-in and tail are
unchanged. A port reproduces them exactly from the files they copy. They
are WAV only: a checker judges the units, whatever the container (3.1). With
the negative files of the code-sweep set (11.6) and of the gapless set
(11.7), seven files in all, they are developers' files, to see a checker
reject a fault, and the release holds them in `bpc1-r1-negative.zip`
(11.4), not in the format archives.

With L the lead-in block count, N the body block count and F the frames per
block (B for PCM, B / 16 DoP frames for DoP), let p = (L + 4) x F + 1024:
frame 1024 of body block 4 (body blocks counted from 0), after the X-sync and
the walking-bit burst, and in no R-header block (block L + 4 is block 26
for 44.1 kHz and DSD64).

| File | Copy of | Fault | p |
|---|---|---|---|
| `90000-bpc1_pcm_044k1_24bit_altered.wav` | `00030-bpc1_pcm_044k1_24bit.wav` | the low 8 bits of every sample of the body (frames L x F to (L + N) x F - 1) cleared: s & ~0xFF | - |
| `90001-bpc1_pcm_044k1_24bit_incomplete.wav` | `00030-bpc1_pcm_044k1_24bit.wav` | frames p to p + 999 removed | 107 520 |
| `91000-bpc1_dsd064_dop-176k4_24bit_altered.wav` | `01000-bpc1_dsd064_dop-176k4_24bit.wav` | DoP frames p to p + 999: the 16 DSD bits of every channel inverted (the 24-bit sample XOR 0x00FFFF), the markers unchanged | 427 008 |
| `91001-bpc1_dsd064_dop-176k4_24bit_incomplete.wav` | `01000-bpc1_dsd064_dop-176k4_24bit.wav` | DoP frames p to p + 999 removed (an even number, so the markers still alternate) | 427 008 |

A removal makes the file 1000 frames shorter; the WAV header gives the new
length. The containers and tags are those of the files they copy, with the
tags of 11.3.

All four fail 12.6 (c): units of the body differ from the expected ones, or
the body is not one run of consecutive units. The lead-in and the tail are
unchanged, so 12.6 (b) holds.

The reference checker's diagnosis (informative; not needed to decide a
verdict) names the faults as follows, using the names, reasons and
counts of 12.5 and 12.6:

| File | Diagnosis | Reason | Coverage and counts |
|---|---|---|---|
| PCM `_altered` | `ALTERED` | `TRUNCATED` to 16 bits | end `LAST`, missing none |
| DoP `_altered` | `ALTERED` | `BIT_ERRORS` (2000 DSD bytes per channel differ) | end `LAST`, missing none |
| PCM `_incomplete` | `INCOMPLETE` | none | end `LAST`, missing `middle`, `dropped` 1000 |
| DoP `_incomplete` | `INCOMPLETE` | none | end `LAST`, missing `middle`, `dropped` 2000 (DSD bytes per channel) |

### 11.6 The code-sweep set

A second, separate set of PCM code-sweep files (kind 5, section 7.4) proves
that every sample code of the depth arrives unchanged. Its files are full-scale
noise for their whole length: **never play them on speakers or headphones.**
The verdict rule (section 12.6) is that of the regular set. The generator
writes the set only on request (`python3 -m generator --sweep`), and
`python3 -m generator package --sweep` builds its archives.

| Group | Files | Body |
|---|---|---|
| Sweep WAV | 16- and 24-bit at all ten PCM rates (20 files) | 16-bit: 10 s; 24-bit: 413 s at 44.1 kHz down to 23.5 s at 768 kHz (section 5.2) |
| Sweep FLAC, AIFF, ALAC | the sweep WAV files as FLAC, as AIFF and as ALAC (20 files each) | as the WAV |
| Negative | `90002-bpc1_pcm_044k1_16bit_sweep_altered.wav` (1 file) | as the file it copies |

The set is 81 files, 4 978 239 923 bytes (about 5.0 GB). The 24-bit files
last 26.0 s (768 kHz) to 415.7 s (44.1 kHz) with lead-in and tail and the
WAV files are about 110 to 120 MB each; the 16-bit files last 12.6 s. All
files are stereo, use seed 0x42504331 and have a 2 s lead-in and a 0.5 s
tail. The FLAC and ALAC files at 705.6 and 768 kHz are as in the regular
set (sections 9.2 and 9.7).

**Names.** `<n>-bpc1_pcm_<rate>_<depth>bit_sweep.wav|.flac|.aiff|.m4a` with
`<n>` and `<rate>` as in section 11.2 (10F00 to 10F09 for 16 bits, 10F30
to 10F39 for 24 bits), e.g. `10130-bpc1_pcm_044k1_24bit_sweep.flac`, and,
in the generated set, `negative/90002-bpc1_pcm_044k1_16bit_sweep_altered.wav`.

**Tags.** As in section 11.3, except:

| Tag | Value |
|---|---|
| Title | `TEST - DO NOT PLAY ON SPEAKERS - <n> BPC1 PCM <rate> <depth>-bit code sweep <format>, FULL-SCALE NOISE`, e.g. `... - 10130 BPC1 PCM 44.1 kHz 24-bit code sweep FLAC, FULL-SCALE NOISE` |
| Comment | `BPC1 v1 bit-perfect test signal, full-scale code sweep. TEST - DO NOT PLAY ON SPEAKERS OR HEADPHONES.` |

**Negative sweep file.** It is a copy of the 16-bit sweep file at 44.1 kHz,
`10000-bpc1_pcm_044k1_16bit_sweep.wav`, as WAV, with one fault in its body:
every body sample of channel 0 whose code is 2^(D-1) - 2 (32766 at 16 bits)
becomes the next code, 2^(D-1) - 1, one code near full scale, a fault only a
sweep finds. A checker reports `NOT_BIT_PERFECT`, with `diff` at the first
changed sample (12.6 c). The title ends in `- NEGATIVE TEST,
DELIBERATELY ALTERED`, and the comment goes on with `NEGATIVE TEST,
deliberately altered: every body sample of channel 0 with code <c> changed to
<c + 1>. A checker must report NOT_BIT_PERFECT.` A regular file of the same
depth does not carry that code, so the same change leaves it
`BIT_PERFECT` (section 13).

**Archives.** The four `bpc1-r1-sweep-*` archives of section 11.4, all
stored, hold the 20 sweep files of their format; the negative sweep file is
in `bpc1-r1-negative.zip`. Each is organised as in 11.4, with the
warning entry `!!! FULL-SCALE NOISE - NEVER PLAY ON SPEAKERS OR HEADPHONES
!!!.txt`. Its `README.txt` has the full-scale warning, what the files are,
how to use them and the clipping section of section 11.4. The regular
archives are unchanged.

### 11.7 The gapless set

A third, separate set tests gapless playback. Each **pair** is a regular file
of the set (11.1) cut in two inside its body: part 1 holds units 0 to J - 1,
part 2 units J to the end. Played gaplessly, a pair arrives as the regular
file, with its identity, and gives one result (12.6). The set assumes a
bit-perfect transport: it is played after the regular files of the same
formats give `BIT_PERFECT`. `python3 -m generator --gapless` writes it and
`python3 -m generator package --gapless` builds its archive.

| Group | Pairs |
|---|---|
| PCM | `bpc1_pcm_044k1_16bit`, `bpc1_pcm_044k1_24bit`, `bpc1_pcm_048k_16bit`, `bpc1_pcm_048k_24bit`, each as WAV, AIFF, FLAC and ALAC (16) |
| DSD | `bpc1_dsd064` as DSF and as DFF (2) |
| Negative | `bpc1_pcm_044k1_16bit` with a gap before part 2, WAV (1 pair, 2 files) |

The set is 38 files, 59 072 484 bytes (about 59 MB): the 36 files of the 18
pairs and the negative pair. The DSD pairs are DSF and DFF only: DoP files
carry the same DSD bits (3.1), and a DoP pair would test the PCM path that
the PCM pairs already test.

**The cut.** J is one unit after 7.5 s. PCM: J = 7.5 x rate + 1 frames
(330 751 at 44.1 kHz, block 80, frame 3071; 360 001 at 48 kHz, block 87,
frame 3649). DSD: J = 16 x (7.5 x rate / 16 + 1) bits, after 1 323 001 DoP
frames (21 168 016 bits: block 80, bit 196 496 at DSD64). J lies in the
payload of a body block that is not an R-header block. J, for DSD its DoP
frame count, is odd and not a multiple of 3, 5 or 7, so it falls on no block
boundary, no 4096-frame FLAC or ALAC packet boundary and no DSF block
boundary, and, counted from the file's start, on no boundary of 2^k frames
or of a 6-frame USB packet. The units at J differ from zero and from the
units before and after them in every channel (in both DSD pairs, DSD bytes
0x95 and 0xA9), so a unit inserted, dropped or repeated at J changes the
unit at J. Part 1 of a DSD pair holds an odd number of DoP frames, so a
player that starts the DoP marker phase again with part 2 repeats a marker.

**Files.** Each part is a whole file of its format (9.1 to 9.4, 9.6, 9.7)
with its own length and the identity (kind, rate, depth, channels, seed) of
the regular file; the FLAC and ALAC parts end on a short frame or packet,
and the FLAC parts' STREAMINFO and the ALAC parts' last `stts` entry carry
their frame counts. A DSF part ends inside a 4096-byte block, whose rest is
zero bytes; its sample count gives the bits that count (in its last block,
part 1 has 4082 bytes per channel, part 2 has 14). Decoding both parts of a
pair and joining them MUST give the units of the regular file exactly.

**Names.** One folder per format, `wav`, `aiff`, `flac`, `alac`, `dsf`,
`dff`; a part is named after its regular file with `_gapless_part1` or
`_gapless_part2`, after its number (11.2), e.g.
`flac/20130-bpc1_pcm_044k1_24bit_gapless_part1.flac` and
`dsf/21400-bpc1_dsd064_gapless_part1.dsf`; the parts of a pair have
consecutive numbers.

**Tags.** As in 11.3, except:

| Tag | Value |
|---|---|
| Title | `TEST - DO NOT PLAY ON SPEAKERS - <n> BPC1 <description> <format> gapless, part <p> of 2`, e.g. `... - 20130 BPC1 PCM 44.1 kHz 24-bit FLAC gapless, part 1 of 2` |
| Album | `BPC1 gapless <description> <format>`, one per pair, e.g. `BPC1 gapless PCM 44.1 kHz 24-bit FLAC`; `BPC1 gapless DSD64 DSF` and `BPC1 gapless DSD64 DFF` |
| Track | `<p>` of 2 |
| Comment | `BPC1 v1 bit-perfect test signal, gapless pair, part <p> of 2: one test file cut at 7.500 s. Play both parts gaplessly; a checker gives one result for the pair. TEST - DO NOT PLAY ON SPEAKERS OR HEADPHONES.` |

WAV adds `ITRK` (`1` or `2`) to `LIST`/`INFO`, after `ICMT`; FLAC adds
`TRACKNUMBER` and `TRACKTOTAL` after `COMMENT`. AIFF adds an `ID3 ` chunk
after `SSND`, holding the ID3v2.3 tag of 9.3 with `TRCK` (`1/2` or `2/2`)
between `TALB` and `COMM`; DSF and DFF add the same `TRCK` to their tag. ALAC
adds, after `\xa9cmt`, `\xa9ART` and `\xa9alb` (UTF-8, type 1), `trkn`
(type 0: 8 bytes, 0, `<p>` and 2 as 16-bit big-endian numbers, then 0) and
`pgap` (type 21: one byte, 1).

**Archive.** `bpc1-r1-gapless.zip`, stored, organised as in 11.4: the
warning file, `README.txt` (the warning, what the
pairs are, when and how to play them, the results below, the clipping
section and the MIT licence), `SHA256SUMS` (names with their folder), and
the six folders. The negative pair is in `bpc1-r1-negative.zip` instead.

**Negative pair.** `90003-bpc1_pcm_044k1_16bit_gapless_part1_gap.wav` and
`90004-bpc1_pcm_044k1_16bit_gapless_part2_gap.wav`, in the folder `negative`
of the generated set, copy the WAV pair of `bpc1_pcm_044k1_16bit` with the
commonest fault of a player that is not gapless built in: part 1 is
unchanged, part 2 starts with 10 ms of digital silence (441 zero frames)
before the units of the original part 2. Their titles end in `- NEGATIVE
TEST, GAP INSERTED`, their album is `BPC1 gapless PCM 44.1 kHz 16-bit WAV,
GAP INSERTED`, and their comment goes on with `NEGATIVE TEST, gap inserted:
441 zero frames (10 ms of silence) before the first frame of part 2. Played
gaplessly, the pair must give NOT_BIT_PERFECT.` Played gaplessly, the pair
gives one `NOT_BIT_PERFECT` with `diff` 7.500 (12.6 c, the zeros at the
cut); played as two streams, two `NOT_BIT_PERFECT` results, as any pair.

**Results.** *Informative: they follow from 12.6.* A pair played gaplessly
gives one `BIT_PERFECT`. Units inserted, dropped or repeated at the cut, or
DoP markers that start again with part 2, give one `NOT_BIT_PERFECT` with
`diff` 7.500; a change that reaches back into part 1, such as a crossfade
or the end of part 1 cut short, gives a little less (7.490 for a 10 ms
linear crossfade). A stream that stops between the parts gives two
`NOT_BIT_PERFECT` results: `end` `STOP` with `diff` 7.500, then `diff` at the
first block after the cut, 7.523 (44.1 kHz, DSD64) or 7.509 (48 kHz). A
pause in which the host sends no audio carries no units and is not seen by
the verdict. A checker that reads files judges each as a stream of its own,
so each part alone gives `NOT_BIT_PERFECT`; the reference checker's `--join`
judges both parts as one stream.

## 12. Checking

This section says when a file is bit-perfect and what a conforming checker
reports (12.1, the Lock and Comparison paragraphs of 12.2, and 12.6:
normative), and how the reference checker finds it and what else it can say
about a changed file (the rest, informative). A checker that reaches the same
verdicts by other means conforms. The reference checker proves bit-perfect
arrival; its diagnosis of what changed (the gap search and inverted search of
12.2, 12.3 to 12.5, and the coverage and gap lists of 12.6) is a desktop tool
and a conforming checker does not need it.

### 12.1 Transport

- **PCM**: any PCM stream.
- **DoP**: a PCM stream with at least 24 bits per sample. A checker reads the
  16 DSD bytes of an X-sync from eight DoP frames whose markers alternate,
  the same in every channel. A file whose X-syncs are read so is DoP, and its
  frames are unpacked to 16 DSD bits each (section 9.5). This is decided per
  X-sync, not per stream: a PCM file and a DoP file in one gapless stream
  give two results. A DoP file received without markers has no readable
  X-sync and gives `UNKNOWN_SIGNAL`; with markers missing or wrong in some
  body frames it gives `NOT_BIT_PERFECT` (12.6 d).
- **DSD**: native DSD (DSF, DFF, or a raw capture such as ALSA's
  `DSD_U32_BE`), unpacked to bits per channel.

### 12.2 Exact layer

**Lock.** Search every channel for the X-sync: in PCM the top 16 bits of
eight consecutive words; in DSD sixteen consecutive bytes (most significant
bit first) starting with 0xB1 0xC0 0x5E 0xED. Each X-sync that passes the
checks of section 6, with its reserved flag bits 0 (3.4), is an **anchor**:
it ties a received position to the start of block k. The file's identity
(kind, depth, rate, nch, seed) is that of its X-syncs; an X-sync of another
identity starts the next file (12.6). A conforming checker does not search
for inverted X-syncs: a stream whose X-syncs arrive inverted gives
`UNKNOWN_SIGNAL` (12.6).

**Comparison.** The received units are compared with the expected ones
(sections 7.5 and 8.6), received channel c against source channel c. A PCM
unit matches if every channel's word W is equal. A received slot wider than
the file depth carries the file's samples left-justified with zero bits below
(for example a 16-bit file in a 24-bit slot); in the W representation this is
the same word, so it matches and is bit-perfect.

**Inverted X-sync search.** *Informative: diagnosis; not needed to decide a verdict.* The reference checker's diagnosis
does it; a conforming checker does not. If no X-sync is found in a PCM
stream, it searches the stream again for inverted X-syncs: each of the eight
words W is read as the top 16 bits of either -W or ~W (the ones'
complement), chosen per word, and an X-sync read so needs its magic and a
valid CRC like any other. After an inversion and a truncation, the top bits
of -W equal ~w where the bits below them were not zero, and -w where they
were, so neither transform of the whole word reads every X-sync (section 13
has an example). Anchors found there mean an inverted stream, which is then
compared against the inverted expected samples.

**Channel order.** *Informative: diagnosis; not needed to decide a verdict.* The channel index field of each received
channel's X-syncs gives the source channel it carries. The diagnosis compares
in the source channel order.

**Gaps.** *Informative: diagnosis; not needed to decide a verdict.* The distance between two anchors, compared with the
distance of their block starts, gives the net number of units dropped (fewer)
or inserted (more). At the first mismatch the reference checker:

1. tries the offset the anchors imply;
2. else accepts a run of mismatching units if the comparison matches again,
   at the same offset, for 16 units within the next 64: a run of zero units
   is **zero-filled** (`zero_filled`), anything else **altered**;
3. else tries offsets of 1, 2, ... S units in both directions, dropped
   before inserted, where S = 2 x ceil(units per second / 8000) + 1 (at
   least 8): two USB High-Speed packets plus one unit;
4. else, once per file until a span compares without a mismatch again,
   tries offsets S+1 ... L in both directions, nearest first, dropped before
   inserted, where L is one block or 4096 units, whichever is less, and
   between two anchors at least the net offset they imply, from the current
   offset, plus S;
5. else counts the rest of the span as altered.

An offset is accepted when the next 16 units match. Units missing at an
accepted offset are **dropped**. Inserted units are named **zero-inserted**
(`zero_inserted`, all zero), **silence-inserted** (`silence_inserted`, DSD
idle bytes 0x69 or 0x96), **repeated** (equal to the units just before them)
or **inserted**. A net offset between two anchors that the search does not
place is **unlocated**. An X-sync whose block index does not follow the one
before it in the same file (the same block twice) is a **discontinuity**.
The position of a gap inside a constant stretch (silence, a chip) is the
first position where it becomes visible.

**Masked comparison.** *Informative: diagnosis; not needed to decide a verdict.* If a PCM stream uses k bits, fewer than the
file depth D (for example a 24-bit file received in a 16-bit slot, or rounded
or dithered to 20 bits), the gap search compares the received and the
expected word with the bits below k cleared, as signed 32-bit integers, and
their difference is taken without wrap-around (0x80000000 and 0x7FFFF000
differ by almost 2^32, not by one step). A unit matches when on every channel
the difference is at most 2 steps of k bits, 2 x 2^(32-k), so gaps are still
found in truncated, rounded and dithered streams. k is the number of bits
the received words use; in an inverted stream it leaves out the word
2^31 - 2^(32-D), to which the inversion clamps -2^31 (as k' in 12.5). Only
the gap search is affected: the verdict, the counts of equal and altered
units, and the classification (12.5) compare whole words.

### 12.3 Exact verification of R-header blocks

The R-header words contain the total block count, which the X-sync does not
carry. The verdict compares R-header blocks unit for unit like any other
block; how it treats the chips that carry the count and the CRC while the
count is not yet known is the R-header rule of 12.6.

*Informative: diagnosis; not needed to decide a verdict.* The reference
checker's diagnosis decodes the total from an exactly received R-header
block as the extent's count is read (12.6, Extent of a file), and then
compares the whole block as usual. The robust decoder (12.4) gives the same
result on any stream.

### 12.4 Robust layer

*Informative: diagnosis; not needed to decide a verdict.* A conforming checker does not need the
robust layer.

The decoder runs on a real signal x per channel with full scale 1.0: the PCM
samples W / 2^31, or for DSD the density of each 16-bit group,
`(2 x ones - 16) / 16`, at the DSD rate / 16.

1. **Chip length.** In received samples the chip lasts
   `L = 11 x rate / 44100` (44.1 kHz family source) or
   `L = 12 x rate / 48000` (48 kHz family source), whatever the source rate,
   because the chip time is the same at every rate. Both are tried. If
   neither finds a header, the native chip lengths of every rate (11M, 12M)
   are tried, which finds content played at the wrong rate without
   resampling.
2. **Barker matched filter.** For each start t, with chip edges
   `e_j = round(j x L)`, j = 0 to 13:
   `corr(t) = sum_j barker_j x sum(x[t + e_j .. t + e_(j+1) - 1])` and
   `rho(t) = corr(t) / sqrt(e_13 x sum(x[t .. t + e_13 - 1]^2))`. Running sums
   make this 13 additions per sample. Peaks with |rho| > 0.7 are candidates;
   the sign of rho is the polarity.
3. **Decode.** For each of the 237 chips q, average x over the middle half of
   the chip, `t + (q + 1/4)L` to `t + (q + 3/4)L`, times the polarity. The
   first 13 averages must have the Barker signs. Each bit is 1 if its first
   chip's average exceeds its second's. The 112 bits give r0 to r6, which
   must pass the checks of section 6. If not, try t - 1 and t + 1.
   **Downmix:** a bit whose two chip averages differ by less than the mean
   Barker chip level is an erasure. If the only erasures are in the channel
   index (r1 bits 15 to 12) and in r6, the received channel is a mix of
   several source channels: every channel index whose CRC agrees with the
   received bits of r6 is one of them, and the header is accepted when there
   are at least two.
4. **Measurements.**
   - **Level:** the mean of chip average times chip value, over all 237
     chips. Its ratio to the nominal level (0.25 for a PCM source, 0.5 for a
     DSD source) gives the gain in dB. Changes below 0.1 dB are within the
     ripple of resampling filters and are not reported as gain.
   - **Polarity:** the sign of rho.
   - **Channel order:** the R-header's channel index against the received
     channel.
   - **Rate ratio:** for two R-headers of one channel with block indices k1
     < k2 at received positions t1 and t2,
     `ratio = (t2 - t1) / ((k2 - k1) x 4096 x M_source)` received samples
     per source unit. If `ratio x source rate` equals the received rate
     (within 0.2 %) the time base is preserved: the stream was resampled (or
     converted) when the rates differ. If the ratio is 1 while the rates
     differ, the content was played at the wrong rate.

Where the robust header shows the same rate and time base, the reference
checker aligns the samples on the R-header positions (block start = Barker
start - 512M) and classifies the difference (12.5). It judges only the blocks
from the first R-header outside the lead-in to the last one before the tail,
since the R-headers carry the flags of their own blocks only.

### 12.5 Classification

*Informative: diagnosis; not needed to decide a verdict.*

The reference checker names the change in this order; the first that holds
over all body samples is the reason, earlier findings (channel order,
polarity) are kept:

1. **Channel order** (X-sync or R-header channel field, or a least-squares
   mixing matrix): `CHANNEL_MAP` for swapped, reordered or duplicated
   channels. Equal shares of several source channels in every output (or a
   downmixed R-header): `DOWNMIX`.
2. **Polarity**: `POLARITY`.
3. **Gain**: least-squares gain g of received over expected samples, fitted
   on the frames whose expected samples are all below a quarter of full scale
   (so clipped bursts do not bias it); |20 log10 g| >= 0.005 dB gives `GAIN`
   with the value in dB and the number of clipped samples.
4. **32-bit float path** (32-bit files): received = expected rounded to a
   24-bit mantissa (round to nearest, ties to even, clamped): `FLOAT32`.
5. With k, the number of bits the received samples use, and the file depth
   D, in this order:
   - k < D and received = expected with the bits below k cleared:
     `TRUNCATED` to k;
   - k < D and received = expected rounded to k bits: `ROUNDED` to k;
   - `POLARITY` holds and the samples were truncated or rounded before the
     inversion (below): `TRUNCATED` or `ROUNDED` to k', before the polarity
     inversion;
   - k < D and |received - expected| <= 2 LSB of k bits for 99.9 % of
     samples: `DITHERED` to k, with the error RMS in LSB.
6. Fewer than 1 % of the frames differ: `BIT_ERRORS`.
7. |received - expected| <= 2 LSB of D bits for 99.9 % of samples: noise or
   dither below the file's depth (`LOW_BIT_NOISE`, k > D) or at its LSB
   (`LSB_NOISE`).
8. Otherwise `UNKNOWN`.

**Inversion.** Where `POLARITY` holds, the expected samples from step 3 on
are the inverted ones: a word W inverts to -W, and W = -2^31, which has no
positive counterpart, to the largest word of depth D, 2^31 - 2^(32-D). A sample quantised before the inversion is
told apart in step 5 as follows. Let e0 be the expected word before the
inversion, and k' the number of bits the received words use, counted without
the received words equal to 2^31 - 2^(32-D) (where an inversion at depth D
clamps). If 0 < k' < D, quantise e0 to k' bits (the bits below k' cleared,
or rounded as in step 5) and invert the result at depth D. The received
samples are `TRUNCATED` (then `ROUNDED`) to k' before the inversion when
every received word equals that inversion, or, where the quantised e0 is
-2^31, equals 2^31 - 2^(32-k') (the clamp of an inversion at k' bits).

The received rate against the header rate gives `RATE_MISMATCH` when they
differ without resampling. A DSD stream whose R-headers decode but whose bits
differ is `DSD_PROCESSED`.

Two findings of the robust layer (12.4) decide before all of the above:

- `DSD_TO_PCM`: a DSD signal (R-header kind 2) received as PCM. The
  report gives the source rate, the received rate and the level
  (`LEVEL`, in dB re 50 % modulation).
- `RESAMPLED`: the R-header rate differs from the received rate and the time
  base is preserved (for DSD at another DSD rate: "DSD re-modulated"). The
  report gives the file's rate and the received rate, and gain, polarity and
  channel order from the R-header.

Like every finding, they are reasons the diagnosis gives for a file that is
`NOT_BIT_PERFECT` (12.6), not verdicts of their own.

### 12.6 Verdicts

This section is normative, but for the paragraphs marked informative.

**What a verdict detects.** The verdict detects changes that players,
mixers, operating systems and transports make. It does not detect a stream
crafted to pass: the format is public and carries no secret, and a file of
the same identity with other section lengths looks like a cut copy.

**Files in a stream.** A host may join several files into one received
stream, for example for gapless playback. A checker splits the received
stream into files. A file's identity is that of its X-syncs. A file ends:

- at the end of a block flagged LAST;
- at an identity change: an X-sync whose kind, depth, rate, channel count or
  seed differs from the file's starts the next file;
- where the block index goes back: an X-sync of the same identity whose
  block index is lower than the one before it starts the next file;
- where the tail is left: an X-sync of the file after its first TAIL X-sync
  that is not flagged TAIL ends the file with `end` `BACK`, like a block
  index going back, and starts the next one.

A file that follows only ends the earlier file; if that happens inside the
body, (b) or (c) below fails. *Informative: this affects only the diagnosis.*
Where a file ends without its LAST block, the reference checker puts the
boundary at the start of the next file's block 0 as the next file's first
X-sync places it, but not before the end of the earlier file's last block
that has an X-sync.

**Results.** A checker gives one result per file and one per stretch of
unknown signal. Only `BIT_PERFECT` is a pass. A file that does not arrive at
all gives no result, so a reader compares the number of `BIT_PERFECT` results
with the files played.

**Extent of a file.** A file found by its X-syncs covers the received units
from the start of its block 0 to the end of its last block, at the positions
its X-syncs imply: block 0 starts k x B units before its first X-sync, which
is that of block k (B as in 5.1); its last block ends (count - k) x B units
after its last X-sync, that of block k. The count is the block index + 1 of
an intact X-sync flagged LAST, or else r5 of the first R-header of the file
that gives a count, read in a channel whose X-sync of that block is intact.
Each of its 237 chips is read at its centre unit: in PCM frame 512M + qL +
floor(L/2) of the block (7.6), +1 for a positive word, -1 for a negative
one; in DSD byte (512M + qL) / 8 + floor(L/16) (8.5), +1 for 0xEE, -1 for
0x11. Any other value, a Barker chip of the other sign or two chips of a bit
that are not opposite gives no count. The words, from each bit's first chip,
must pass section 6, with r0 to r4 equal to h0 to h4 of that channel's
X-sync. A later R-header does not change the count, and a count no greater
than the block index of the file's last X-sync gives no count. Without a
count, the extent ends at the end of the last block that has an X-sync. Each end extends
by one more block, for units dropped or inserted in the lead-in or tail. The
extent never reaches before the stream's start, after its end, or into the
next file (placed as in Files in a stream).

**Unknown signal.** Each longest run of received units outside every file's
extent gets one result with the verdict `UNKNOWN_SIGNAL` (kind `NONE`, end
`STOP`, no file fields, no `diff`), silence included: a test plays test files
only. This holds at the stream's start and end too: silence that a host
prepends or appends is not the file's data. Packets without audio carry no units. A buffer overflow of a hardware
checker while no file is open gives that stretch `DEVICE_OVERRUN` instead.

**Allowed silence.** A checker may offer the setting `allow_silence`: `false`
(the rule above) or `true`; it chooses its default and reports the setting
in effect. With `true`, silence outside every
file's extent gives no result, wherever it is (before, between or after
files), and so does a stream of silence only. It never applies inside a
file: silence that replaces part of a file's body is a difference like any
other and gives `NOT_BIT_PERFECT` under either setting. Anything that is not silence
still gives `UNKNOWN_SIGNAL`. Silence is: for PCM, words 0; for DoP, frames
with valid markers (0x05 or 0xFA, the same in every channel) and DSD bytes
0x69 or 0x96, with zero bits below the 24; for native DSD, bytes 0x69 or
0x96. A report states the setting in effect.

**Verdict.** Each file, and each stretch of unknown signal, gets one verdict,
the first of these that holds:

1. `UNKNOWN_SIGNAL`: the units belong to no file (see above). A file is found
   by an X-sync that passes section 6 (PCM: in the top 16 bits of the eight
   words; DoP and native DSD: in the 16 bytes). There is no search for
   negated or inverted X-syncs, so an inverted, rounded, dithered or
   resampled stream may give `UNKNOWN_SIGNAL`. A DoP file received without
   markers has no readable X-sync and gives it too (12.1).
2. `DEVICE_OVERRUN` (hardware checkers only): the checker's own receive buffer
   overflowed between the file's first received unit and its end, and no
   difference of the file (a unit that did not arrive as expected, as `diff`
   below) came before the overflow. The file is not judged; this is not a
   property of the stream: play it again. A file whose first difference came
   before the overflow is `NOT_BIT_PERFECT`, with that difference's `diff`,
   whatever the overflow lost afterwards. A software checker, which reads the
   whole stream, never reports `DEVICE_OVERRUN`.
3. `BIT_PERFECT` when all of (a) to (d) hold, else `NOT_BIT_PERFECT`:
   - **(a) Rate.** The received rate equals the file's rate (PCM: the frame
     rate; DoP: the DoP frame rate x 16; native DSD: the DSD rate), and the
     received channel count equals nch.
   - **(b) Limits.** The block before the first body block has an intact
     X-sync of the file flagged LEAD_IN, or the body starts at block 0; and
     the block after the last body block has an intact X-sync of the file
     flagged TAIL, or the last body block's X-sync is flagged LAST. The first
     body block is the first block whose X-sync is not flagged LEAD_IN;
     LEAD_IN X-syncs later in the file are compared as body units (c). An
     intact X-sync of the file passes section 6, with its reserved flag bits
     0, in at least one channel, with the file's identity and that block's
     index. If such a limit X-sync is broken in every channel or not
     received, the file is `NOT_BIT_PERFECT`: a rare false fail, chosen over
     a possible false pass.
   - **(c) Body.** The stream holds the whole body as one run of consecutive
     units, from the first body block's X-sync on. Every unit equals the
     expected unit (sections 7.5 and 8.6) in every channel, received channel
     c against source channel c: X-syncs (compared as generated: flags 0, or
     LAST on the last block, reserved flag bits 0), walking-bit bursts,
     payload and every R-header unit included, the chips of the R-header
     rule below as that rule says. No unit is left out of the comparison.
   - **(d) Words.** PCM: the whole word W. DoP: the DSD bits, and the marker
     0x05 or 0xFA alternating in every body frame, either phase first, the
     same in all channels, with zero bits below the 24 in wider slots.
     Native DSD: the bits. A file of a depth below the slot size, such as
     16-bit content in a 24-bit slot, carries the same words and can be
     bit-perfect.

**R-header rule.** Chips 173 to 236 of the chip sequence (section 7.6; r5, the
block count, and r6, the CRC) are checked in every channel in two steps.
*Chips:* each bit's first chip is one of its two values in every unit, the
same through the chip, and its second chip is the opposite value in every
unit. The bits are checked in order, each as it is read, and the first unit
that breaks this is a difference: in a bit's first chip, its first unit if
that is neither value, else the first unit that differs from it; in its
second chip, the first unit that is not the opposite of the first chip's
value. *Words:* only if chips 173 to 236 arrived whole in every channel, with
no difference in them (by the chip rule, and in DoP by (d) in their frames),
are the words they spell (each bit from its first chip) checked: r6 =
crc16(r0 to r5), with r0 to r4 equal to h0 to h4 of the block's own X-sync;
and r5 is the block index + 1 of the file's intact X-sync flagged LAST or,
where none arrived, the same in every channel and in every R-header block of
the body. If a word check fails in any channel, the difference is the first
unit of the block's chip 173 (its first r5 unit). Where no LAST X-sync
arrived, the block that fails is the first whose r5 differs from another
channel's or from an earlier R-header block's. `diff` is the earliest
difference. A failed word check comes before every later difference, even
when the count that shows it arrives only with the LAST X-sync, so `diff` is
the same whether or not the checker knows the count yet. All other chips are
compared as expected units.

**Lead-in and tail.** They are not compared, except that (b) needs the last
lead-in X-sync and the first tail X-sync: a stream that starts after the
former, or stops before the latter, gives `NOT_BIT_PERFECT`. A stream that
stops later, inside the tail or inside the LAST block, passes; `end` is
`LAST` once the X-sync of the LAST block has arrived.

**Consequences.** An inverted stream gives `UNKNOWN_SIGNAL`. Swapped or
duplicated channels, USB packets lost in the body, dropped, inserted or
repeated units, a start late in the body and a stop early in the body give
`NOT_BIT_PERFECT`. Units dropped or inserted in the lead-in or tail, with
both limit X-syncs intact, do not change the verdict.

**Report.** A checker reports, for each file:

- the verdict;
- `end`, how the file ended: `LAST` (its LAST block arrived), `NEXT`
  (another file followed: an identity change), `BACK` (the block index went
  back) or `STOP` (the stream stopped first);
- `diff`, the position in the file of the first body unit that did not
  arrive as expected, in seconds from the file's start as generated, 3
  decimals, rounded half up (PCM: frame index / rate; DSD: byte index
  x 8 / DSD rate). It is the earliest of: the body's first unit, when (a) fails or the first
  body block's X-sync or the LEAD_IN X-sync before it is missing (where the
  body's start is not known at all: the first unit of the first block whose
  X-sync arrived); the first body unit that differs or did not arrive (in
  chips 173 to 236, as the R-header rule places it); the
  first unit after the body, when (b) fails at the end. Where a limit X-sync
  is broken or cut off, the body runs from after the last LEAD_IN X-sync
  received to the first TAIL X-sync received, and `diff` is the first unit
  compared in it that differs or did not arrive. For `BIT_PERFECT`,
  `UNKNOWN_SIGNAL` and `DEVICE_OVERRUN` there is none;
- the transport (PCM, DoP, native DSD), the received rate, the slot size and
  the number of bits used in the body ("effective depth");
- the signal identity: kind, rate, depth, channels and seed.

#### Coverage and gap lists

*Informative: diagnosis; not needed to decide a verdict.* The reference
checker's diagnosis also reports the following. It keeps two names for
`NOT_BIT_PERFECT` files: `ALTERED`, when a received sample of the body changed
value, with the deciding finding of 12.5 as its reason (for example
`TRUNCATED`, `CHANNEL_MAP`, `RATE_MISMATCH`, `RESAMPLED` or `DSD_TO_PCM`), and
`INCOMPLETE`, when no sample arrived changed in value but the body did not
arrive whole and in order (frames of it are missing, or frames that are not in
it were inserted or repeated).

The body is **missing** at the:

- **start** when the first body block was not received from its start;
- **middle** when any count of the gap list below is not zero. Zero-filled
  units (file units replaced by zeros at the same offset) count here, not as
  altered;
- **end** when its last body block did not arrive in full.

Only the body counts (5.2): a file whose stream starts in the lead-in or
stops in the tail, with the whole body received, misses nothing, and its
`end` may be `STOP`. Where no tail block and no LAST block arrived, the
diagnosis cannot know that the body's last block arrived, and reports the end
missing.

The diagnosis reports, for each file:

- the lock state: exact (X-syncs), robust (R-headers only) or none;
- the block counts of the lead-in, the body and the file (`lead`, `body`,
  `total`; unknown when no block that shows them arrived);
- the reason, and every finding that holds (12.5);
- `missing`: any of `start`, `middle` and `end`, or `none`;
- `body_blocks`: the body blocks that arrived, in full or in part;
- `units`: the body units compared; `equal`: of those, the units exactly
  equal; `altered`: the units that differ in value, zero-filled units not
  included; `uncompared`: body units that arrived but could not be compared;
- the gap list, the counts by kind in the body: `dropped`, `inserted`,
  `repeated`, `zero_inserted`, `silence_inserted`, `zero_filled` and
  `unlocated` in units, and `discontinuity` in events (12.2);
- events in the lead-in and tail (`side_events`), as notes that do not count.

Units are frames for PCM, and DSD bytes per channel (8 DSD bits) for native
DSD and DoP, where one DoP frame carries 2 units.

## 13. Test vectors

[`vectors/bpc1-v1-vectors.json`](../vectors/bpc1-v1-vectors.json) holds, for
PCM 44.1 kHz 16-bit, 48 kHz 24-bit, 96 kHz 32-bit, 768 kHz 24-bit, the code
sweeps of 44.1 kHz 16-bit and 48 kHz 24-bit, DSD64 DSF, DSD128 DFF and
DSD256 DoP-WAV: the block geometry, channel keys,
X-sync and R-header words of chosen blocks, the first 45 chips, exact
samples (PCM) or bytes (DSD) at chosen positions, and the first bytes of the
container's audio data (for a code sweep also its sweep keys, and the first
sweep units of block 30); under `primitives`, the `lbD` and `sweep_channel_key`
entries; and, under `checking`, units of the gap search's
masked comparison and an inverted X-sync (12.2; diagnosis only, not needed for
a verdict), and, under `verdicts`, the verdict cases below as exact
constructions (file, position, change, verdict, `diff`). Each
configuration gives its name and the file name of the published set
(11.2), with its number; the 96 kHz 32-bit one, not published, takes the
number its depth base gives (00063).
`python3 -m generator.vectors --check` regenerates and compares it.
Selected values:

| Primitive | Input | Result |
|---|---|---|
| lb32 | key 0x00000000, n 0x00000001 | 0x688990C0 |
| lb32 | key 0x12345678, n 0x00000000 | 0xF5E71C96 |
| lb32 | key 0xDEADBEEF, n 0xFFFFFFFF | 0x1D9C5A71 |
| splitmix64_first | 0x0000000000000000 | 0xE220A8397B1DCDAF |
| crc16 | ASCII `123456789` | 0x29B1 |
| key | seed 0x42504331, PCM, 44.1 kHz, 16-bit, channel 0 / 1 | 0xDAA9D30E / 0xDB3F79C2 |
| key | seed 0x42504331, PCM, 48 kHz, 24-bit, channel 0 / 1 | 0xE5AD7F77 / 0x2F4B035B |
| lbD | D 16, key 0x8788, s 0x0000 / 0x3039 | 0x884E / 0x603E |
| lbD | D 24, key 0xE5BFCF, s 0x000000 / 0x12D687 | 0x315360 / 0x7F80ED |
| key, sweep key | seed 0x42504331, code sweep (kind 5), 44.1 kHz, 16-bit, channel 0 / 1 | 0xC9898788 / 0x4C4A48A7, sweep key 0x8788 / 0x48A7 |
| key, sweep key | seed 0x42504331, code sweep (kind 5), 48 kHz, 24-bit, channel 0 / 1 | 0x7F867DC7 / 0xD8EDA554, sweep key 0x867DC7 / 0xEDA554 |

PCM 48 kHz 24-bit, block 0 (lead-in, flags 0x01):

| | Words |
|---|---|
| X-sync, channel 0 | B1C0 5EED 1131 0201 4250 4331 0000 A7BC |
| X-sync, channel 1 | B1C0 5EED 1131 1201 4250 4331 0000 9A08 |
| Samples, frames 0 to 3 (L, R) | (-5128180, -5127986), (6221092, 6221194), (1126868, 1126767), (131409, 1179937) |
| Samples, frames 8 to 11 (walk) | (1, -5592406), (2, 5592405), (4, -5592406), (8, 5592405) |
| R-header of block 8, channel 0 | 1131 0201 4250 4331 0008 0094 5949 |

Code sweep, block 30, first sweep units (frames 72 to 75 of the block, L and
R samples):

| File | s of frame 72 | Samples |
|---|---|---|
| PCM 44.1 kHz 16-bit | 47 136 | (-15029, 19905), (3766, -22208), (-3602, -11174), (-4404, -11096) |
| PCM 48 kHz 24-bit | 112 672 | (4359456, -4212737), (2453841, -5666167), (8192613, -7204575), (7865913, -408350) |

DSD256 DoP-WAV, first DoP frames (both channels): 0x05B1C0, 0xFA5EED,
0x05121C (h0 0x121C: kind 2, depth code 1, rate code 12), then 0xFA0201 (channel 0) and 0xFA1201 (channel 1).

Masked comparison of the gap search (12.2; informative), one unit, words as
unsigned hexadecimal:

| k | Received | Expected | Match |
|---|---|---|---|
| 16 | 0x12340000 | 0x1234ABCD | yes (truncated) |
| 16 | 0x12350000 | 0x1234ABCD | yes (rounded up) |
| 16 | 0x12370000 | 0x12340000 | no (3 steps) |
| 20 | 0xFFFFF000 | 0x00001000 | yes (2 steps, across zero) |
| 20 | 0x80000000 | 0x7FFFF000 | no (no wrap-around) |
| 16 | 0x12340000, 0x56780000 | 0x12340000, 0x567B0000 | no (one channel 3 steps off) |

Inverted X-sync (12.2; informative, a conforming checker gives `UNKNOWN_SIGNAL`
for such a stream): PCM 48 kHz 24-bit, block 41, channel 0, inverted at
24 bits and truncated to 16 bits. The words W 0xB1C09800 0x5EED3300 ...
0x00299900 0xAA960000 arrive as 0x4E3F0000 0xA1120000 ... 0xFFD60000
0x556A0000. Words 0 to 6 read as ~W (0xB1C0 0x5EED ... 0x0029), word 7 as
-W (0xAA96, the CRC): neither -W nor ~W alone gives a valid X-sync.

Verdict cases (12.6). The vectors file lists them exactly under `verdicts`:
each case is a stream built from short BPC1 files (a lead-in, a body and a
tail of a few blocks), the changes made to it, the `allow_silence` setting
and the results (verdict and `diff`). Besides these, each regular file of the
published set played unchanged gives `BIT_PERFECT`, each negative file
(11.5, 11.6) gives `NOT_BIT_PERFECT`, each gapless pair (11.7) joined gives
`BIT_PERFECT`, the negative gapless pair joined gives one `NOT_BIT_PERFECT`
with `diff` 7.500, and each part alone gives `NOT_BIT_PERFECT`.

- `BIT_PERFECT`:
  - the file unchanged, PCM, DoP and code sweep; 16-bit content in a 24-bit
    slot; a DoP file with the other marker phase;
  - a regular 16-bit file with every body sample of code 32766 in channel 0
    changed to 32767: it does not carry that code;
  - the stream starts at the last lead-in block, or stops right after the
    first tail X-sync;
  - the lead-in and tail zeroed outside their limit X-syncs; the first
    lead-in block and the last tail block halved (a fade);
  - 300 frames removed in the lead-in before the first intact X-sync, 700
    frames repeated in the tail after the LAST X-sync, zeros shorter than a
    block before and after a file (the margin of the extent);
  - two gapless files, the same file twice, a PCM file and a DoP file at the
    same frame rate: each with its own result;
  - a splice of two lengths of one file, joined after the first file's first
    TAIL X-sync: the first file is `BIT_PERFECT` with `end` `BACK`, and the
    next one `NOT_BIT_PERFECT` (`diff` 2.304 in the vectors);
  - a file with the 24-block lead-in of the published 48 kHz files, so that
    its first body block, 24, is an R-header block, unchanged or stopping
    right after the first tail X-sync;
  - a file whose LAST X-sync is broken and whose only R-header, in the
    lead-in, spells another block count with a valid CRC: the extent ends at
    that count;
  - a reserved flag bit set, with a valid CRC, in one channel's last lead-in
    X-sync: the other channel's X-sync shows the body's start;
  - a file whose LAST X-sync and tail X-syncs after the first are erased:
    the count of its lead-in R-header gives the extent.
- `NOT_BIT_PERFECT`:
  - a changed payload bit, the last body frame, a body X-sync's flags word
    broken in both channels, a bit below the X-sync word in one channel;
  - frames removed, zero frames inserted or frames repeated in the body;
  - the stream starts in the body, stops in the body, or stops at the body's
    end, before the first tail X-sync;
  - the samples received at another rate than the file's (12.6 a), or with
    the channels exchanged;
  - the last lead-in X-sync, or the first tail X-sync, broken in both
    channels (12.6 b), or with a reserved flag bit set in both channels;
    channel 1's first body X-sync replaced by its last lead-in X-sync, or its
    last body X-sync replaced by its first tail X-sync; a reserved flag bit
    set in one channel's body X-sync;
  - a file with a 2-block lead-in followed by the same file with a 12-block
    lead-in, from block 6, with or without a changed payload bit of block 3:
    LEAD_IN X-syncs after the first body X-sync are body units;
  - an r5 chip unit changed, with the count known, in a stream that starts
    after block 8, or in one that stops 50 chips later or in the tail before
    LAST, or in the first body block, 24, after a 24-block lead-in;
  - the words of an R-header block's chips 173 to 236, all valid pairs: an r6
    bit's two chips inverted in one channel (the CRC fails), in the whole
    stream, in one that stops in the tail before LAST, and in one that stops
    right after chip 236; block 8 or block 24 spelling another count with a
    valid CRC, with and without the LAST block; a failed CRC in block 8 and
    another count in block 24: `diff` is chip 173 of the block that fails
    (block 24 where block 8 spells another count and no LAST block
    arrived);
  - another count in block 8 and a changed payload bit in block 10: `diff`
    is block 8's chip 173 when the LAST block arrives, the payload bit when
    it does not;
  - a chip difference in chips 173 to 236 together with a valid pair of the
    other value, in the same channel or the other, before or after it,
    another count in the block included, and in DoP a marker zeroed or a
    padding bit set in chip 193: the words are not judged, and `diff` is
    the chip difference;
  - a DoP file cut in its body and followed by a PCM file at the same frame
    rate from its block 1; and one cut 7 frames into a body block's X-sync,
    followed by that PCM file or by a DoP file from its block 1: `diff` is the
    first unit after the cut, the X-sync frames that arrived included (1.951
    in the vectors), and the next file is `BIT_PERFECT`;
  - a code-sweep 16-bit file with every body sample of code 32766 in channel
    0 changed to 32767 (as the negative sweep files, 11.6);
  - DoP: a DSD bit changed, a padding bit set, a marker zeroed, two markers
    exchanged, a phase jump, a marker that differs between channels;
  - a change whose `diff` is half-way between two milliseconds: `diff` rounds
    up;
  - a file followed by the same file rounded to 16 bits: `BIT_PERFECT`, then
    `NOT_BIT_PERFECT`.
- `UNKNOWN_SIGNAL`:
  - an inverted file, a file muted to zeros, a stream of zeros, a DoP file
    without markers; a DoP file whose X-syncs all carry the reserved kind 3,
    or 4, with valid CRCs, also with `allow_silence` `true`;
  - zeros (3 blocks), DoP idle or a track that is not a test file between
    two files: two results `BIT_PERFECT` and one `UNKNOWN_SIGNAL`;
  - zeros before, between and after two files, or DoP idle before and after a
    DoP file: an `UNKNOWN_SIGNAL` result for each stretch;
  - the zeros after a file whose LAST X-sync is broken and whose lead-in
    R-header spells another block count and has a reserved flag bit set:
    that R-header gives no count, and the file is `BIT_PERFECT`;
  - the rest of the tail of a file whose LAST X-sync and tail X-syncs after
    the first are erased, and its lead-in R-header's first Barker chip: that
    R-header gives no count, and the extent ends one block after the first
    tail block, after a `BIT_PERFECT` file;
  - a file rounded to 16 bits with the stream starting at block 9 (a file
    rounded to 16 bits gives `UNKNOWN_SIGNAL`, or `NOT_BIT_PERFECT` when
    X-syncs survive by chance: a fail either way), and a file followed by
    one rounded to 16 bits with the stream stopping at its block 8.
- With `allow_silence` `true`: zeros or DoP idle before, between and after
  two files give two `BIT_PERFECT` results; a stream of zeros and a file muted
  to zeros give no result; a track that is not a test file, a near-silent
  word in the zeros and a padding bit set in DoP idle still give
  `UNKNOWN_SIGNAL`.
The vectors have no `DEVICE_OVERRUN` case: only a hardware checker reports
it.

## 14. Design notes

- **Block size** scales with the rate so a block lasts the same time at every
  rate (85 to 93 ms): resynchronisation after any disturbance takes at most
  one block, and the per-block work of a checker is fixed in time.
- **Counter-based hash.** lowbias32 lets a checker jump to any frame, which
  a diagnosis tool's gap search needs, at two multiplications per sample. A 64-bit
  generator costs about twice as much on a 32-bit microcontroller.
- **Fields in the top 16 bits** keep the X-sync intact in 16-bit files and
  through truncation to 16 bits.
- **Low-level payload.** At -48 dBFS (-36 dBFS for 16-bit) the payload still
  exercises every bit of the file depth, gain and dither remain measurable
  by a diagnosis tool,
  and the files are less dangerous when played by mistake. The top bits are
  exercised by the X-sync and the walking-bit burst. A float32 path carries
  the 32-bit payload unchanged, because its values fit a 24-bit mantissa;
  the X-sync and the walking-bit burst do not, so the change is still found
  in every block.
- **Robust header.** 0.25 ms chips put the energy near 2 kHz, far below any
  resampler's cut-off, even 768 kHz to 44.1 kHz. Zero guards around the chip
  span keep the bursts and the payload out of the matched filter. Fixed 1110
  and 0001 chip patterns in DSD keep the header exactly reproducible by a
  checker.
- **Paired DSD payload bits** (b, not b) cancel each other at audio
  frequencies, so the DSD payload is about as quiet as the PCM one
  while still testing every bit.
- **Body length.** A change that affects every sample (gain, resampling,
  truncation, dither, EQ, polarity, channel order, a float path) shows within
  the first samples, so any body length finds it. The walking-bit bursts set
  and clear every bit position at full scale in every block, so a stuck or
  flipped bit shows too. Length matters only for intermittent faults (lost
  packets, underruns) at some rate of lambda per second: a clean body of T
  seconds bounds lambda below 3 / T with 95 % confidence (the rule of
  three). One 10 s body bounds it below 0.3 per second. Played continuously,
  the bodies add up: an hour of playing is about 48 minutes of body, which
  bounds it below one per 16 minutes. Rare faults are therefore found by
  playing files continuously, for hours if needed, or by looping one file
  gaplessly, not by longer files. A 10 s body keeps a file short enough to
  play the regular set (the 29 audio files of the WAV archive) in 6
  minutes.
- **Code sweep.** The regular set already catches the value faults it models
  (gain, truncation, rounding, dither, polarity, a float path, bit errors),
  because every block carries the walking-bit bursts, which hold the
  full-scale codes at both ends and every single-bit pattern. It does not carry every
  code, so a fault that changes one code only (for example 2^(D-1) - 2 to
  2^(D-1) - 1) passes it. The code sweep fills the body with every code of
  the depth exactly once per 2^D units, in a scrambled order, so a
  bit-perfect result proves that every code arrived unchanged. A bijection on
  D bits (4.4) generates it without a table or a state, so a checker still
  computes any unit directly. The cost is a long full-scale file (up to 416 s
  at 44.1 kHz and 24 bits), which is why the sweeps are a separate set.

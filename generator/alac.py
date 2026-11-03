"""Apple's ALAC reference codec through a small helper.

The codec is Apple's open-source ALAC encoder and decoder (macosforge/alac,
Apache-2.0), a git submodule at generator/third_party/alac pinned at
CODEC_COMMIT. The helper, alac_tool.cpp beside this file, is built from it
with the host C++ compiler on first use, into a cache directory keyed by the
sources, the compiler and the flags. The generator encodes with it and the
checker decodes with it: ffmpeg's ALAC encoder and decoder code some packets
differently from Apple's (a Rice-history clamp), so neither uses ffmpeg.
"""

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile

CODEC_COMMIT = "c38887c5c5e64a4b31108733bd79ca9b2496d987"
HERE = os.path.dirname(os.path.abspath(__file__))
CODEC = os.path.join(HERE, "third_party", "alac", "codec")
TOOL_SOURCE = os.path.join(HERE, "alac_tool.cpp")
CODEC_SOURCES = ("EndianPortable.c", "ALACBitUtilities.c", "ALACDecoder.cpp", "ALACEncoder.cpp",
                 "ag_dec.c", "ag_enc.c", "dp_dec.c", "dp_enc.c", "matrix_dec.c", "matrix_enc.c")
FRAMES_PER_PACKET = 4096
# EndianPortable.c detects the byte order on x86 only; state it for every host.
FLAGS = ["-std=c++11", "-O2", "-DTARGET_RT_LITTLE_ENDIAN=1"]

_built = {}


def compiler():
    """The host C++ compiler: $CXX, else c++, g++ or clang++ on the PATH."""
    cxx = os.environ.get("CXX")
    if cxx:
        return shutil.which(cxx)
    for name in ("c++", "g++", "clang++"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def missing():
    """Why the codec cannot be used here, or None."""
    if sys.byteorder != "little":
        return "the ALAC helper is built for little-endian hosts only"
    if not all(os.path.isfile(os.path.join(CODEC, s)) for s in CODEC_SOURCES):
        return ("Apple's ALAC codec sources are missing; run "
                "`git submodule update --init generator/third_party/alac`")
    if not compiler():
        return "a C++ compiler (c++, g++ or clang++, or $CXX) is needed to build Apple's ALAC codec"
    return None


def usable():
    return missing() is None


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "bit-perfect-checker")


def tool():
    """The helper's path, built on first use."""
    why = missing()
    if why:
        raise RuntimeError(why)
    cxx = compiler()
    if cxx in _built:
        return _built[cxx]
    version = subprocess.run([cxx, "--version"], capture_output=True, text=True).stdout
    h = hashlib.sha256("\0".join([cxx, version] + FLAGS).encode())
    for path in [TOOL_SOURCE] + [os.path.join(CODEC, s) for s in CODEC_SOURCES]:
        with open(path, "rb") as f:
            h.update(f.read())
    for name in sorted(os.listdir(CODEC)):              # the headers
        if name.endswith(".h"):
            with open(os.path.join(CODEC, name), "rb") as f:
                h.update(name.encode() + f.read())
    exe = os.path.join(cache_dir(), "alac-" + h.hexdigest()[:16], "alac_tool")
    if not os.path.isfile(exe):
        os.makedirs(os.path.dirname(exe), exist_ok=True)
        tmp = f"{exe}.{os.getpid()}.tmp"
        cmd = ([cxx] + FLAGS + ["-I", CODEC, "-o", tmp, "-x", "c++", TOOL_SOURCE]
               + [os.path.join(CODEC, s) for s in CODEC_SOURCES])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"building the ALAC helper failed:\n{r.stderr}")
        os.replace(tmp, exe)                            # atomic: parallel builds race safely
    _built[cxx] = exe
    return exe


def _run(args, stdin=None):
    r = subprocess.run([tool()] + [str(a) for a in args], input=stdin, capture_output=True,
                       text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or f"alac_tool failed ({r.returncode})")
    return r.stdout.split("\n")


def encode(src, offset, nbytes, rate, nch, bits, out):
    """Encode nbytes of interleaved little-endian PCM at offset of the file src
    (16 bits, or 24 bits in 3 bytes) with Apple's encoder into packets of 4096
    frames, written back to back to the file out. Returns (cookie, sizes)."""
    lines = _run(["encode", rate, nch, bits, src, offset, nbytes, out])
    if not lines[0].startswith("cookie "):
        raise RuntimeError("alac_tool: no cookie")
    return bytes.fromhex(lines[0][7:]), [int(s) for s in lines[1:] if s]


def decode(src, packets, cookie, out):
    """Decode the packets [(offset, size)] of the file src with Apple's decoder
    and the magic cookie into interleaved little-endian PCM in the file out (16
    bits in 2 bytes, 20 and 24 bits in 3, 32 in 4). Returns the frame count."""
    listing = "".join(f"{o} {s}\n" for o, s in packets)
    lines = _run(["decode", cookie.hex(), src, out], stdin=listing)
    return int(lines[0].split()[1])


def encode_pcm(pcm, rate, nch, bits):
    """Encode PCM bytes in memory: (cookie, [packet bytes])."""
    with tempfile.TemporaryDirectory(prefix="bpc1-alac-") as d:
        src, out = os.path.join(d, "in.pcm"), os.path.join(d, "out.alac")
        with open(src, "wb") as f:
            f.write(pcm)
        cookie, sizes = encode(src, 0, len(pcm), rate, nch, bits, out)
        with open(out, "rb") as f:
            data = f.read()
    packets, pos = [], 0
    for s in sizes:
        packets.append(data[pos:pos + s])
        pos += s
    return cookie, packets


def decode_packets(packets, cookie):
    """Decode packets in memory: the PCM bytes."""
    with tempfile.TemporaryDirectory(prefix="bpc1-alac-") as d:
        src, out = os.path.join(d, "in.alac"), os.path.join(d, "out.pcm")
        offsets, pos = [], 0
        with open(src, "wb") as f:
            for p in packets:
                f.write(p)
                offsets.append((pos, len(p)))
                pos += len(p)
        decode(src, offsets, cookie, out)
        with open(out, "rb") as f:
            return f.read()


COOKIE_FIELDS = ("frame_length", "compatible_version", "bit_depth", "pb", "mb", "kb",
                 "num_channels", "max_run", "max_frame_bytes", "avg_bit_rate", "sample_rate")


def parse_cookie(cookie):
    """The ALACSpecificConfig of a magic cookie (big-endian, 24 bytes), after
    the optional 'frma' and 'alac' atoms that Apple's decoder skips too."""
    if cookie[4:8] == b"frma":
        cookie = cookie[12:]
    if cookie[4:8] == b"alac":
        cookie = cookie[12:]
    if len(cookie) < 24:
        raise ValueError("ALAC magic cookie too short")
    return dict(zip(COOKIE_FIELDS, struct.unpack(">IBBBBBBHIII", cookie[:24])))

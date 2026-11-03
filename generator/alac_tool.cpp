/*
 * alac_tool: Apple's ALAC reference codec (third_party/alac/codec, the
 * macosforge/alac sources) as a small command-line helper for the generator
 * and the checker. Built by generator/alac.py with the host C++ compiler.
 *
 *   alac_tool encode RATE CHANNELS BITS IN OFFSET BYTES OUT
 *     Encodes BYTES of interleaved little-endian PCM (16 bits, or 24 bits in
 *     3 bytes) read at OFFSET of IN into ALAC packets of 4096 frames, the
 *     last one partial, and writes them back to back to OUT. Prints the
 *     magic cookie (ALACSpecificConfig) as "cookie <hex>", then one line per
 *     packet with its size in bytes.
 *
 *   alac_tool decode COOKIE IN OUT
 *     Reads lines "<offset> <size>" from stdin, one per packet of IN, decodes
 *     each with Apple's decoder and the cookie (hex), and writes the PCM,
 *     interleaved little-endian (16 bits in 2 bytes, 20 and 24 bits in 3, 32
 *     in 4), to OUT. Prints "frames <n>".
 *
 * Errors go to stderr with exit status 1. One or two channels only. The
 * encoder prints notes (an escape packet: "compressed frame too big") to
 * stdout; they go to stderr, so that stdout holds the results only.
 */

#include <cerrno>
#include <cstddef>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include <unistd.h>

#include "ALACAudioTypes.h"
#include "ALACBitUtilities.h"
#include "ALACDecoder.h"
#include "ALACEncoder.h"

/* EndianPortable.c detects the byte order on x86 only; elsewhere (ARM) its
   byte swaps are wrong unless the build states the order. The build passes
   -DTARGET_RT_LITTLE_ENDIAN=1, and main() checks it against the host. */
#if !TARGET_RT_LITTLE_ENDIAN || TARGET_RT_BIG_ENDIAN
#error "build with -DTARGET_RT_LITTLE_ENDIAN=1 on a little-endian host"
#endif

static const uint32_t kFrames = kALACDefaultFramesPerPacket;    /* 4096, as afconvert */
static const uint32_t kMaxFrameLength = 16384;                   /* ReadMe.txt: no more */
static FILE *results;                                            /* the real stdout */

static int fail(const char *what)
{
    fprintf(stderr, "alac_tool: %s\n", what);
    return 1;
}

static bool parse_u64(const char *s, uint64_t *out)
{
    char *end;
    errno = 0;
    unsigned long long v = strtoull(s, &end, 10);
    if (errno || end == s || *end || *s == '-')
        return false;
    *out = v;
    return true;
}

static void put_be32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static int encode(int argc, char **argv)
{
    uint64_t rate, nch, bits, offset, nbytes;
    if (argc != 9 || !parse_u64(argv[2], &rate) || !parse_u64(argv[3], &nch) ||
        !parse_u64(argv[4], &bits) || !parse_u64(argv[6], &offset) || !parse_u64(argv[7], &nbytes))
        return fail("usage: encode RATE CHANNELS BITS IN OFFSET BYTES OUT");
    if (rate < 1 || rate > 0xFFFFFFFFu)
        return fail("rate out of range");
    if (nch < 1 || nch > 2)
        return fail("one or two channels only");
    if (bits != 16 && bits != 24)
        return fail("16 or 24 bits only");
    const uint32_t frame_bytes = (uint32_t)(nch * bits / 8);
    if (nbytes % frame_bytes)
        return fail("input is not a whole number of frames");

    AudioFormatDescription in = {};
    in.mSampleRate = (alac_float64_t)rate;
    in.mFormatID = kALACFormatLinearPCM;
    in.mFormatFlags = kALACFormatFlagIsSignedInteger | kALACFormatFlagIsPacked;  /* native: LE */
    in.mBytesPerPacket = in.mBytesPerFrame = frame_bytes;
    in.mFramesPerPacket = 1;
    in.mChannelsPerFrame = (uint32_t)nch;
    in.mBitsPerChannel = (uint32_t)bits;

    AudioFormatDescription out = {};
    out.mSampleRate = (alac_float64_t)rate;
    out.mFormatID = kALACFormatAppleLossless;
    out.mFormatFlags = bits == 16 ? 1 : 3;       /* the encoder's 16- and 24-bit source codes */
    out.mFramesPerPacket = kFrames;
    out.mChannelsPerFrame = (uint32_t)nch;

    ALACEncoder enc;
    enc.SetFrameSize(kFrames);
    if (enc.InitializeEncoder(out))
        return fail("InitializeEncoder failed");

    FILE *fi = fopen(argv[5], "rb");
    if (!fi)
        return fail("cannot open the input");
    if (fseeko(fi, (off_t)offset, SEEK_SET))
        return fail("cannot seek in the input");
    FILE *fo = fopen(argv[8], "wb");
    if (!fo)
        return fail("cannot create the output");

    const uint32_t packet_bytes = kFrames * frame_bytes;
    /* The encoder may write up to mMaxOutputBytes = frames * channels * 5 + 1. */
    std::vector<uint8_t> rd(packet_bytes), wr(kFrames * nch * 5 + 1 + kALACMaxEscapeHeaderBytes);
    std::vector<uint32_t> sizes;
    uint64_t remaining = nbytes, total = 0, frames = 0;
    uint32_t largest = 0;
    while (remaining) {
        uint32_t n = remaining < packet_bytes ? (uint32_t)remaining : packet_bytes;
        if (fread(rd.data(), 1, n, fi) != n)
            return fail("short read from the input");
        int32_t size = (int32_t)n;
        if (enc.Encode(in, in, rd.data(), wr.data(), &size) || size <= 0)
            return fail("Encode failed");
        if (fwrite(wr.data(), 1, (size_t)size, fo) != (size_t)size)
            return fail("write failed");
        sizes.push_back((uint32_t)size);
        total += (uint32_t)size;
        largest = (uint32_t)size > largest ? (uint32_t)size : largest;
        frames += n / frame_bytes;
        remaining -= n;
    }
    enc.Finish();
    fclose(fi);
    if (fclose(fo))
        return fail("write failed");

    uint8_t cookie[64] = {0};
    uint32_t cookie_size = enc.GetMagicCookieSize((uint32_t)nch);
    if (cookie_size > sizeof cookie)
        return fail("cookie too large");
    enc.GetMagicCookie(cookie, &cookie_size);
    if (cookie_size < sizeof(ALACSpecificConfig))
        return fail("GetMagicCookie failed");
    /* The encoder fills maxFrameBytes as it goes but leaves avgBitRate 0 (its
       Finish() has the computation commented out). Fill both as afconvert
       does: the largest packet, and the bits of all packets over the
       duration. */
    put_be32(cookie + offsetof(ALACSpecificConfig, maxFrameBytes), largest);
    uint64_t avg = frames ? total * 8 * rate / frames : 0;
    put_be32(cookie + offsetof(ALACSpecificConfig, avgBitRate),
             avg > 0xFFFFFFFFu ? 0xFFFFFFFFu : (uint32_t)avg);

    fprintf(results, "cookie ");
    for (uint32_t i = 0; i < cookie_size; i++)
        fprintf(results, "%02x", cookie[i]);
    fprintf(results, "\n");
    for (uint32_t s : sizes)
        fprintf(results, "%" PRIu32 "\n", s);
    return fflush(results) ? 1 : 0;
}

static bool parse_hex(const char *s, std::vector<uint8_t> *out)
{
    size_t n = strlen(s);
    if (n % 2)
        return false;
    for (size_t i = 0; i < n; i += 2) {
        unsigned v;
        if (sscanf(s + i, "%2x", &v) != 1)
            return false;
        out->push_back((uint8_t)v);
    }
    return true;
}

/* Apple's decoder trusts the packet: a partial-frame count above the cookie's
   frame length, or an extra element, overruns its buffers. With one or two
   channels it stops after the first audio element, so check that one: its
   tag must be the element of the channel count (SCE for one, CPE for two) and
   its count must fit the frame length. */
static const char *check_packet(const uint8_t *p, uint32_t size, uint32_t nch, uint32_t frame_length)
{
    if (size < 3)
        return "packet too short";
    uint32_t tag = p[0] >> 5;
    if (tag != (nch == 2 ? (uint32_t)ID_CPE : (uint32_t)ID_SCE))
        return "packet does not start with the element for the channel count";
    if (p[2] & 0x10) {              /* partial frame: a 32-bit count after bit 23 */
        if (size < 7)
            return "packet too short";
        uint64_t w = 0;
        for (int i = 0; i < 7; i++)
            w = (w << 8) | p[i];
        uint32_t count = (uint32_t)(w >> (56 - 23 - 32));
        if (count > frame_length)
            return "partial-frame count exceeds the cookie's frames per packet";
    }
    return nullptr;
}

static int decode(int argc, char **argv)
{
    std::vector<uint8_t> cookie;
    if (argc != 5 || !parse_hex(argv[2], &cookie))
        return fail("usage: decode COOKIE IN OUT");
    if (cookie.size() < sizeof(ALACSpecificConfig) + 24)
        cookie.resize(sizeof(ALACSpecificConfig) + 24, 0);   /* Init peeks at 8 bytes */
    ALACDecoder dec;
    if (dec.Init(cookie.data(), (uint32_t)strlen(argv[2]) / 2))
        return fail("bad cookie");
    const ALACSpecificConfig &c = dec.mConfig;
    uint32_t width;
    switch (c.bitDepth) {
    case 16: width = 2; break;
    case 20: case 24: width = 3; break;
    case 32: width = 4; break;
    default: return fail("bad cookie: bit depth");
    }
    if (c.numChannels < 1 || c.numChannels > 2)
        return fail("bad cookie: one or two channels only");
    if (c.frameLength < 1 || c.frameLength > kMaxFrameLength)
        return fail("bad cookie: frames per packet");

    FILE *fi = fopen(argv[3], "rb");
    if (!fi)
        return fail("cannot open the input");
    FILE *fo = fopen(argv[4], "wb");
    if (!fo)
        return fail("cannot create the output");

    /* A packet is at most the escape (uncompressed) form plus headers. The
       read buffer has as much again of zeros behind it, so that the decoder's
       reads stay inside it even on a damaged packet. */
    const uint32_t limit = c.frameLength * c.numChannels * 4 + 64;
    std::vector<uint8_t> rd(2 * limit), wr((size_t)c.frameLength * c.numChannels * 4);
    uint64_t frames = 0;
    unsigned long long offset;
    unsigned long size;
    int got;
    while ((got = scanf("%llu %lu", &offset, &size)) == 2) {
        if (size > limit)
            return fail("packet larger than the cookie's format allows");
        memset(rd.data(), 0, rd.size());
        if (fseeko(fi, (off_t)offset, SEEK_SET) || fread(rd.data(), 1, size, fi) != size)
            return fail("short read from the input");
        const char *bad = check_packet(rd.data(), (uint32_t)size, c.numChannels, c.frameLength);
        if (bad)
            return fail(bad);
        BitBuffer bb;
        BitBufferInit(&bb, rd.data(), (uint32_t)size);
        uint32_t n = 0;
        if (dec.Decode(&bb, wr.data(), c.frameLength, c.numChannels, &n) || n > c.frameLength)
            return fail("Decode failed");
        size_t out = (size_t)n * c.numChannels * width;
        if (fwrite(wr.data(), 1, out, fo) != out)
            return fail("write failed");
        frames += n;
    }
    if (got != EOF)
        return fail("bad packet list on stdin");
    fclose(fi);
    if (fclose(fo))
        return fail("write failed");
    fprintf(results, "frames %" PRIu64 "\n", frames);
    return fflush(results) ? 1 : 0;
}

int main(int argc, char **argv)
{
    const uint16_t one = 1;
    uint8_t first;
    memcpy(&first, &one, 1);
    if (first != 1)
        return fail("built for a little-endian host, running on a big-endian one");
    int fd = dup(STDOUT_FILENO);
    if (fd < 0 || !(results = fdopen(fd, "w")) || dup2(STDERR_FILENO, STDOUT_FILENO) < 0)
        return fail("cannot redirect stdout");
    if (argc >= 2 && !strcmp(argv[1], "encode"))
        return encode(argc, argv);
    if (argc >= 2 && !strcmp(argv[1], "decode"))
        return decode(argc, argv);
    return fail("usage: alac_tool encode|decode ...");
}

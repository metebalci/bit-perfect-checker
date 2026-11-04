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
 *     in 4), to OUT. Prints "frames <n>". The cookie and every packet are
 *     checked first, so that a damaged or crafted file fails instead of
 *     overrunning the decoder's buffers or decoding past a packet's end.
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
static const uint32_t kMinFrameLength = 32;                      /* the largest order (31) + 1 */
static const uint32_t kReadSlack = 16;                           /* see decode() */
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

/* Apple's decoder trusts its input. Init() allocates its buffers from the
   cookie's frame length, and unpc_block() writes out[0..numU] whatever the
   sample count (codec/dp_dec.c), so a small frame length overruns them; an
   escape packet's PCM is read without a length check (ALACDecoder.cpp), so a
   truncated one decodes from the bytes behind it and reports success. The
   helper therefore checks the cookie before Init(), and the header of each
   packet before Decode(), and afterwards that the element and the END tag
   behind it end exactly at the packet's end. */

/* The cookie's ALACSpecificConfig after the optional 'frma' and 'alac' atoms
   that Init() skips, checked: version 0, a frame length within ReadMe.txt's
   limit and no smaller than the predictor's largest order (31) plus one, the
   bit depths of the decoder's output code, one or two channels, and a Rice
   limit kb the decoder's bit reads assume (ag_dec.c: k <= 14; kb 0 shifts by
   32). pb and mb only tune the Rice history: any value decodes the bits it is
   given. Writes the 24 bytes to pass to Init(). */
static const char *check_cookie(const std::vector<uint8_t> &cookie, uint8_t out[24])
{
    size_t at = 0;
    if (cookie.size() >= 8 && !memcmp(&cookie[4], "frma", 4))
        at += 12;
    if (cookie.size() >= at + 8 && !memcmp(&cookie[at + 4], "alac", 4))
        at += 12;
    if (cookie.size() < at + sizeof(ALACSpecificConfig))
        return "bad cookie: too short";
    memcpy(out, &cookie[at], sizeof(ALACSpecificConfig));
    const uint32_t frame_length = (uint32_t)out[0] << 24 | (uint32_t)out[1] << 16 |
                                  (uint32_t)out[2] << 8 | out[3];
    const uint8_t version = out[4], depth = out[5], kb = out[8], nch = out[9];
    if (version != 0)
        return "bad cookie: compatible version";
    if (frame_length < kMinFrameLength || frame_length > kMaxFrameLength)
        return "bad cookie: frames per packet";
    if (depth != 16 && depth != 20 && depth != 24 && depth != 32)
        return "bad cookie: bit depth";
    if (nch < 1 || nch > 2)
        return "bad cookie: one or two channels only";
    if (kb < 1 || kb > 14)
        return "bad cookie: Rice limit kb";
    return nullptr;
}

/* A bounds-checked reader of a packet's bits, most significant first. */
struct PacketBits {
    const uint8_t *p;
    uint64_t end, pos;          /* in bits */
    bool take(uint32_t n, uint32_t *v)
    {
        if (end - pos < n)
            return false;
        uint32_t r = 0;
        for (uint32_t i = 0; i < n; i++, pos++)
            r = r << 1 | ((p[pos >> 3] >> (7 - (pos & 7))) & 1);
        *v = r;
        return true;
    }
    bool skip(uint64_t n)
    {
        if (end - pos < n)
            return false;
        pos += n;
        return true;
    }
};

/* The header of a packet's first element, which Apple's decoder decodes and
   stops after with one or two channels: the element of the channel count (SCE
   for one, CPE for two), the unused bits 0, a sample count from 1 to the
   frame length, and for a compressed element predictor orders below the
   sample count (Apple's encoder escapes a packet that short: its coefficients
   alone cost as much as the PCM) and bytes shifted below the bit depth, with
   the shifted bytes inside the packet. Sets *escape_end to the bit position
   at which an escape (uncompressed) element ends, else to 0. */
static const char *check_packet(const uint8_t *p, uint32_t size, uint32_t nch, uint32_t depth,
                                uint32_t frame_length, uint64_t *escape_end)
{
    PacketBits b = {p, (uint64_t)size * 8, 0};
    uint32_t tag, instance, unused, head, count, mix, coef;
    if (!b.take(3, &tag) || !b.take(4, &instance) || !b.take(12, &unused) || !b.take(4, &head))
        return "packet too short";
    if (tag != (nch == 2 ? (uint32_t)ID_CPE : (uint32_t)ID_SCE))
        return "packet does not start with the element for the channel count";
    if (unused)
        return "packet header: unused bits set";
    const uint32_t partial = head >> 3, shifted = (head >> 1) & 3, escape = head & 1;
    uint32_t samples = frame_length;
    if (partial) {
        if (!b.take(32, &count))
            return "packet too short";
        if (count > frame_length)
            return "partial-frame count exceeds the cookie's frames per packet";
        if (count == 0)
            return "partial-frame count 0";
        samples = count;
    }
    *escape_end = 0;
    if (escape) {
        /* The decoder reads bitDepth bits per sample of a CPE, and bitDepth
           less the shifted bytes of an SCE; the encoder shifts none here. */
        if (shifted)
            return "escape packet with shifted bytes";
        if (!b.skip((uint64_t)samples * nch * depth))
            return "escape packet shorter than its samples";
        *escape_end = b.pos;
        return nullptr;
    }
    const uint32_t chan_bits = depth - shifted * 8 + (nch == 2);
    if (shifted * 8 >= depth || chan_bits > 32)
        return "packet header: bytes shifted do not fit the bit depth";
    if (!b.take(16, &mix))
        return "packet too short";
    for (uint32_t ch = 0; ch < nch; ch++) {
        uint32_t mode, filter;
        if (!b.take(8, &mode) || !b.take(8, &filter))
            return "packet too short";
        const uint32_t order = filter & 0x1f;
        if (order >= samples)
            return "packet header: predictor order not below the sample count";
        for (uint32_t i = 0; i < order; i++)
            if (!b.take(16, &coef))
                return "packet too short";
    }
    if (!b.skip((uint64_t)shifted * 8 * nch * samples))
        return "packet shorter than its shifted bytes";
    return nullptr;
}

/* After Decode(): the element must end where the decoder stopped (an escape
   element where its header says), followed by the END tag and the padding to
   the packet's last byte. */
static const char *check_end(const BitBuffer &bb, const uint8_t *p, uint32_t size, uint64_t escape_end)
{
    const uint64_t pos = (uint64_t)(bb.cur - p) * 8 + bb.bitIndex;
    if (escape_end && pos != escape_end)
        return "escape packet decoded to the wrong length";
    PacketBits b = {p, (uint64_t)size * 8, pos};
    uint32_t tag;
    if (pos > b.end || !b.take(3, &tag))
        return "packet ends before its END element";
    if (tag != ID_END)
        return "packet: no END element after the audio element";
    if ((b.pos + 7) / 8 != size)
        return "packet longer than its elements";
    return nullptr;
}

static int decode(int argc, char **argv)
{
    std::vector<uint8_t> cookie;
    if (argc != 5 || !parse_hex(argv[2], &cookie))
        return fail("usage: decode COOKIE IN OUT");
    uint8_t config[sizeof(ALACSpecificConfig)];
    const char *bad = check_cookie(cookie, config);
    if (bad)
        return fail(bad);
    ALACDecoder dec;
    if (dec.Init(config, sizeof config))
        return fail("bad cookie");
    const ALACSpecificConfig &c = dec.mConfig;
    const uint32_t width = c.bitDepth == 16 ? 2 : c.bitDepth == 32 ? 4 : 3;

    FILE *fi = fopen(argv[3], "rb");
    if (!fi)
        return fail("cannot open the input");
    FILE *fo = fopen(argv[4], "wb");
    if (!fo)
        return fail("cannot create the output");

    /* A packet is at most the escape (uncompressed) form plus headers. Behind
       it the read buffer has as many zero bytes again plus kReadSlack: the
       Rice decoder checks its position against the packet's size counted
       from where its channel starts, so it may read up to a packet's length
       beyond the end before it fails (ag_dec.c dyn_decomp), plus a few bytes
       of look-ahead. */
    const uint32_t limit = c.frameLength * c.numChannels * 4 + 64;
    std::vector<uint8_t> wr((size_t)c.frameLength * c.numChannels * 4);
    uint64_t frames = 0;
    unsigned long long offset;
    unsigned long size;
    int got;
    while ((got = scanf("%llu %lu", &offset, &size)) == 2) {
        if (size > limit)
            return fail("packet larger than the cookie's format allows");
        std::vector<uint8_t> rd(2 * (size_t)size + kReadSlack);
        if (fseeko(fi, (off_t)offset, SEEK_SET) || fread(rd.data(), 1, size, fi) != size)
            return fail("short read from the input");
        uint64_t escape_end;
        bad = check_packet(rd.data(), (uint32_t)size, c.numChannels, c.bitDepth, c.frameLength,
                           &escape_end);
        if (bad)
            return fail(bad);
        BitBuffer bb;
        BitBufferInit(&bb, rd.data(), (uint32_t)size);
        uint32_t n = 0;
        if (dec.Decode(&bb, wr.data(), c.frameLength, c.numChannels, &n) || n > c.frameLength)
            return fail("Decode failed");
        bad = check_end(bb, rd.data(), (uint32_t)size, escape_end);
        if (bad)
            return fail(bad);
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

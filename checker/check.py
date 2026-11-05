# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mete Balci
"""Check a received stream against BPC1 v1 and build the report.

The verdict (BIT_PERFECT, NOT_BIT_PERFECT, UNKNOWN_SIGNAL) comes from verify.py.
The rest of this module is the diagnosis: informative, it describes what
changed, under the report's "diagnosis". Order of work of the diagnosis:
  1. Transport: PCM, DoP (markers 0x05/0xFA) or native DSD.
  2. Exact layer: X-sync anchors, frame-exact comparison, gaps.
  3. Robust layer: R-headers, for the block count, and for everything the
     exact layer cannot see (gain, resampling, DSD-to-PCM conversion).
  4. Classification of what changed, and the verdict.

A received stream may carry several files (a host joins files, for example
for gapless playback). check_stream() splits it as section 12.6 says and
reports each file; check() is for a stream that holds one.

The report names its coverage fields as the device's result line does:
coverage holds end, missing, lead, body, total, body_blocks, units, equal,
altered and uncompared, counts the body events by kind (in units;
discontinuity in events) and side_events (events in the lead-in and tail).
"""

import dataclasses
from collections import Counter

import numpy as np

from . import classify, exact, model, readers, robust, verify

GAP_KINDS = ("dropped", "inserted", "repeated", "zero_inserted", "silence_inserted",
             "zero_filled", "unlocated", "discontinuity")


def rate_text(rate):
    if rate in model.RATES[10:]:
        return "DSD%d" % (rate // 44100)
    return "%g kHz" % (rate / 1000)


def channel_finding(chmap):
    """CHANNEL_MAP finding for received channel c carrying source chmap[c]."""
    n = len(chmap)
    if sorted(chmap) == list(range(n)):
        txt = "channels swapped" if n == 2 else f"channels reordered {chmap}"
    else:
        txt = "channels duplicated (received channels carry source channels %s)" % chmap
    return classify.finding("CHANNEL_MAP", txt, order=list(chmap))


def _majority(items, key):
    if not items:
        return None
    best = Counter(key(i) for i in items).most_common(1)[0][0]
    return next(i for i in items if key(i) == best)


def _identity(f):
    return (f.kind, f.depth_code, f.rate_code, f.nch, f.seed)


def _signal_info(sig):
    return {
        "kind": model.KIND_TEXT[sig.kind],
        "rate": sig.rate,
        "depth": sig.depth,
        "channels": sig.nch,
        "seed": "0x%08X" % sig.seed,
        "blocks": {"lead_in": sig.lead, "body": sig.body, "total": sig.total},
    }


def _body_or(stream, res):
    acc = 0
    U = res.sig.units_per_block
    for rx_s, src_s, n in res.regions:
        for k in range(src_s // U, (src_s + n - 1) // U + 1):
            if res.sig.section(k) == "body":
                a, b = max(src_s, k * U), min(src_s + n, (k + 1) * U)
                if a < b:
                    seg = stream.data[rx_s + a - src_s:rx_s + b - src_s]
                    acc |= int(np.bitwise_or.reduce(seg.view(np.uint32).ravel()))
    return acc


def _effective(acc):
    return 0 if acc == 0 else 32 - ((acc & -acc).bit_length() - 1)


def _pairs_without_gaps(stream, res):
    """Aligned body pairs, leaving out zero-filled runs; the expected samples
    before res's polarity inversion."""
    rx, ref, pos = exact.aligned_pairs(stream, res, inverted=False)
    if rx is None:
        return None, None
    drop = np.zeros(len(stream.data), dtype=bool)
    for e in res.events:
        if e.kind == "zero_filled" and e.section == "body":
            drop[e.pos:e.pos + e.count] = True
    keep = ~drop[pos]
    return rx[keep], ref[keep]


def _counts(events):
    """Body events by kind, in units (discontinuity in events), and the
    number of events in the lead-in and tail."""
    counts = dict.fromkeys(GAP_KINDS, 0)
    for e in events:
        if e.section == "body" and e.kind in GAP_KINDS:
            counts[e.kind] += abs(e.count) if e.kind != "discontinuity" else 1
    counts["side_events"] = sum(1 for e in events if e.section != "body")
    return counts


def _no_coverage(total=None):
    """Coverage without an exact lock: not judged, 0 for unknown numbers."""
    return {"end": "STOP", "missing": "none", "lead": 0, "body": 0, "total": total or 0,
            "body_blocks": 0, "units": 0, "equal": 0, "altered": 0, "uncompared": 0}


def _coverage(work, ex, end, counts):
    """What of the file's body arrived, and how it ended (section 12.6)."""
    sig = ex.sig
    U = sig.units_per_block
    lead = sig.lead or 0
    rx, ref, pos = exact.aligned_pairs(work, ex)
    units = equal = altered = 0
    if rx is not None:
        same = (rx == ref).all(axis=1)
        zf = np.zeros(len(work.data), dtype=bool)
        for e in ex.events:
            if e.kind == "zero_filled" and e.section == "body":
                zf[e.pos:e.pos + e.count] = True
        units, equal = len(rx), int(same.sum())
        altered = int((~same & ~zf[pos]).sum())

    def covered(u):
        return any(src <= u < src + n for _, src, n in ex.regions)

    # The flags say where the body starts (a lead-in block, or block 0, seen)
    # and where it ends (a tail or the LAST block seen); else 0, unknown.
    flags = [f.flags for a in ex.anchors for f in a.fields.values()]
    lead_known = any(fl & model.F_LEAD_IN for fl in flags) or any(a.k == 0 for a in ex.anchors)
    body_known = lead_known and any(fl & (model.F_TAIL | model.F_LAST) for fl in flags)
    missing = []
    if not covered(lead * U):
        missing.append("start")
    if any(counts[k] for k in GAP_KINDS):
        missing.append("middle")
    # Only the body counts: a stop in the tail misses nothing. Without a tail
    # block or the LAST block seen, the body's end is not known to have arrived.
    if sig.body is None or not covered((lead + sig.body) * U - 1):
        missing.append("end")
    return {"end": end, "missing": ",".join(missing) or "none",
            "lead": lead if lead_known else 0, "body": (sig.body or 0) if body_known else 0,
            "total": sig.total or 0,
            "body_blocks": len(ex.body_blocks), "units": units, "equal": equal,
            "altered": altered, "uncompared": ex.body_units - units}


def _incomplete_text(cov, counts):
    parts = []
    if "start" in cov["missing"]:
        parts.append("the start of the body is missing")
    gaps = [f"{v} {k}" for k, v in counts.items()
            if k in GAP_KINDS and v]
    if gaps:
        parts.append(", ".join(gaps))
    if "end" in cov["missing"]:
        parts.append(f"the end of the body is missing (ended {cov['end']}, "
                     f"{cov['body_blocks']} of {cov['body'] or '?'} body blocks)")
    return "INCOMPLETE: " + "; ".join(parts)


def _event_dict(e):
    return {"type": e.kind, "count": e.count, "position": e.pos, "block": e.block,
            "section": e.section}


def _rhdr_summary(hs):
    if not hs:
        return None
    h = _majority(hs, lambda h: _identity(h.fields))
    same = [x for x in hs if _identity(x.fields) == _identity(h.fields)]
    level = float(np.median([x.level for x in same]))
    nominal = 0.5 if model.is_dsd_kind(h.fields.kind) else 0.25
    pol = Counter(x.polarity for x in same).most_common(1)[0][0]
    chmap = {}
    for x in same:
        chmap.setdefault(x.channel, Counter())[x.fields.channel] += 1
    chmap = {c: v.most_common(1)[0][0] for c, v in chmap.items()}
    mixed = set()
    for x in same:
        if x.mixed:
            mixed |= set(x.mixed)
    return {
        "mixed": mixed,
        "count": len(same),
        "fields": h.fields,
        "total": h.total,
        "src_rate": h.src_rate,
        "level_db": round(20 * np.log10(max(level, 1e-9) / nominal), 2),
        "polarity": pol,
        "chmap": chmap,
        "ratio": robust.rate_ratio(same),
        "chip": h.chip,
    }


def check(stream, name=None, allow_silence=False):
    """The report of a stream that holds one file."""
    reps = check_stream(stream, name, allow_silence)
    if len(reps) != 1:
        raise ValueError(f"the stream holds {len(reps)} files: use check_stream()")
    return reps[0]


def check_stream(stream, name=None, allow_silence=False):
    """One report per result of the received stream, in order: the verdict
    and the result-line facts from verify.py (allow_silence as there), and
    the informative diagnosis of what changed under "diagnosis" (None where
    it has none)."""
    diags = diagnose_stream(stream, name)
    results = verify.verify_stream(stream, allow_silence)
    files = [r for r in results if r["verdict"] != "UNKNOWN_SIGNAL"]
    paired = dict(zip(map(id, files), diags)) if len(files) == len(diags) else {}
    return [_report(name, r, paired.get(id(r)) or _overlapping(r, diags, stream)) for r in results]


def _overlapping(r, diags, stream):
    """The diagnosis report that overlaps the result's units most, if any."""
    scale = 2 if not stream.dsd and readers.detect_dop(stream) else 1
    a, b = r["first"] * scale, (r["first"] + r["units"]) * scale
    best, most = None, 0
    for d in diags:
        o = min(b, d["received"]["first"] + d["received"]["units"]) - max(a, d["received"]["first"])
        if o > most:
            best, most = d, o
    return best


def _report(name, r, diagnosis):
    rep = {"path": name, "file": r["file"], "verdict": r["verdict"]}
    if r["verdict"] == "BIT_PERFECT":
        rep["verdict_text"] = "BIT-PERFECT"
    elif r["verdict"] == "NOT_BIT_PERFECT":
        rep["verdict_text"] = f"NOT BIT-PERFECT: {r['why']}, at {r['diff']:.3f} s"
    else:
        rep["verdict_text"] = "UNKNOWN SIGNAL: audio of no recognised BPC1 file"
    rep.update((k, r[k]) for k in ("kind", "dop"))
    rep["seed"] = "0x%08X" % r["seed"]
    rep.update((k, r[k]) for k in ("nch", "file_rate", "depth", "rate", "bits", "end", "diff",
                                   "diff_unit", "diff_at", "why"))
    rep["received"] = {"first": r["first"], "units": r["units"]}
    rep["diagnosis"] = diagnosis
    return rep


def diagnose(stream, name=None):
    """The diagnosis of a stream that holds one file."""
    reps = diagnose_stream(stream, name)
    if len(reps) != 1:
        raise ValueError(f"the stream holds {len(reps)} files: use diagnose_stream()")
    return reps[0]


def diagnose_stream(stream, name=None):
    """The diagnosis, informative: one report per file in the received
    stream, in order, of what changed."""
    base = {"path": name, "notes": list(stream.notes)}
    work = stream
    transport = "DSD" if stream.dsd else "PCM"
    if not stream.dsd and readers.detect_dop(stream):
        work = readers.unpack_dop(stream)
        transport = "DoP"
        base["notes"] += work.notes
    base["stream"] = {
        "transport": transport,
        "container": stream.container,
        "rate": stream.rate,
        "channels": stream.nch,
        "slot_bits": stream.slot_bits if not stream.dsd else None,
        "valid_bits": stream.valid_bits if not stream.dsd else None,
        "float": stream.is_float,
    }
    if transport == "DoP":
        base["stream"]["dsd_rate"] = work.rate
    findings = []
    polarity = 1
    anchors = exact.find_anchors(work)
    if not anchors and not work.dsd:
        # Without an X-sync, look for an inverted one: an inverted stream is
        # then compared against the inverted reference.
        anchors = exact.find_anchors(work, inverted=True)
        if anchors:
            polarity = -1
            findings.append(classify.finding("POLARITY", "polarity inverted"))
    if not anchors:
        rep = _new_report(base, 1, 0, len(work.data))
        return [_check_file(rep, stream, work, [], polarity, findings)]
    reps = []
    for i, (u0, u1, end, fa) in enumerate(exact.split_files(anchors, len(work.data)), 1):
        part = dataclasses.replace(work, data=work.data[u0:u1], notes=[])
        fa = [exact.Anchor(a.pos - u0, a.k, a.fields) for a in fa]
        rep = _new_report(base, i, u0, u1 - u0)
        reps.append(_check_file(rep, stream, part, fa, polarity, list(findings), end))
    return reps


def _new_report(base, index, first, count):
    rep = {"path": base["path"], "file": index, "notes": list(base["notes"]),
           "stream": dict(base["stream"]), "received": {"first": first, "units": count}}
    return rep


def _check_file(rep, stream, work, anchors, polarity, findings, end="STOP"):
    hs = robust.scan(work, max_seconds=30.0 if anchors else None)
    rh = _rhdr_summary(hs)
    rep["robust"] = None if rh is None else {
        "headers": rh["count"], "source_rate": rh["src_rate"],
        "level_db": rh["level_db"], "polarity": rh["polarity"],
        "rate_ratio": None if rh["ratio"] is None else round(rh["ratio"], 6)}

    ex = None
    if anchors:
        ref_fields, kept = exact.file_identity(anchors)
        total = rh["total"] if rh and _identity(rh["fields"]) == _identity(ref_fields) else None
        sig0 = exact.build_signal(ref_fields, kept, total)
        if total is None:
            total = exact.decode_rhdr_exact(work, sig0, kept, exact.channel_map(kept, work.nch))
        k = None
        if not work.dsd:
            data = work.data
            if polarity < 0:    # leave out the word the inversion clamps -2^31 to
                data = data[data != np.int32((1 << 31) - (1 << (32 - sig0.depth)))]
            k_all = classify.effective_bits(data)
            if 0 < k_all < sig0.depth:
                k = k_all
        ex = exact.analyze(work, total=total, k=k, anchors=anchors, polarity=polarity)

    if ex is not None:
        return _report_exact(rep, stream, work, ex, rh, findings, end)
    rep["counts"] = _counts([])
    if rh is not None:
        rep["coverage"] = _no_coverage(rh["total"])
        return _report_robust(rep, stream, work, hs, rh, rep["stream"]["transport"])
    rep["coverage"] = _no_coverage()
    rep["lock"] = "none"
    rep["verdict"] = "NO_SIGNAL"
    rep["verdict_text"] = "NO SIGNAL / NOT A BPC1 TEST FILE"
    rep["reasons"] = []
    return rep


def _report_exact(rep, stream, work, ex, rh, findings, end):
    sig = ex.sig
    rep["lock"] = "exact"
    rep["signal"] = _signal_info(sig)
    rep["sync"] = {"x_syncs": len(ex.anchors), "r_headers": rh["count"] if rh else 0}
    if ex.chmap != list(range(work.nch)):
        findings.append(channel_finding(ex.chmap))
    if work.rate != sig.rate:
        findings.append(classify.finding(
            "RATE_MISMATCH", f"{rate_text(sig.rate)} content received at "
            f"{rate_text(work.rate)} without resampling"))
    counts = _counts(ex.events)
    cov = _coverage(work, ex, end, counts)
    rep["coverage"] = cov
    rep["counts"] = counts
    first = rep["received"]["first"]
    rep["events"] = [dict(_event_dict(e), position=e.pos + first) for e in ex.events][:50]
    altered = [e for e in ex.events if e.section == "body" and e.kind == "altered"]
    if not work.dsd:
        acc = _body_or(work, ex)
        rep["depth"] = {"file_bits": sig.depth, "used_bits": _effective(acc),
                        "slot_bits": stream.slot_bits}
        if altered or ex.body_equal < ex.body_units:
            rx, ref = _pairs_without_gaps(work, ex)
            if rx is not None:
                findings += classify.classify(rx, ref, sig.depth, polarity=ex.polarity)
    elif altered:
        n = sum(e.count for e in altered)
        findings.append(classify.finding("BIT_ERRORS", f"{n} DSD bytes differ", bytes=n))
    findings = classify.in_order(findings)
    rep["reasons"] = findings
    if findings:
        rep["verdict"] = "ALTERED"
        rep["verdict_text"] = "ALTERED: " + findings[0]["text"]
    elif cov["missing"] != "none":
        rep["verdict"] = "INCOMPLETE"
        rep["verdict_text"] = _incomplete_text(cov, counts)
    else:
        rep["verdict"] = "BIT_PERFECT"
        rep["verdict_text"] = "BIT-PERFECT"
    if not work.dsd and stream.slot_bits > sig.depth and not findings:
        rep["notes"].append(f"{sig.depth}-bit content in a {stream.slot_bits}-bit slot")
    side = Counter((e.section, e.kind) for e in ex.events if e.section != "body")
    for (sec, kind), n in sorted(side.items()):
        rep["notes"].append(f"{sec}: {n} {kind} event(s), not counted")
    return rep


def _report_robust(rep, stream, work, hs, rh, transport):
    f = rh["fields"]
    sig = model.Signal(f, None, None, rh["total"])
    rep["lock"] = "robust"
    rep["signal"] = _signal_info(sig)
    rep["sync"] = {"x_syncs": 0, "r_headers": rh["count"]}
    findings = []
    rx_rate = work.rate
    src = rh["src_rate"]
    if rh["polarity"] < 0:
        findings.append(classify.finding("POLARITY", "polarity inverted"))
    if rh["mixed"]:
        findings.append(classify.finding(
            "DOWNMIX", "channels downmixed (every received channel carries "
            "source channels %s)" % sorted(rh["mixed"])))
    elif any(c != v for c, v in rh["chmap"].items()):
        findings.append(channel_finding([rh["chmap"].get(c, c) for c in range(work.nch)]))
    level = rh["level_db"]
    ratio = rh["ratio"]
    if model.is_dsd_kind(f.kind) and transport == "PCM":
        findings.append(classify.finding(
            "DSD_TO_PCM", f"DSD converted to PCM: {rate_text(src)} -> PCM {rate_text(rx_rate)}",
            file_rate=src, rate=rx_rate))
        findings.append(classify.finding("LEVEL", f"level {level:+.2f} dB re 50% DSD modulation",
                                         level_db=level))
        return _altered(rep, findings)
    time_kept = ratio is None or abs(ratio * src / rx_rate - 1) < 0.002
    if src != rx_rate and time_kept:
        what = "DSD re-modulated" if model.is_dsd_kind(f.kind) else "resampled"
        findings.append(classify.finding(
            "RESAMPLED", f"{what}: {rate_text(src)} -> {rate_text(rx_rate)}",
            file_rate=src, rate=rx_rate))
        if abs(level) >= robust.GAIN_DB_MIN:
            findings.append(classify.finding("GAIN", f"volume changed ({level:+.2f} dB, "
                                             "from the robust header level)", gain_db=[level]))
        return _altered(rep, findings)
    if src != rx_rate:
        findings.append(classify.finding(
            "RATE_MISMATCH", f"{rate_text(src)} content received at {rate_text(rx_rate)} "
            "without resampling"))
    # Same rate and time base: align on the R-headers and compare samples.
    ex = _align_on_rheaders(work, hs, rh, sig)
    if ex is not None and not work.dsd:
        rx, ref, _ = exact.aligned_pairs(work, ex)
        if rx is not None:
            found = classify.classify(rx, ref, sig.depth)
            if rh["mixed"]:
                found = [x for x in found if x["code"] not in ("UNKNOWN", "BIT_ERRORS")]
            have = {x["code"] for x in findings}
            findings += [x for x in found if x["code"] not in have]
            rep["depth"] = {"file_bits": sig.depth, "used_bits": classify.effective_bits(rx),
                            "slot_bits": stream.slot_bits}
    elif work.dsd:
        findings.append(classify.finding("DSD_PROCESSED", "DSD bitstream processed "
                                         f"(robust header level {level:+.2f} dB)"))
    if not findings:
        findings.append(classify.finding("UNKNOWN", "unknown processing"))
    return _altered(rep, findings)


def _altered(rep, findings):
    findings = classify.in_order(findings)
    rep["reasons"] = findings
    rep["verdict"] = "ALTERED"
    rep["verdict_text"] = "ALTERED: " + findings[0]["text"]
    return rep


def _align_on_rheaders(work, hs, rh, sig):
    """Pseudo-anchors from the R-headers (block start = Barker start - 512 M)."""
    ident = _identity(rh["fields"])
    by_pos = {}
    for h in hs:
        if _identity(h.fields) != ident:
            continue
        if work.dsd:
            pos = int(round(2 * h.pos)) - sig.r_start // 8
        else:
            pos = int(round(h.pos)) - sig.r_start
        f = h.fields
        if h.mixed:     # a downmix: compare each channel with its own source
            f = dataclasses.replace(f, channel=h.channel)
        by_pos.setdefault(pos, {})[h.channel] = f
    if not by_pos:
        return None
    anchors = [exact.Anchor(p, next(iter(fl.values())).block, fl)
               for p, fl in sorted(by_pos.items()) if p >= 0]
    if not anchors:
        return None
    if not work.dsd:
        # refine by +-2 frames against the expected payload of the next block
        a = anchors[0]
        best = (None, 0)
        nxt = sig.ref((a.k + 1) * sig.B, (a.k + 2) * sig.B).astype(np.float64)
        for d in range(-2, 3):
            p = a.pos + sig.B + d
            seg = work.data[p:p + sig.B].astype(np.float64)
            if len(seg) == sig.B:
                c = abs(float(np.sum(seg[:, 0] * nxt[:, 0])))
                if best[0] is None or c > best[1]:
                    best = (d, c)
        if best[0]:
            anchors = [exact.Anchor(x.pos + best[0], x.k, x.fields) for x in anchors]
    ex = exact.analyze(work, total=rh["total"], anchors=anchors)
    if ex is None:
        return None
    # The R-headers carry their own block's flags only. Judge the blocks from
    # the first R-header outside the lead-in to the last one before the tail.
    flags = {a.k: next(iter(a.fields.values())).flags for a in anchors}
    body = [k for k, fl in flags.items() if not fl & (model.F_LEAD_IN | model.F_TAIL)]
    if not body:
        return None
    ex.sig.lead = min(body)
    ex.sig.body = max(body) + 1 - min(body)
    return ex

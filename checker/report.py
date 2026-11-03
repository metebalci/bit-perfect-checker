"""Text rendering of a check report."""

from .check import rate_text


def render(rep, several=False):
    """The report as text, the verdict first, then the diagnosis; `several`:
    the stream holds more than one file."""
    out = []
    if rep.get("path"):
        out.append(rep["path"])
    if several:
        rc = rep["received"]
        out.append(f"  File      {rep['file']} of the stream, from unit {rc['first']}, "
                   f"{rc['units']} units")
    out.append(f"  Verdict   {rep['verdict_text']}")
    if rep["verdict"] != "UNKNOWN_SIGNAL":
        kind = rep["kind"] + (" as DoP" if rep["dop"] else "")
        out.append(f"  File      {kind}, {rate_text(rep['file_rate'])}, depth {rep['depth']}, "
                   f"{rep['nch']} ch, seed {rep['seed']}; received at {rep['rate']} Hz, "
                   f"{rep['bits']} bits, end {rep['end']}")
    if rep["diagnosis"] is not None:
        out.append("  Diagnosis (informative, not the verdict):")
        out += ["  " + x for x in render_diagnosis(dict(rep["diagnosis"], path=None)).splitlines()]
    return "\n".join(out)


def render_diagnosis(rep, several=False):
    """The diagnosis report as text."""
    out = []
    if rep.get("path"):
        out.append(rep["path"])
    if several:
        rc = rep["received"]
        out.append(f"  File      {rep['file']} of the stream, from unit {rc['first']}, "
                   f"{rc['units']} units")
    st = rep["stream"]
    if st["transport"] == "DSD":
        line = f"native DSD, {rate_text(st['rate'])}, {st['channels']} ch ({st['container']})"
    else:
        line = (f"{st['transport']}, {st['rate']} Hz, {st['channels']} ch, "
                f"{st['slot_bits']}-bit slot"
                + (f", {st['valid_bits']} valid bits" if st['valid_bits'] != st['slot_bits'] else "")
                + (", float" if st["float"] else "") + f" ({st['container']})")
        if st["transport"] == "DoP":
            line += f", carrying {rate_text(st['dsd_rate'])}"
    out.append(f"  Stream    {line}")
    sig = rep.get("signal")
    if sig:
        depth = "1-bit" if sig["depth"] == 1 else f"{sig['depth']}-bit"
        what = (f"PCM {rate_text(sig['rate'])} {depth}" if sig["kind"] == "PCM"
                else f"{rate_text(sig['rate'])} from {sig['kind']}")
        out.append(f"  Signal    BPC1 v1 {what}, {sig['channels']} ch, seed {sig['seed']}")
        b = sig["blocks"]
        if b["total"] is not None:
            txt = f"{b['total']} blocks"
            if b["lead_in"] is not None and b["body"] is not None and rep["lock"] == "exact":
                txt += f" (lead-in {b['lead_in']}, body {b['body']}, tail {b['total'] - b['lead_in'] - b['body']})"
            out.append(f"  Blocks    {txt}")
    sy = rep.get("sync")
    lock = rep["lock"]
    if sy:
        out.append(f"  Lock      {lock}: {sy['x_syncs']} X-syncs, {sy['r_headers']} R-headers")
    else:
        out.append(f"  Lock      {lock}")
    d = rep.get("depth")
    if d:
        out.append(f"  Depth     {d['file_bits']}-bit file, {d['used_bits']} bits used, "
                   f"{d['slot_bits']}-bit slot")
    rb = rep.get("robust")
    if rb and lock != "exact":
        r = f"source {rate_text(rb['source_rate'])}, level {rb['level_db']:+.2f} dB"
        if rb["polarity"] < 0:
            r += ", inverted"
        if rb["rate_ratio"] is not None:
            r += f", rate ratio {rb['rate_ratio']:.6f}"
        out.append(f"  R-header  {r}")
    cov = rep.get("coverage")
    if rep["lock"] == "exact":
        out.append(f"  Coverage  ended {cov['end']}, missing {cov['missing']}; body blocks "
                   f"{cov['body_blocks']}/{cov['body'] or '?'}, units {cov['units']} compared, "
                   f"{cov['equal']} equal, {cov['altered']} altered, "
                   f"{cov['uncompared']} uncompared")
    counts = {k: v for k, v in rep.get("counts", {}).items() if v and k != "side_events"}
    if counts:
        out.append("  Counts    " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    for r in rep.get("reasons", [])[1:]:
        out.append(f"  Also      {r['text']}")
    for n in rep.get("notes", []):
        out.append(f"  Note      {n}")
    out.append(f"  Finding   {rep['verdict_text']}")
    return "\n".join(out)

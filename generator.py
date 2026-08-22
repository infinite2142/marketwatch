#!/usr/bin/env python3
"""
generator.py — Market Watch V2.7 real-data HTML generator (Phase 2a)

Reads:
  market_watch_data.json   the canonical, section-by-section data model
  template_v27.html        the LOCKED v2.7 visual design (CSS + JS render engine)
Writes:
  index.html               a complete, self-contained page, visually identical to v2.7

Method: the locked template renders every section client-side from ~14 inline JS data
literals. This generator swaps those literals for the JSON data, rewrites the state-of-play
narrative, and stamps the build/report dates — leaving all CSS, SVG and interactivity
(treemap, lifecycle spine, bubble map, flip tiles, expandable cards, timeframe dropdown,
hamburger drawer, crash buckets, Dashboard|Stocks tabs, alerts preview) untouched.
Re-runnable: the daily task calls `python3 generator.py` each morning.

No external dependencies. No localStorage. Output is offline-capable.
"""
import json, re, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH     = os.path.join(HERE, "market_watch_data.json")
TEMPLATE_PATH = os.path.join(HERE, "template_v27.html")
OUT_PATH      = os.path.join(HERE, "index.html")

# JS var name in the template  ->  path into the JSON data
INJECT = {
    "windows":   lambda d: d["windows"],
    "tiles":     lambda d: d["state_of_play"]["tiles"],
    "crash":     lambda d: d["crash_risk"],
    "secData":   lambda d: d["sectors"],
    "drivers":   lambda d: d["drivers"],
    "cat":       lambda d: d["cat"],
    "themes":    lambda d: d["investible_themes"],
    "spine":     lambda d: d["spine"],
    "radar":     lambda d: d["radar"],
    "faded":     lambda d: d["faded"],
    "sigTag":    lambda d: d["sigTag"],
    "signals":   lambda d: d["signals"],
    "stockSegs": lambda d: d["stocks"]["segments"],
    "watchlist": lambda d: d["stocks"]["watchlist"],
}

def find_literal_end(s, i):
    """Return index just past the JS array/object literal that starts at s[i] ('[' or '{').
    Quote/backtick-aware so brackets inside strings are ignored."""
    depth = 0; in_str = None; esc = False; j = i
    n = len(s)
    while j < n:
        c = s[j]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == in_str: in_str = None
        else:
            if c in ('"', "'", "`"): in_str = c
            elif c in "[{": depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0: return j + 1
        j += 1
    raise ValueError("unterminated literal from index %d" % i)

def replace_js_literal(html, name, value):
    key = "const " + name + "="
    p = html.find(key)
    if p < 0:
        raise ValueError("template missing declaration: %s" % key)
    start = p + len(key)
    while html[start] in " \t\r\n":
        start += 1
    if html[start] not in "[{":
        raise ValueError("unexpected literal start for %s: %r" % (name, html[start]))
    end = find_literal_end(html, start)
    payload = json.dumps(value, ensure_ascii=False)
    return html[:start] + payload + html[end:]


def derive_sector_reads(data):
    """Attach each sector's conviction and a derived timing read, joined from
    investible_themes by name. The timing read is COMPUTED from stage / momentum /
    runway so it can never contradict the fields beneath it. Unmatched sectors are
    reported — they are the sector/theme drift, and they should be resolved in the
    ledger rather than papered over here."""
    def norm(s):
        return "".join(ch for ch in s.lower() if ch.isalnum())
    themes = data.get("investible_themes", [])
    idx = {norm(t["nm"]): t for t in themes}
    orphans = []
    for sec in data.get("sectors", []):
        key = norm(sec["nm"])
        th = idx.get(key)
        if th is None:                      # prefix match either direction
            for k, t in idx.items():
                if k.startswith(key[:12]) or key.startswith(k[:12]):
                    th = t; break
        if th is None:
            orphans.append(sec["nm"]); sec["conv"] = None; sec["timing"] = None
            continue
        sec["conv"] = th.get("conv")
        stage  = (th.get("stage") or "").lower()
        runway = th.get("runway") or 0
        mom    = sec.get("mom")
        if mom == "det":                        t = "Rolling over"
        elif stage == "late" or runway >= 58:   t = "Late"
        elif stage == "emerging":               t = "Forming"
        elif mom == "flat":                     t = "Stalled"
        elif stage == "building":               t = "Early"
        else:                                   t = "Running"
        sec["timing"] = t
    if orphans:
        print("WARN: sectors with no matching theme (resolve in the ledger): %s"
              % ", ".join(orphans), file=sys.stderr)
    return data

STAGE_ORDER = ["Radar", "Emerging", "Building", "Confirmed", "Fading", "Faded / Retired"]
THEME_STAGE = {"emerging": "Emerging", "building": "Building",
               "confirmed": "Confirmed", "late": "Fading"}

def _chip(nm, cap=20):
    """Short chip label: drop a parenthetical or em-dash tail, then trim on a word
    boundary. Only used for entries the old hand-written spine never labelled."""
    for cut in (" \u2014 ", " \u2013 ", " ("):
        if cut in nm:
            nm = nm.split(cut)[0]
    nm = nm.strip()
    if len(nm) <= cap:
        return nm
    out = nm[:cap].rsplit(" ", 1)[0]
    return (out or nm[:cap]) + "\u2026"

def derive_spine(data):
    """Build the lifecycle spine from radar + investible_themes + faded rather than
    reading the stored `spine` array. Same reasoning as derive_sector_reads: a second
    hand-maintained copy of a theme's stage will drift from the record beneath it, and
    on 2026-08-22 three had — stablecoin infra was a 2/5 radar card AND 'Fading',
    crypto was `stage: confirmed` AND 'Fading', and 7 of 10 faded entries were missing
    entirely. Placement is derived; only the short chip label stays editorial, reused
    from the stored spine where an id still matches."""
    labels = {}
    for col in data.get("spine", []):
        for it in col.get("items", []):
            if isinstance(it, list) and len(it) > 1 and it[1]:
                labels[it[1]] = it[0]

    buckets = {s: [] for s in STAGE_ORDER}
    for r in data.get("radar", []):
        buckets["Radar"].append([labels.get(r["id"], _chip(r["nm"])), r["id"]])
    for t in data.get("investible_themes", []):
        tid = "theme-" + t["id"]
        stage = THEME_STAGE.get((t.get("stage") or "").lower())
        if stage is None:
            print("WARN: theme %s has unmapped stage %r — placed in Building"
                  % (t["id"], t.get("stage")), file=sys.stderr)
            stage = "Building"
        buckets[stage].append([labels.get(tid, _chip(t["nm"])), tid])
    for bucket in ("retired", "dormant"):
        for f in data.get("faded", {}).get(bucket, []):
            buckets["Faded / Retired"].append([labels.get(f["id"], _chip(f["nm"])), f["id"]])

    data["spine"] = [{"stage": s, "items": buckets[s]} for s in STAGE_ORDER]
    return data


def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        html = f.read()

    data = derive_sector_reads(data)
    data = derive_spine(data)
    meta = data["meta"]
    build_date  = datetime.date.today().isoformat()   # stamp fresh each run
    report_date = meta.get("report_date", build_date)
    report_long = meta.get("report_date_long", report_date)
    refresh     = meta.get("refresh_label", "Last refresh")

    # 1) swap the 14 data literals
    for name, getter in INJECT.items():
        html = replace_js_literal(html, name, getter(data))

    # 2) rewrite the state-of-play narrative (static <p> in the shell)
    narrative = data["state_of_play"]["narrative"]
    html, nsub = re.subn(
        r'(<div class="stateofplay">\s*<div class="k">Narrative</div>\s*<p>).*?(</p>)',
        lambda mo: mo.group(1) + narrative + mo.group(2),
        html, count=1, flags=re.S)
    if nsub != 1:
        print("WARN: narrative paragraph not replaced", file=sys.stderr)

    # 3) stamp build / report dates (header vbadge, sub, footer, datechip, JS base date)
    html = html.replace("built 2026-08-10", "built " + build_date)     # vbadge + footer
    html = html.replace("Sunday 9 August 2026", report_long)           # header .sub
    # datechip: fetch_data.py refreshes the numbers daily but does NOT touch
    # meta.report_date — that is the analytical layer's field. When the two
    # diverge the page must say so rather than imply the analysis is current.
    if report_date == build_date:
        chip = refresh
    else:
        chip = "Data %s \u00b7 analysis %s" % (build_date, report_date)
    html = html.replace("Last refresh 07:30 BST", chip)                # datechip
    # JS relative-date base -> report date (month is 0-indexed in JS)
    y, mo, dy = (int(x) for x in report_date.split("-"))
    html = html.replace("new Date(2026,7,9)", "new Date(%d,%d,%d)" % (y, mo - 1, dy))

    # 4) provenance banner comment (invisible; audit aid)
    banner = ("\n<!-- Generated by generator.py from market_watch_data.json (schema %s) "
              "on %s · report date %s · Phase 2a real-data preview -->\n"
              % (meta.get("schema_version", "?"), build_date, report_date))
    html = html.replace("</body>", banner + "</body>")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote %s (%d bytes) · build %s · report %s" %
          (OUT_PATH, len(html), build_date, report_date))

if __name__ == "__main__":
    main()

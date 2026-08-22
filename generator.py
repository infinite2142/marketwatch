#!/usr/bin/env python3
"""
generator.py — Market Watch V2.8 real-data HTML generator

Reads:
  market_watch_data.json   the canonical, section-by-section data model
  template_v28.html        the v2.8 design (CSS + JS render engine)
                           template_v27.html is kept for reference; nothing reads it
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
TEMPLATE_PATH = os.path.join(HERE, "template_v28.html")
OUT_PATH      = os.path.join(HERE, "index.html")

# JS var name in the template  ->  path into the JSON data
INJECT_V28 = ["MARKS","DRIVERS","TILES","SIGNALS","CATS","SIGMETA",
              "CRASH","META","WINDOWS","STATE"]

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



# ===================== V2.8 view model =====================
# The page renders one dataset several ways, so the shaping happens here rather than
# in the browser: the same records feed the lifecycle chart, the themes grid and the
# detail panel, and they cannot disagree with each other if they are built once.

import subprocess
from collections import Counter

STAGE_ORDER_V28 = ["Radar", "Emerging", "Building", "Confirmed", "Fading", "Faded / Retired"]
THEME_STAGE_V28 = {"emerging": "Emerging", "building": "Building",
                   "confirmed": "Confirmed", "late": "Fading"}
CALLY   = {"good": 80, "mixed": 48, "miss": 16}
DIRLBL  = {"tailwind": "Tailwind", "headwind": "Headwind", "rotational": "Rotational"}
ARROW   = {"up": "↗", "flat": "→", "down": "↘"}
MOMLBL  = {"imp": "improving", "flat": "flat", "det": "deteriorating"}
LOOKBACK = "HEAD~30"
WINDOW   = 7
CAP      = 420

BAD = re.compile(r"correction|held at|scored|th(is|e) (page|desk|book)'?s?\b|priced at", re.I)
OPS = re.compile(r"^(CORRECTION|FALSE SILENCE|SECOND FALSE SILENCE|PINNED)\b"
                 r"|\bth(is|e) (page|desk|book)'?s?\b", re.I)
DESK = re.compile(r'\b(this (card|entry|book|page|desk|week)|yesterday this|why it was missed|'
                  r'the discipline|logged|scored|sweep(s|ing)?|stated precisely|observed quiet|'
                  r'quiet cycle|the silence broke|silence|first observed|two cycles|'
                  r'live base|withdrawn|re-?scored|not adopted|this run|'
                  r'this desk|the desk|for two runs|previous run|the daily|age trigger|'
                  r'invalidation|the ledger|conviction cut|trajectory cut)\b'
                  r"|\b[a-z]+_[a-z_]+\b"                    # composite_meta.as_of, last_fetch
                  r"|\bthe (rule|register|book)\b", re.I)

SECICON = {"rates":"activity","capex":"layers","power":"zap","memory":"cpu","semis":"cpu",
           "optics":"sparkles","gold":"coins","crypto":"coins","minerals":"layers",
           "nuclear":"atom","defence":"shield","cyber":"shield","geo":"globe","intl":"globe",
           "reshore":"factory","shipbuilding":"ship","labour":"users","privmkts":"landmark",
           "neoclouds":"layers"}
RADICON = {"quantum":"atom","humanoid":"cpu","shipbuilding":"ship","trades":"users",
           "labourdef":"shield","stablecoin":"coins","privmkts":"landmark"}
DRVICON = {"capex":"layers","rates":"activity","energy":"zap","fiscal":"landmark",
           "reshore":"factory","supply":"shield","labour":"users","intl":"globe","geo":"alert"}

def _pick(table, key, default):
    k = (key or "").lower()
    for probe, val in table.items():
        if probe in k:
            return val
    return default

def _cap(t):
    """Reader prose is a summary, not the working. Trim to the last whole sentence
    inside CAP so one long entry cannot crowd out everything below it."""
    t = (t or "").strip()
    if len(t) <= CAP:
        return t
    cut = t[:CAP]
    i = max(cut.rfind(". "), cut.rfind("; "))
    return cut[:i + 1] if i > CAP * 0.45 else cut.rsplit(" ", 1)[0] + "…"

def desk_share(txt):
    """Fraction of a passage that is desk commentary. Used to decide whether a whole
    paragraph belongs in the audit, instead of exiling it for one trigger word."""
    parts = [p for p in re.split(r'(?<=[.!?])\s+', txt or "") if p.strip()]
    if not parts:
        return 0.0
    bad = sum(1 for p in parts if BAD.search(p) or DESK.search(p))
    return bad / len(parts)

def reader_prose(txt):
    """Split a prose field into what the reader gets and the desk commentary it
    displaced. See daily-task.md, "Two audiences". Once the daily writes `audit`
    directly this becomes a fallback for records written before that."""
    if not txt:
        return "", []
    parts = re.split(r'(?<=[.!?])\s+', txt)
    keep  = [p for p in parts if not BAD.search(p) and not DESK.search(p)]
    out   = " ".join(keep).strip()
    drop  = [p.strip() for p in parts if p not in keep]
    if len(out) <= 60:
        return _cap(txt), []          # never blank a field to enforce a style
    return _cap(out), drop

def _rp(t): return reader_prose(t)[0]
def _au(t): return reader_prose(t)[1]

def _prior_stages(rev):
    """Stage of every tracked id at an earlier commit. The data file is committed daily,
    so movement between stages is derivable from history and needs no stored field.
    Returns None when history is unavailable — the page then shows no badges, which is
    correct rather than wrong."""
    try:
        raw = subprocess.run(["git", "show", rev + ":market_watch_data.json"],
                             capture_output=True, text=True, check=True, cwd=HERE,
                             timeout=20).stdout
        d = json.loads(raw)
    except Exception as e:
        print("WARN: movement badges unavailable (%s)" % e, file=sys.stderr)
        return None
    o = {}
    for r in d.get("radar", []):
        o[r["id"]] = "Radar"
    for t in d.get("investible_themes", []):
        o["theme-" + t["id"]] = THEME_STAGE_V28.get((t.get("stage") or "").lower(), "?")
    for _, items in d.get("faded", {}).items():
        for f in items:
            o[f["id"]] = "Faded / Retired"
    return o

def build_v28(data):
    """Everything template_v28.html renders, shaped once."""
    prior = _prior_stages(LOOKBACK)
    order = {s: i for i, s in enumerate(STAGE_ORDER_V28)}

    def movement(mid, stage):
        if prior is None:
            return None
        if mid not in prior:
            return {"tag": "new", "txt": "new"}
        was = prior[mid]
        if was == stage:
            return None
        if stage == "Faded / Retired":
            return {"tag": "retired", "txt": "retired from " + was.lower()}
        if order.get(stage, 0) > order.get(was, 0):
            verb = "graduated" if was == "Radar" else "promoted"
            return {"tag": "up", "txt": verb + " from " + was.lower()}
        return {"tag": "down", "txt": "demoted from " + was.lower()}

    def norm(x): return "".join(c for c in x.lower() if c.isalnum())
    secs = {norm(s["nm"]): s for s in data.get("sectors", [])}

    def sector_for(nm):
        k = norm(nm)
        if k in secs:
            return secs[k]
        for kk, v in secs.items():
            if kk.startswith(k[:12]) or k.startswith(kk[:12]):
                return v
        return None

    def timing_of(t, mom):
        stage, runway = (t.get("stage") or "").lower(), t.get("runway") or 0
        if mom == "det":                      return "Rolling over"
        if stage == "late" or runway >= 58:   return "Late"
        if stage == "emerging":               return "Forming"
        if mom == "flat":                     return "Stalled"
        if stage == "building":               return "Early"
        return "Running"

    crit = data.get("critLabels") or []
    marks = []

    for r in data.get("radar", []):
        n = sum(1 for m in r["met"] if m)
        marks.append(dict(
            id=r["id"], nm=r["nm"], k="radar", st="Radar", y=n / 5 * 100, met=n,
            status=r["status"], icon=_pick(RADICON, r["id"], "sparkles"),
            mv=movement(r["id"], "Radar"),
            sub="%s · %d/5 criteria" % (r["status"], n), note=r.get("eta", ""),
            body=[["What it is", r.get("what", "")], ["To graduate", _rp(r.get("needs", ""))]],
            audit=(r.get("audit") or _au(r.get("needs", "")) + _au(r.get("summ", ""))),
            bullets=[e for e in r.get("evidence", []) if not BAD.search(e)][:4],
            crit=[[crit[i][0] if i < len(crit) else str(i + 1), bool(m)]
                  for i, m in enumerate(r["met"])]))

    for t in data.get("investible_themes", []):
        sec = sector_for(t["nm"]) or {}
        mom = sec.get("mom", "flat")
        tim = timing_of(t, mom)
        acc = []
        for a in t.get("access", []) or []:
            veh = ["".join(v) if isinstance(v, list) else str(v) for v in a.get("vehicles", [])]
            acc.append([a.get("route", ""), "".join(veh)])
        stage = THEME_STAGE_V28.get((t.get("stage") or "").lower(), "Building")
        marks.append(dict(
            id="theme-" + t["id"], nm=t["nm"], k="theme", st=stage, y=t.get("y", 50),
            size=t.get("size", 100), conv=t.get("conv", 3), mom=mom, timing=tim,
            mv=movement("theme-" + t["id"], stage),
            sub="%s · conviction %s/5 · %s, momentum %s"
                % (t.get("stageLbl", stage), t.get("conv", 3), tim.lower(), MOMLBL.get(mom, mom)),
            note="%s → %s  (%s)" % (t.get("now", ""), t.get("proj", ""), t.get("hz", "")),
            body=[["Includes", t.get("includes", "")], ["Read", _rp(t.get("read", ""))]],
            audit=(t.get("audit") or _au(t.get("read", ""))), access=acc,
            bullets=[e for e in (t.get("flows", []) + t.get("tail", []))
                     if not BAD.search(e)][:4], crit=None))

    for bucket, items in (data.get("faded") or {}).items():
        for f in items:
            marks.append(dict(
                id=f["id"], nm=f["nm"], k="faded", st="Faded / Retired",
                y=CALLY.get(f.get("call"), 40), call=f.get("call", ""),
                revived=bool(f.get("revived")), mv=movement(f["id"], "Faded / Retired"),
                sub="%s · call scored %s" % (bucket, f.get("call", "?")),
                note=f.get("ran", ""),
                body=[["Why it rolled over", _rp(f.get("why", ""))],
                      ["How the call scored", _rp(f.get("callTxt", ""))],
                      ["Revival condition", _rp(f.get("reviveIf", ""))]],
                audit=(f.get("audit") or _au(f.get("why", "")) + _au(f.get("callTxt", ""))),
                bullets=[], crit=None))

    def clean_traj(lbl, raw):
        t = (lbl or "").strip()
        if t and not BAD.search(t) and len(t) <= 46:
            return t
        head = re.split(r'[-—;:,]', t)[0].strip() if t else ""
        if head and not BAD.search(head) and len(head) <= 46:
            return head
        return (raw or "").capitalize()

    drivers = []
    for x in data.get("drivers", []):
        # oneLiner is specced as a weekly snapshot (daily-task.md). Today's data still
        # has a dated news item there, so take it only when it reads like a state: no
        # specific date, no source citation. Otherwise fall back to trajLbl, which is
        # the field that currently holds state, and to the traj enum as a last resort.
        one = (x.get("oneLiner") or "").strip()
        dated = re.search(r"\b\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                          r"|\b(19|20)\d{2}\b|\b(said|says|reported|announced|letter|filing)\b",
                          one, re.I)
        lbl = clean_traj(x.get("trajLbl"), x.get("traj"))
        if one and not BAD.search(one) and not dated and len(one) <= 180:
            snap = one
        elif len(lbl) > 18:
            snap = lbl
        else:
            snap = lbl
        rest = [p for p in [_rp(p) for p in (x.get("summary") or [])[1:]]
                if p and not BAD.search(p)]
        drivers.append(dict(
            id=x["id"], nm=x["nm"], conv=x.get("conv", 3), dir=x.get("dir", "rotational"),
            dirLbl=DIRLBL.get(x.get("dir", ""), "Rotational"), traj=x.get("traj", "flat"),
            arrow=ARROW.get(x.get("traj", "flat"), "→"), snap=snap,
            icon=_pick(DRVICON, x["id"], "activity"), master=bool(x.get("master")),
            defn=(x.get("summary") or [""])[0], drives=[d[0] for d in x.get("drives", [])],
            body=[["Where it stands", snap],
                  ["What this driver is", (x.get("summary") or [""])[0]]],
            detail=rest[:3],
            audit=(x.get("audit") or [p for p in (x.get("summary") or [])[1:] if BAD.search(p)]),
            bullets=[e for e in x.get("ev", []) if not BAD.search(e)][:4],
            sub="%s · conviction %s/5" % (DIRLBL.get(x.get("dir", ""), "Rotational"),
                                               x.get("conv", 3)),
            note="", k="driver", crit=None))

    tiles = []
    for t in data.get("state_of_play", {}).get("tiles", []):
        m = t.get("meta") or {}
        tiles.append(dict(
            lbl=t["lbl"], val=t["val"], chg=t["chg"], dir=t.get("dir", "flat"),
            hist=t.get("hist") or [], chgw=t.get("chgw") or {}, k="tile", crit=None,
            bullets=[], nm=t["lbl"], sub="%s  %s" % (t["val"], t["chg"]),
            note=t.get("chartRead", ""),
            body=[["What it is", t.get("back", "")],
                  ["Source", (m.get("source", "") or "") +
                   (" · as of " + m["as_of"] if m.get("as_of") else "")]]))

    sigs = data.get("signals", [])
    def is_ops(s):
        return bool(OPS.search(s.get("headline", "")) or OPS.search(s.get("assessment") or ""))
    win = [s for s in sigs if s.get("daysAgo", 99) <= WINDOW]
    vis = [s for s in win if not is_ops(s)]
    written = {w.get("cat"): w for w in (data.get("what_changed") or [])}

    cats = []
    for sec, n in Counter(s["sector"] for s in vis).most_common():
        rows = sorted([s for s in vis if s["sector"] == sec], key=lambda s: s["daysAgo"])
        span = max(r["daysAgo"] for r in rows)
        w = written.get(sec)
        if w and w.get("line"):
            digest, placeholder = w["line"], False
        else:
            when = "today" if span == 0 else ("yesterday" if span == 1
                                              else "the last %d days" % span)
            digest = "%d signal%s over %s" % (n, "" if n == 1 else "s", when)
            placeholder = True
        cats.append(dict(
            sec=sec, n=n, icon=_pick(SECICON, sec, "activity"), newest=rows[0]["daysAgo"],
            span=span, digest=digest, placeholder=placeholder, nm=sec.upper(), sub=digest,
            note="", k="cat", crit=None, bullets=[], body=[],
            rows=[dict(ago=r["daysAgo"], hd=r["headline"], src=r.get("src", ""),
                       sm=" ".join(r.get("summary", []))[:200]) for r in rows]))
    if all(c["placeholder"] for c in cats) and cats:
        print("WARN: no what_changed[].line in the data — What changed is showing "
              "counts, not summaries (see daily-task.md)", file=sys.stderr)

    cr = data.get("crash_risk", {})
    crash = dict(
        v=cr.get("composite"), lvl=cr.get("level", ""),
        read=_rp(cr.get("read", "")),
        deskvoice=round(desk_share(cr.get("read", "")), 2),
        buckets=[dict(nm=b["nm"], summary=_rp(b.get("summary", "")),
                      inds=[dict(nm=i["nm"], val=i["val"], status=i["status"],
                                 tr=i.get("tr", "flat"), mean=i.get("mean", ""))
                            for i in (b.get("inds") or [])])
                 for b in (cr.get("buckets") or [])])

    # The narrative is ~5-6k characters of continuous prose with a capitalised lead
    # clause opening each paragraph. Those clauses are already section headings, so the
    # panel renders it as sections rather than one wall of text, and the paragraphs that
    # are about this desk rather than about the world go to the audit disclosure.
    # The narrative is ~5-6k characters with a capitalised lead clause opening each
    # paragraph. Those clauses are already section headings, so the panel renders it as
    # sections rather than one wall of text. A paragraph moves to the audit only when
    # MOST of it is desk commentary — otherwise its desk sentences are dropped and the
    # rest is kept, because these paragraphs carry the run's actual findings.
    nar = data.get("state_of_play", {}).get("narrative", "")
    paras = [p.strip() for p in re.split(r'\n+', nar) if p.strip()]
    sections, nar_audit = [], []
    for p in paras:
        if desk_share(p) >= 0.5:
            nar_audit.append(p)
            continue
        m = re.match(r"([A-Z][A-Z0-9 ,'’/&()\u2014-]{10,150}?)\s*(?=[.:\u2014]\s|$)", p)
        head, body = ("", p)
        if m and m.group(1).upper() == m.group(1):
            head, body = m.group(1).strip(" ,"), p[m.end():].lstrip(" .:\u2014")
        body = " ".join(x for x in re.split(r'(?<=[.!?])\s+', body)
                        if not (BAD.search(x) or DESK.search(x))).strip() or body
        if head and (BAD.search(head) or DESK.search(head)):
            head = ""                              # a desk-voice heading is still desk voice
        if head:
            head = head[0] + head[1:].lower()
        sections.append({"h": head, "b": body})
    lead = _cap(sections[0]["b"]) if sections else _rp(nar)
    state = dict(lead=lead, sections=sections, audit=nar_audit[:8])

    meta = data.get("meta", {})
    return {
        "MARKS": marks, "DRIVERS": drivers, "TILES": tiles,
        "SIGNALS": [dict(ago=s["daysAgo"], sec=s["sector"], hd=s["headline"]) for s in sigs],
        "CATS": cats,
        "SIGMETA": dict(window=WINDOW, shown=len(vis), ops=len(win) - len(vis), total=len(sigs)),
        "CRASH": crash, "WINDOWS": data.get("windows", {}), "STATE": state,
        "META": dict(build=(meta.get("last_fetch") or "")[:10],
                     report=meta.get("report_date", ""),
                     reportLong=meta.get("report_date_long", ""),
                     mvwin="30 days ago"),
    }


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

    # 1) shape the view model and swap the ten data literals
    view = build_v28(data)
    for name in INJECT_V28:
        html = replace_js_literal(html, name, view[name])

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

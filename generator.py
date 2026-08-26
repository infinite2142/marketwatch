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
              "CRASH","META","WINDOWS","STATE","CHANGELOG"]

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
# V1 tracked single-name exit flags (MSTR, MU, SPCX) alongside themes. The book is
# themes now, so they are dropped from the page. The ledger keeps their history —
# the MSTR flag being scored wrong on 21 Aug is a logged correction and stays there.
# Words too generic to imply two entries are the same subject.
GENERIC = {"power","energy","infrastructure","market","markets","global","industrial",
           "policy","technology","systems","complex","assets","digital","compliance",
           "premium","payments","service","automation"}

def subject_tokens(name):
    return {w for w in re.findall(r"[a-z]{4,}", (name or "").lower())
            if w not in GENERIC}

STOCK_FLAG = re.compile(r'\(id \d+\)|exit flag|catalyst watch', re.I)
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
RADICON = {"quantum":"atom","humanoid":"cpu","robot":"cpu","shipbuilding":"ship","maritime":"ship",
           "trades":"users","labour":"shield","defensive":"shield","stablecoin":"coins",
           "crypto":"coins","privmkts":"landmark","private":"landmark","fusion":"atom",
           "nuclear":"atom","storage":"zap","batter":"zap","grid":"zap","power":"zap",
           "water":"droplet","pfas":"droplet","medicine":"flask","biotech":"flask","techbio":"flask","bio":"flask",
           "pharma":"flask","longevity":"flask","space":"sparkles","agri":"leaf","food":"leaf"}
DRVICON = {"capex":"layers","rates":"activity","energy":"zap","fiscal":"landmark",
           "reshore":"factory","supply":"shield","labour":"users","intl":"globe","geo":"alert"}

def _pick(table, key, default):
    k = (key or "").lower()
    for probe, val in table.items():
        if probe in k:
            return val
    return default

def sentences(t):
    return [x for x in re.split(r'(?<=[.!?])\s+', (t or "").strip()) if x]

def whole_sentences(t, budget, floor=190):
    """Trim on a sentence boundary, never mid-clause. Always returns at least one
    complete sentence even if it exceeds the budget — a finished thought that runs
    long reads better than an unfinished one that fits — and keeps taking sentences
    until `floor`, because this house style writes long ones and a lone short
    opener next to a full paragraph looks like a rendering fault rather than a
    summary."""
    parts = sentences(t)
    if not parts:
        return ""
    out = parts[0]
    # The floor may overshoot, and fairly far: this narrative pairs short openers
    # with 370-character sentences, so a tight ceiling leaves a 62-character section
    # that reads as broken. Deriving a summary from someone else's prose cannot do
    # better than this — daily-task.md now specs sections[].line so the run writes
    # them, and this becomes the fallback.
    ceiling = int(budget * 1.6)
    for p in parts[1:]:
        nxt = len(out) + len(p) + 1
        if len(out) >= floor:
            if nxt > budget:
                break
        elif nxt > ceiling:
            break                     # a short opener beats dragging in a 370-char sentence
        out += " " + p
    return out.strip()

def _cap_words(t, n):
    """Trim to n characters on a word boundary. The signal line is a glance, not a
    paragraph; the full set is one click away."""
    t = (t or "").strip()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(" ,;:-—") + "…"

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

def summarise(txt, cap=190):
    """One or two sentences of assessment — where a theme stands today. Falls back to
    the head of `read` until the daily writes a `glance` field of its own."""
    t = _rp(txt)
    if not t:
        return ""
    out, parts = "", re.split(r'(?<=[.!?])\s+', t)
    for p in parts:
        if out and len(out) + len(p) + 1 > cap:
            break
        out = (out + " " + p).strip()
        if len(out) >= cap * 0.55:
            break
    out = out or _cap(t)
    return _desnout(out)

# Acronyms and brands that stay upper-case when a shouted clause is calmed down.
ACRO = {"AI","US","UK","EU","G7","G20","OPEC","FOMC","NDAA","IEEPA","MASGA","HBM","NAND",
        "DRAM","ASP","ASPS","CPI","PCE","GDP","VIX","ETF","ETFS","IPO","LNG","EV","EVS",
        "SMR","SMRS","GPU","GPUS","CPU","TAM","RPO","OAS","HALEU","NVIDIA","OEM","OEMS",
        "Q1","Q2","Q3","Q4","H1","H2","YTD","M2"}
TITLE = {"MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY",
         "JANUARY","FEBRUARY","MARCH","APRIL","MAY","JUNE","JULY","AUGUST",
         "SEPTEMBER","OCTOBER","NOVEMBER","DECEMBER","CHINA","JAPAN","KOREA",
         "EUROPE","AMERICA","WASHINGTON","BERLIN","BRUSSELS","CONGRESS","FED"}

def _desnout(txt):
    """The house style opens a read with an all-caps clause. At tile size that reads as
    shouting, so the LEADING clause is sentence-cased; the rest of the passage already
    has normal casing and is left untouched."""
    if not txt:
        return txt
    m = re.match(r"^(.{0,180}?[.!?])(\s+|$)", txt)
    head, rest = (m.group(1), txt[m.end():]) if m else (txt, "")
    letters = [c for c in head if c.isalpha()]
    if not letters or sum(1 for c in letters if c.isupper()) / len(letters) < 0.7:
        return txt
    def fix(w):
        t = w.group(0)
        if t in ACRO or any(c.isdigit() for c in t):
            return t
        return t.capitalize() if t in TITLE else t.lower()
    head = re.sub(r"\b[A-Z][A-Z'’&/-]+\b", fix, head)
    head = re.sub(r"\bA\b", "a", head)          # stray article left upper by the rule above
    head = head[0].upper() + head[1:] if head else head
    return (head + (" " + rest if rest else "")).strip()

def _trim_lead(text, glance):
    """The glance is cut from the head of `read`, so printing both repeats those
    sentences. Drop the overlap. Compares on letters only, since the glance has
    been sentence-cased and the source has not."""
    if not text or not glance:
        return text
    norm = lambda x: re.sub(r"[^a-z0-9]", "", (x or "").lower())
    g = norm(glance)
    parts = re.split(r'(?<=[.!?])\s+', text)
    i = 0
    acc = ""
    while i < len(parts) and len(norm(acc)) < len(g):
        acc += (" " if acc else "") + parts[i]
        i += 1
    if norm(acc)[:len(g)] == g and i < len(parts):
        return " ".join(parts[i:]).strip()
    # the other direction: the glance often reuses read's lead clause and adds to
    # it, so read's first sentence is a prefix of the glance rather than vice versa
    if len(parts) > 1:
        first = norm(parts[0])
        if first and len(first) > 20 and g.startswith(first[:min(len(first), 60)]):
            return " ".join(parts[1:]).strip()
    return text

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

def _json_mtime():
    try:
        return datetime.datetime.fromtimestamp(
            os.path.getmtime(DATA_PATH), datetime.timezone.utc).strftime("%H:%M")
    except Exception:
        return ""

AREAS = ["Markets and risk appetite", "Policy and rates", "Trade and industrial policy",
         "Energy and commodities", "AI and the compute build", "Geopolitics and security",
         "What to watch"]
_AREA_HINTS = [
    ("What to watch",            r"\bwatch|ahead|next week|due |scheduled|calendar|reports on\b"),
    ("Policy and rates",         r"\bfed\b|fomc|rate|yield|inflation|cpi|claims|payroll|central bank|policy path"),
    ("Trade and industrial policy", r"tariff|section 23|section 33|duties|reshor|trade deal|export control|subsid"),
    ("Energy and commodities",   r"\bbrent|crude|oil\b|gas\b|gold|silver|copper|uranium|power price|commodit"),
    ("AI and the compute build", r"\bai\b|semiconduct|gpu|datacen|memory|hbm|optic|neocloud|capex|nvidia"),
    ("Geopolitics and security", r"sanction|conflict|geopolit|military|defence|defense|strait|missile|security"),
    ("Markets and risk appetite",r"s&p|nasdaq|equit|index|vix|risk-on|risk-off|breadth|rally|selloff|bitcoin"),
]

def _classify(text):
    """Bucket a paragraph into one of the fixed areas by keyword weight. Ties and
    misses fall to Markets, which is the broadest and least wrong default."""
    t = (text or "").lower()
    best, score = "Markets and risk appetite", 0
    for area, pat in _AREA_HINTS:
        n = len(re.findall(pat, t))
        if n > score:
            best, score = area, n
    return best

CHANGE_LABEL = {
    "added":     "added to the book",
    "graduated": "graduated from radar",
    "promoted":  "promoted",
    "demoted":   "demoted",
    "retired":   "retired",
    "revived":   "revived",
    "removed":   "removed",
    "deduped":   "duplicate cleared",
}

def _stage_map(d):
    """id -> (stage, name) for everything the book tracks at one revision."""
    o = {}
    for r in d.get("radar", []):
        o[r["id"]] = ("Radar", r.get("nm", r["id"]))
    for t in d.get("investible_themes", []):
        o["theme-" + t["id"]] = (THEME_STAGE_V28.get((t.get("stage") or "").lower(), "Building"),
                                 t.get("nm", t["id"]))
    for _, items in (d.get("faded") or {}).items():
        for f in items:
            o[f["id"]] = ("Faded / Retired", f.get("nm", f["id"]))
    return o

def derive_changelog(audit_index=None, limit=40):
    """When themes joined, moved or left — read out of git history rather than kept
    as a field. The data file is committed on every run, so the record already
    exists and cannot fall out of step with the book the way a hand-maintained log
    would. Costs about 0.4s across 29 revisions."""
    try:
        out = subprocess.run(["git", "log", "--format=%H %cs", "-n", str(limit),
                              "--", "market_watch_data.json"],
                             capture_output=True, text=True, check=True,
                             cwd=HERE, timeout=30).stdout.strip()
    except Exception as e:
        print("WARN: changelog unavailable (%s)" % e, file=sys.stderr)
        return []
    revs = [l.split() for l in out.split("\n") if l.strip()]
    if len(revs) < 2:
        # A shallow clone gives one revision and the walk finds nothing, which looks
        # exactly like a quiet week. Say so rather than render an empty section.
        print("WARN: change log needs history — only %d revision(s) visible. "
              "Is this a shallow checkout? (deploy.yml sets fetch-depth)" % len(revs),
              file=sys.stderr)
    if len(revs) < 2:
        # A shallow clone gives one revision and the walk finds nothing, which looks
        # exactly like a quiet week. Say so instead of rendering an empty section.
        print("WARN: change log needs history — only %d revision(s) visible. "
              "Is this a shallow checkout? (deploy.yml sets fetch-depth)" % len(revs),
              file=sys.stderr)
    revs.reverse()                                    # oldest first
    order = {s: i for i, s in enumerate(STAGE_ORDER_V28)}
    snaps = []
    for sha, date in revs:
        try:
            raw = subprocess.run(["git", "show", sha + ":market_watch_data.json"],
                                 capture_output=True, text=True, check=True,
                                 cwd=HERE, timeout=30).stdout
            snaps.append((date, _stage_map(json.loads(raw))))
        except Exception:
            continue
    by_date = {}
    for i in range(1, len(snaps)):
        date, cur = snaps[i]
        _, prev = snaps[i - 1]
        evs = []
        for tid, (stage, nm) in cur.items():
            if tid not in prev:
                evs.append(dict(id=tid, nm=nm, kind="added", to=stage))
                continue
            was = prev[tid][0]
            if was == stage:
                continue
            if stage == "Faded / Retired":
                kind = "retired"
            elif was == "Faded / Retired":
                kind = "revived"
            elif was == "Radar":
                kind = "graduated"
            else:
                kind = "promoted" if order.get(stage, 0) > order.get(was, 0) else "demoted"
            evs.append(dict(id=tid, nm=nm, kind=kind, frm=was, to=stage))
        for tid, (stage, nm) in prev.items():
            if tid in cur:
                continue
            # "Fusion power removed" is misleading when the subject is still live
            # somewhere else — that entry was a stale duplicate, not a retirement.
            twin = next((n for i, (_, n) in cur.items()
                         if i != tid and subject_tokens(n) & subject_tokens(nm)), None)
            evs.append(dict(id=tid, nm=nm, kind="deduped" if twin else "removed",
                            frm=stage, twin=twin or ""))
        # The log is for the book moving, not for tidying up after it. Two classes
        # of event are housekeeping and are dropped: anything that is a single-name
        # exit flag rather than a theme (MSTR, MU, SPCX — already hidden from the
        # page), and a duplicate being cleared, which is a correction to the record
        # rather than a change in what is tracked.
        evs = [e for e in evs
               if not STOCK_FLAG.search(e.get("nm", "")) and e["kind"] != "deduped"]
        if evs:
            by_date.setdefault(date, []).extend(evs)
    # Why it happened, not just that it did: the daily writes dated `audit` notes,
    # so an event on 23 August can carry that record's note from the same day.
    ai = audit_index or {}
    for d, evs in by_date.items():
        for e in evs:
            notes = [a for a in ai.get(e["id"], [])
                     if isinstance(a, dict) and a.get("date") == d and a.get("note")]
            if not notes:                       # else the newest note at or before it
                earlier = sorted([a for a in ai.get(e["id"], [])
                                  if isinstance(a, dict) and a.get("note")
                                  and (a.get("date") or "") <= d],
                                 key=lambda a: a.get("date") or "")
                notes = earlier[-1:]
            e["why"] = [a["note"] for a in notes][:2]
    log = [dict(date=d, events=by_date[d]) for d in sorted(by_date, reverse=True)]
    for entry in log:                                 # stable, readable ordering
        entry["events"].sort(key=lambda e: (list(CHANGE_LABEL).index(e["kind"]), e["nm"]))
    return log

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
            sub="Pre-investible", note=r.get("eta", ""),
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
            sub=("first seen " + t["first_seen"]) if t.get("first_seen") else "",
            note="",   # the runway size line already shows now -> proj; see the panel
            runway=t.get("runway"), now=t.get("now", ""), proj=t.get("proj", ""),
            hz=t.get("hz", ""), sizeUnit=t.get("sizeUnit", ""),
            nowEst=bool(t.get("nowEst")), projEst=bool(t.get("projEst")),
            glance=(t.get("glance") or summarise(t.get("read", ""))),
            body=[["Includes", t.get("includes", "")],
                  ["Read", _desnout(_trim_lead(_rp(t.get("read", "")),
                                      t.get("glance") or summarise(t.get("read", ""))))]],
            audit=(t.get("audit") or _au(t.get("read", ""))), access=acc,
            bullets=[e for e in (t.get("flows", []) + t.get("tail", []))
                     if not BAD.search(e)][:4], crit=None))

    # A theme lives at exactly one stage. If a subject is live on the radar or in the
    # book, a faded copy of it is stale state, not history — the history belongs to the
    # ledger. Today: "Fusion power" was both a 3/5 radar entry and a dormant one.
    live_subjects = [subject_tokens(m["nm"]) for m in marks]
    dropped_flags = dropped_dupes = revived_stuck = 0
    for bucket, items in (data.get("faded") or {}).items():
        for f in items:
            if STOCK_FLAG.search(f.get("nm", "")):
                dropped_flags += 1
                continue
            fs = subject_tokens(f.get("nm", ""))
            if fs and any(fs & ls for ls in live_subjects):
                dropped_dupes += 1
                continue
            if f.get("revived"):
                revived_stuck += 1
            marks.append(dict(
                id=f["id"], nm=f["nm"], k="faded", st="Faded / Retired",
                y=CALLY.get(f.get("call"), 40), call=f.get("call", ""),
                revived=bool(f.get("revived")), mv=movement(f["id"], "Faded / Retired"),
                sub=f.get("ran", ""),
                note=f.get("ran", ""),
                body=[["Why it rolled over", _desnout(_rp(f.get("why", "")))],
                      ["How the call scored", _desnout(_rp(f.get("callTxt", "")))],
                      ["Revival condition", _desnout(_rp(f.get("reviveIf", "")))]],
                audit=(f.get("audit") or _au(f.get("why", "")) + _au(f.get("callTxt", ""))),
                bullets=[], crit=None))

    if dropped_flags:
        print("note: %d single-name exit flag(s) hidden from the page (themes only); "
              "their history stays in the ledger" % dropped_flags, file=sys.stderr)
    if dropped_dupes:
        print("note: %d faded entr(y/ies) hidden because the same subject is live at an "
              "earlier stage — a theme belongs to one stage" % dropped_dupes, file=sys.stderr)
    if revived_stuck:
        print("WARN: %d faded entr(y/ies) marked revived but still in Faded — a revival is "
              "a move back to an earlier stage, not a label" % revived_stuck, file=sys.stderr)

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
        # oneLiner is specced as a weekly snapshot (daily-task.md) and the daily now
        # writes one. An earlier guard here rejected any snapshot citing a date, which
        # was right when the field held news headlines and wrong now: "counter-tariffs
        # dated 8 September" is a state, not a headline, and rejecting it fell through
        # a contaminated trajLbl to the bare enum "Up".
        one = (x.get("oneLiner") or "").strip()
        lbl = clean_traj(x.get("trajLbl"), x.get("traj"))
        if one and not BAD.search(one):
            snap = one
        elif len(lbl) > 18:
            snap = lbl
        else:
            snap = ""          # better empty than a trajectory enum the arrow already shows
        rest = [p for p in [_rp(p) for p in (x.get("summary") or [])[1:]]
                if p and not BAD.search(p)]
        drivers.append(dict(
            id=x["id"], nm=x["nm"], conv=x.get("conv", 3), dir=x.get("dir", "rotational"),
            dirLbl=DIRLBL.get(x.get("dir", ""), "Rotational"), traj=x.get("traj", "flat"),
            arrow=ARROW.get(x.get("traj", "flat"), "→"), snap=snap,
            icon=_pick(DRVICON, x["id"], "activity"), master=bool(x.get("master")),
            defn=(x.get("summary") or [""])[0], drives=[d[0] for d in x.get("drives", [])],
            # the panel's graphical row already carries `snap`; repeating it as a
            # labelled paragraph is the same duplication as the old "At a glance"
            body=[["What this driver is", (x.get("summary") or [""])[0]]],
            detail=rest[:3],
            audit=(x.get("audit") or [p for p in (x.get("summary") or [])[1:] if BAD.search(p)]),
            bullets=[e for e in x.get("ev", []) if not BAD.search(e)][:4],
            sub="",
            note="", k="driver", crit=None))

    tiles = []
    for t in data.get("state_of_play", {}).get("tiles", []):
        m = t.get("meta") or {}
        tiles.append(dict(
            lbl=t["lbl"], val=t["val"], chg=t["chg"], dir=t.get("dir", "flat"),
            hist=t.get("hist") or [], chgw=t.get("chgw") or {}, k="tile", crit=None,
            bullets=[], nm=t["lbl"],
            sub=("as of " + m["as_of"]) if m.get("as_of") else "",
            asOf=m.get("as_of", ""),
            note=t.get("chartRead", ""),
            body=[["What it is", t.get("back", "")],
                  ["Source", (m.get("source", "") or "") +
                   (" · as of " + m["as_of"] if m.get("as_of") else "")]]))

    sigs = data.get("signals", [])
    def is_ops(s):
        return bool(OPS.search(s.get("headline", "")) or OPS.search(s.get("assessment") or ""))
    driver_ids = {d.get("id") for d in data.get("drivers", [])}
    win = [s for s in sigs if s.get("daysAgo", 99) <= WINDOW]
    # Driver-level categories (rates, geo, intl…) have their own section; this list is
    # the theme-level tape, which is what "signals" meant before the rollup.
    vis = [s for s in win if not is_ops(s) and s.get("sector") not in driver_ids]
    drv_cats = len({s["sector"] for s in win if s.get("sector") in driver_ids})
    written = {w.get("cat"): w for w in (data.get("what_changed") or [])}

    cats = []
    MAXCATS = 10
    for sec, n in Counter(s["sector"] for s in vis).most_common(MAXCATS):
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
            span=span, digest=digest, placeholder=placeholder, nm=sec.upper(),
            sub="%d signal%s · newest %s" % (n, "" if n == 1 else "s",
                 "today" if rows[0]["daysAgo"] == 0 else "%dd ago" % rows[0]["daysAgo"]),
            note="", k="cat", crit=None, bullets=[], body=[],
            rows=[dict(ago=r["daysAgo"], hd=r["headline"], src=r.get("src", ""),
                       sm=" ".join(r.get("summary", []))[:200]) for r in rows]))
    if all(c["placeholder"] for c in cats) and cats:
        print("WARN: no what_changed[].line in the data — What changed is showing "
              "counts, not summaries (see daily-task.md)", file=sys.stderr)

    cr = data.get("crash_risk", {})
    cm = cr.get("composite_meta") or {}
    method = ("A blended 0-100 judgement over the seven buckets below, each scored from its own "
              "indicators. It flags fragility, not a crash date. "
              "Recomputed when the last computation is more than 7 days old — age-triggered, so a "
              "missed run cannot strand it.")
    crash = dict(
        method=method,
        asOf=cm.get("as_of", cr.get("as_of", "")),
        cadence=cm.get("cadence") or cr.get("cadence", ""),
        estimate=bool(cm.get("estimate", True)),
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
    # State of play is bucketed into a FIXED set of areas rather than whatever
    # paragraphs the narrative happened to have, so the sidebar has the same shape
    # every day and a reader learns where to look. Each is trimmed hard: the full
    # narrative runs 5-6k characters and almost nobody reads that on a dashboard.
    nar = data.get("state_of_play", {}).get("narrative", "")
    written = data.get("state_of_play", {}).get("sections")
    sections, nar_audit = [], []
    if written:
        for w in written:
            sections.append({"h": w.get("area", ""),
                             "b": whole_sentences(_rp(w.get("line", "")), 300, 150)})
    else:
        paras = [p.strip() for p in re.split(r'\n+', nar) if p.strip()]
        for p in paras:
            if desk_share(p) >= 0.5:
                nar_audit.append(p)
                continue
            body = " ".join(x for x in re.split(r'(?<=[.!?])\s+', p)
                            if not (BAD.search(x) or DESK.search(x))).strip() or p
            area = _classify(body)
            sections.append({"h": area, "b": _desnout(body)})
        merged = {}
        for sec in sections:                      # one entry per area, in fixed order
            merged.setdefault(sec["h"], []).append(sec["b"])
        sections = []
        for a in AREAS:
            if a not in merged:
                continue
            full = " ".join(merged[a]).strip()
            shown = whole_sentences(full, 300, 150)
            rest = full[len(shown):].strip()
            sections.append({"h": a, "b": shown, "more": rest})
    # The page's box is a summary of the day, not the opening of whichever section
    # happened to sort first. Prefer one the daily wrote; otherwise take the whole
    # opening sentences of the narrative, which is where the house style puts the
    # top-line finding.
    written_sum = (data.get("state_of_play", {}) or {}).get("summary")
    if written_sum:
        lead = whole_sentences(_rp(written_sum), 320, 160)
    else:
        first = next((p for p in re.split(r'\n+', nar) if p.strip()
                      and desk_share(p) < 0.5), "")
        lead = whole_sentences(_desnout(_rp(first)), 320, 160) or _rp(nar)
    # The summary is cut from the narrative's opening paragraph, which also lands in
    # one of the areas — so that section would repeat it verbatim. Drop the overlap,
    # the same way a theme's `read` drops what its `glance` already carries.
    if lead:
        lead_set = {re.sub(r"[^a-z0-9]", "", x.lower()) for x in sentences(lead)}
        for sec in sections:
            keep = [x for x in sentences(sec["b"])
                    if re.sub(r"[^a-z0-9]", "", x.lower()) not in lead_set]
            if len(keep) != len(sentences(sec["b"])):
                sec["b"] = " ".join(keep).strip() or whole_sentences(sec.get("more", ""), 300, 150)
                break
    sections = [x for x in sections if x["b"].strip()]
    state = dict(lead=lead, summary=lead, sections=sections, audit=nar_audit[:8])

    meta = data.get("meta", {})
    return {
        "MARKS": marks, "DRIVERS": drivers, "TILES": tiles,
        "SIGNALS": [dict(ago=s["daysAgo"], sec=s["sector"], hd=s["headline"]) for s in sigs],
        "CATS": cats,
        "SIGMETA": dict(window=WINDOW, shown=sum(c["n"] for c in cats),
                        cats=len(cats), ops=sum(1 for s in win if is_ops(s)),
                        drvcats=drv_cats, total=len(sigs)),
        "CRASH": crash, "WINDOWS": data.get("windows", {}), "STATE": state,
        "CHANGELOG": derive_changelog({m["id"]: m.get("audit") or [] for m in marks}),
        "META": dict(build=(meta.get("last_fetch") or "")[:10],
                     buildTime=(meta.get("last_fetch") or "")[11:16],
                     report=meta.get("report_date", ""),
                     # the daily has no report_time field yet (specced); until it does,
                     # the data file's own mtime is when the analysis last wrote it
                     reportTime=meta.get("report_time") or _json_mtime(),
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

#!/usr/bin/env python3
"""
fetch_data.py -- Market Watch numeric fetcher (Phase 2b)

Pulls the cleanly-sourceable numeric metrics identified in the Phase 2a report
and writes them into market_watch_data.json BEFORE generator.py runs. It updates
only the numeric value/as_of fields it has a clean free feed for; narrative text,
editorial estimates and any metric without a clean feed are left untouched.

Sources (all free, no API key):
  * FRED CSV       fredgraph.csv?id=<SERIES>   -- DGS10, VIXCLS, BAMLH0A0HYM2,
                   BAMLC0A0CM, T10Y2Y, NFCI, SAHMREALTIME, ICSA, DEXUSEU,
                   BAMLEMHBHYCRPIOAS
  * Stooq CSV      stooq.com/q/d/l/?s=<sym>&i=d -- index/price history
  * Yahoo chart    query1.finance.yahoo.com/v8/finance/chart/<sym> -- fallback

Metrics covered (18 cleanly-sourceable, per Phase 2a report):
  Tiles: S&P 500, US 10Y, Gold, Bitcoin, DXY, VIX, Brent, FTSE 100, Nikkei 225,
         MSCI EM (EEM proxy), EUR/USD, (S&P/Nasdaq/Dow price feeds also power stocks)
  Crash indicators (numeric, clean feed only): US HY OAS (BAMLH0A0HYM2),
         IG OAS (BAMLC0A0CM), VIX (VIXCLS), Chicago Fed NFCI (NFCI),
         EM $-credit (BAMLEMHBHYCRPIOAS), DXY funding/stress (dx.f)
  Stock prices: every ticker in stocks.segments[*].rows and stocks.watchlist.

Robust by design: each fetch is isolated in try/except. If a fetch fails, the
PRIOR value is kept and the field is marked stale (meta.stale = true, with a note)
rather than crashing the run. A single bad feed never blocks the rest.

NOTE: this script cannot be exercised in the Cowork sandbox -- FRED/Stooq/Yahoo
are blocked there by the egress allowlist (verified in the Phase 2a spike). It is
written for GitHub Actions, whose runners have open outbound network, and is
invoked there by .github/workflows/deploy.yml.
"""

import json
import os
import io
import csv
import sys
import time
import datetime

try:
    import requests
except ImportError:  # pragma: no cover - requests is installed in the workflow
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "market_watch_data.json")

TODAY = datetime.date.today().isoformat()
UA = {"User-Agent": "Mozilla/5.0 (MarketWatch fetch_data.py; +https://github.com/infinite2142/MarketWatch)"}
TIMEOUT = 20
RETRIES = 3


# --------------------------------------------------------------------------- #
# Low-level HTTP with retry
# --------------------------------------------------------------------------- #
def _get(url):
    """GET with retries. Returns response text, or raises on final failure."""
    if requests is None:
        raise RuntimeError("requests not available")
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            if r.status_code == 200 and r.text:
                return r.text
            last = "HTTP %s" % r.status_code
        except Exception as e:  # noqa: BLE001 - want to retry any network error
            last = str(e)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("GET failed for %s: %s" % (url, last))


# --------------------------------------------------------------------------- #
# Feed fetchers -- each returns (latest_value: float, prev_value: float|None,
# as_of: str). prev_value enables day-change; None when unavailable.
# --------------------------------------------------------------------------- #
def fetch_fred(series):
    """FRED full-history CSV. Column 2 is the value; '.' marks missing."""
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s" % series
    text = _get(url)
    rows = list(csv.reader(io.StringIO(text)))
    clean = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        date, val = row[0].strip(), row[1].strip()
        if val in ("", "."):
            continue
        try:
            clean.append((date, float(val)))
        except ValueError:
            continue
    if not clean:
        raise RuntimeError("FRED %s returned no numeric observations" % series)
    latest_date, latest = clean[-1]
    prev = clean[-2][1] if len(clean) >= 2 else None
    _SERIES["closes"] = [c for _, c in clean[-HIST_KEEP:]]
    return latest, prev, latest_date


# The feeds return years of daily closes; we used to keep only the last two rows.
# _SERIES holds the full series from whichever fetcher last succeeded, so the tile
# loop can compute real window changes and a real sparkline from the same data.
WINDOW_DAYS = {"1W": 5, "1M": 22, "3M": 65, "6M": 125, "1Y": 250}
HIST_KEEP = 260
_SERIES = {}


def fetch_stooq(symbol):
    """Stooq daily history CSV: Date,Open,High,Low,Close,Volume."""
    url = "https://stooq.com/q/d/l/?s=%s&i=d" % symbol
    text = _get(url)
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0][0].lower() != "date":
        raise RuntimeError("Stooq %s: unexpected payload" % symbol)
    clean = []
    for row in rows[1:]:
        if len(row) < 5:
            continue
        try:
            clean.append((row[0], float(row[4])))
        except (ValueError, IndexError):
            continue
    if not clean:
        raise RuntimeError("Stooq %s returned no closes" % symbol)
    latest_date, latest = clean[-1]
    prev = clean[-2][1] if len(clean) >= 2 else None
    return latest, prev, latest_date


def fetch_yahoo(symbol):
    """Yahoo chart JSON, two years of daily bars. Fallback for symbols Stooq lacks."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
           "?range=2y&interval=1d" % symbol)
    text = _get(url)
    data = json.loads(text)
    result = data["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    closes = [c for c in closes if c is not None]
    if not closes:
        raise RuntimeError("Yahoo %s returned no closes" % symbol)
    ts = result["timestamp"][-1]
    as_of = datetime.datetime.utcfromtimestamp(ts).date().isoformat()
    latest = float(closes[-1])
    prev = float(closes[-2]) if len(closes) >= 2 else None
    _SERIES["closes"] = [float(c) for c in closes[-HIST_KEEP:]]
    return latest, prev, as_of


def fetch_chain(*fetchers):
    """Try each (fn, arg) in order; return the first success. Raise if all fail."""
    errors = []
    _SERIES.pop("closes", None)
    for fn, arg in fetchers:
        try:
            return fn(arg)
        except Exception as e:  # noqa: BLE001
            errors.append("%s(%s): %s" % (fn.__name__, arg, e))
    raise RuntimeError(" | ".join(errors))


# -------------------------------------------------------------------------- #
# Display-string formatting -- values in the JSON are formatted strings
# ("7,757.64", "4.65%", "$4,329", "271 bp", "~560"). We detect the shape of the
# existing string and re-apply it to the freshly fetched number so the page keeps
# its look. Values that are words ("positive", "Contango") are never overwritten.
# --------------------------------------------------------------------------- #
def parse_shape(s):
    """Return (prefix, suffix, thousands, decimals, is_numeric)."""
    t = str(s).strip()
    if t.startswith("~"):
        t = t[1:].strip()
    prefix = ""
    if t.startswith("$"):
        prefix, t = "$", t[1:].strip()
    suffix = ""
    for suf in (" bp", "bp", "%"):
        if t.endswith(suf):
            suffix, t = suf, t[:-len(suf)].strip()
            break
    core = t.replace(",", "").lstrip("+-")
    is_numeric = core.replace(".", "", 1).isdigit() and core != ""
    thousands = "," in t
    decimals = 0
    if "." in core:
        decimals = len(core.split(".")[1])
    return prefix, suffix, thousands, decimals, is_numeric


def fmt_value(old, number):
    """Format `number` using the shape of the existing `old` string."""
    prefix, suffix, thousands, decimals, _ = parse_shape(old)
    num = round(number, decimals) if decimals else int(round(number))
    if thousands:
        body = "{:,.{d}f}".format(number, d=decimals)
    else:
        body = "{:.{d}f}".format(number, d=decimals)
    return "%s%s%s" % (prefix, body, suffix)


def fmt_change(old_chg, latest, prev):
    """Rebuild the day-change string in the same unit as the existing one."""
    if prev is None or prev == 0:
        return old_chg, None
    delta = latest - prev
    unit = str(old_chg)
    if "bp" in unit:
        bp = int(round(delta * 100.0))  # yields are quoted in %, 1bp = 0.01%
        return ("%+d bp" % bp), delta
    if "%" in unit:
        pct = (delta / prev) * 100.0
        return ("%+.2f%%" % pct), delta
    # plain point change
    return ("%+.2f" % delta), delta


def dir_from(delta):
    if delta is None:
        return None
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


# --------------------------------------------------------------------------- #
# Metric -> feed maps
# --------------------------------------------------------------------------- #
# Tiles are matched by their `lbl`. Each entry is a chain of (fetcher, arg).
TILE_FEEDS = {
    "S&P 500":        [(fetch_stooq, "^spx"),  (fetch_yahoo, "^GSPC")],
    "US 10Y":         [(fetch_fred, "DGS10")],
    "Gold":           [(fetch_stooq, "xauusd"), (fetch_yahoo, "GC=F")],
    "Bitcoin":        [(fetch_yahoo, "BTC-USD"), (fetch_stooq, "btcusd")],
    "US Dollar (DXY)":[(fetch_stooq, "dx.f"),  (fetch_yahoo, "DX-Y.NYB")],
    "VIX":            [(fetch_fred, "VIXCLS"), (fetch_yahoo, "^VIX")],
    "Brent":          [(fetch_stooq, "cb.f"),  (fetch_yahoo, "BZ=F")],
    "STOXX 600":      [(fetch_yahoo, "^STOXX")],
    "FTSE 100":       [(fetch_stooq, "^ftm"),  (fetch_yahoo, "^FTSE")],
    "Nikkei 225":     [(fetch_stooq, "^nkx"),  (fetch_yahoo, "^N225")],
    "MSCI EM":        [(fetch_yahoo, "EEM")],   # ETF proxy for the index
    "EUR/USD":        [(fetch_fred, "DEXUSEU"), (fetch_yahoo, "EURUSD=X")],
}

# Crash-risk indicators matched by `nm`. Only numeric, clean-feed metrics here.
# `scale` converts the raw feed number into the units the displayed val uses
# (FRED OAS series are in %, the page shows basis points).
CRASH_FEEDS = {
    "US HY OAS spread":  {"chain": [(fetch_fred, "BAMLH0A0HYM2")], "scale": 100.0},
    "IG OAS spread":     {"chain": [(fetch_fred, "BAMLC0A0CM")],   "scale": 100.0},
    "VIX":               {"chain": [(fetch_fred, "VIXCLS"), (fetch_yahoo, "^VIX")], "scale": 1.0},
    "Chicago Fed NFCI":  {"chain": [(fetch_fred, "NFCI")],         "scale": 1.0},
    "EM $-credit spread":{"chain": [(fetch_fred, "BAMLEMHBHYCRPIOAS")], "scale": 100.0},
    "USD (DXY) funding": {"chain": [(fetch_stooq, "dx.f"), (fetch_yahoo, "DX-Y.NYB")], "scale": 1.0},
    "USD (DXY) stress":  {"chain": [(fetch_stooq, "dx.f"), (fetch_yahoo, "DX-Y.NYB")], "scale": 1.0},
}


def stooq_symbols_for(ticker):
    """Map an equity ticker to a Stooq symbol (+ Yahoo fallback symbol)."""
    t = ticker.strip()
    if "." in t:  # foreign listing, e.g. BA.L, RR.L, RHM.DE
        exch = t.rsplit(".", 1)[1].upper()
        root = t.rsplit(".", 1)[0].lower()
        smap = {"L": "uk", "DE": "de", "PA": "fr", "MI": "it", "AS": "nl", "SW": "ch"}
        stooq = "%s.%s" % (root, smap.get(exch, exch.lower()))
        return [(fetch_stooq, stooq), (fetch_yahoo, t)]
    return [(fetch_stooq, "%s.us" % t.lower()), (fetch_yahoo, t)]


# --------------------------------------------------------------------------- #
# Update helpers -- each mutates the data in place and records outcome.
# --------------------------------------------------------------------------- #
def mark_stale(meta, why):
    meta["stale"] = True
    meta["stale_reason"] = why
    meta["stale_since"] = TODAY


def clear_stale(meta):
    for k in ("stale", "stale_reason", "stale_since"):
        meta.pop(k, None)


def update_tiles(data, report):
    for tile in data.get("state_of_play", {}).get("tiles", []):
        lbl = tile.get("lbl")
        chain = TILE_FEEDS.get(lbl)
        meta = tile.setdefault("meta", {})
        if not chain:
            continue  # no clean feed -> leave editorial value untouched
        try:
            latest, prev, as_of = fetch_chain(*chain)
            tile["val"] = fmt_value(tile.get("val", ""), latest)
            new_chg, delta = fmt_change(tile.get("chg", ""), latest, prev)
            tile["chg"] = new_chg
            d = dir_from(delta)
            if d:
                tile["dir"] = d
            meta["as_of"] = as_of or TODAY
            meta["estimate"] = False  # now a real print
            clear_stale(meta)
            # Real history -> real sparkline and real per-window change. Both are
            # written together, so the page never shows a true delta beside a
            # synthetic line. Absent history leaves both fields off and the page
            # falls back to the 1-day change.
            closes = _SERIES.get("closes") or []
            if len(closes) >= 30:
                tile["hist"] = [round(float(c), 6) for c in closes]
                chgw = {}
                for wk, back in WINDOW_DAYS.items():
                    if len(closes) > back:
                        wchg, _d = fmt_change(tile.get("chg", ""), latest, closes[-1 - back])
                        chgw[wk] = wchg
                if chgw:
                    tile["chgw"] = chgw
                meta["hist_points"] = len(closes)
            report["ok"].append("tile:%s = %s" % (lbl, tile["val"]))
        except Exception as e:  # noqa: BLE001
            mark_stale(meta, "fetch failed %s: %s" % (TODAY, e))
            report["stale"].append("tile:%s (%s)" % (lbl, e))


def update_crash(data, report):
    for bucket in data.get("crash_risk", {}).get("buckets", []):
        for ind in bucket.get("inds", []):
            nm = ind.get("nm")
            cfg = CRASH_FEEDS.get(nm)
            if not cfg:
                continue
            meta = ind.setdefault("meta", {})
            _, _, _, _, is_num = parse_shape(ind.get("val", ""))
            if not is_num:
                # displayed value is a word (e.g. "positive"); don't overwrite
                continue
            try:
                latest, prev, as_of = fetch_chain(*cfg["chain"])
                scaled = latest * cfg["scale"]
                ind["val"] = fmt_value(ind.get("val", ""), scaled)
                meta["as_of"] = as_of or TODAY
                meta["estimate"] = False
                clear_stale(meta)
                report["ok"].append("crash:%s = %s" % (nm, ind["val"]))
            except Exception as e:  # noqa: BLE001
                mark_stale(meta, "fetch failed %s: %s" % (TODAY, e))
                report["stale"].append("crash:%s (%s)" % (nm, e))


def update_stocks(data, report):
    cache = {}  # symbol-chain key -> latest price, so repeated tickers hit once

    def price_for(ticker):
        key = ticker.upper()
        if key in cache:
            return cache[key]
        latest, _, _ = fetch_chain(*stooq_symbols_for(ticker))
        cache[key] = latest
        return latest

    stocks = data.get("stocks", {})

    for seg in stocks.get("segments", []):
        for row in seg.get("rows", []):
            if not isinstance(row, list) or len(row) < 3:
                continue
            ticker = row[0]
            try:
                p = price_for(ticker)
                row[2] = round(p, 2) if p < 100 else round(p)
                report["ok"].append("stock:%s = %s" % (ticker, row[2]))
            except Exception as e:  # noqa: BLE001
                report["stale"].append("stock:%s (%s)" % (ticker, e))

    for w in stocks.get("watchlist", []):
        ticker = w.get("tk")
        if not ticker:
            continue
        try:
            p = price_for(ticker)
            w["price"] = round(p, 2) if p < 100 else round(p)
            report["ok"].append("watch:%s = %s" % (ticker, w["price"]))
        except Exception as e:  # noqa: BLE001
            report["stale"].append("watch:%s (%s)" % (ticker, e))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    if not os.path.exists(DATA_PATH):
        print("ERROR: %s not found" % DATA_PATH, file=sys.stderr)
        return 1

    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    report = {"ok": [], "stale": []}

    # Each section is independent; a failure inside one never aborts the others.
    for fn in (update_tiles, update_crash, update_stocks):
        try:
            fn(data, report)
        except Exception as e:  # noqa: BLE001
            print("WARN: %s raised %s" % (fn.__name__, e), file=sys.stderr)

    # Stamp the fetch time on meta (as_of already set per-field).
    meta = data.setdefault("meta", {})
    meta["last_fetch"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("fetch_data.py: %d updated, %d stale/kept-prior"
          % (len(report["ok"]), len(report["stale"])))
    for line in report["stale"]:
        print("  STALE  " + line)
    # Non-zero exit only if literally nothing succeeded (likely total network
    # outage) -- lets the workflow still publish the prior good page.
    return 0 if report["ok"] else 0


if __name__ == "__main__":
    sys.exit(main())

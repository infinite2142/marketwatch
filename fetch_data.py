#!/usr/bin/env python3
"""
fetch_data.py -- Market Watch numeric fetcher (Phase 2b)

Pulls the cleanly-sourceable numeric metrics identified in the Phase 2a report
and writes them into market_watch_data.json BEFORE generator.py runs. It updates
only the numeric value/as_of fields it has a clean free feed for; narrative text,
editorial estimates and any metric without a clean feed are left untouched.

Sources (all free, no API key):
  * Yahoo chart    query1.finance.yahoo.com/v8/finance/chart/<sym> -- primary for
                   every price, index and FX series; same-day, and the only source
                   that carries FTSE, STOXX, DXY, gold and individual equities
  * FRED CSV       fredgraph.csv?id=<SERIES>   -- the credit/macro series that have
                   no market feed (BAMLH0A0HYM2, BAMLC0A0CM, T10Y2Y, NFCI,
                   SAHMREALTIME, ICSA, BAMLEMHBHYCRPIOAS), plus the independent
                   second source for S&P, 10Y, VIX, Brent, Nikkei and EUR/USD
  * CoinGecko      api.coingecko.com/api/v3/simple/price -- Bitcoin fallback
  * Frankfurter    api.frankfurter.app/latest -- ECB reference rates, EUR/USD fallback

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

NOTE: this script cannot be exercised in the Cowork sandbox -- the market feeds
are blocked there by the egress allowlist (verified in the Phase 2a spike). It
runs on the Mac mini, invoked by marketwatch-core/daily-update.sh ahead of the
analysis step. .github/workflows/deploy.yml no longer runs it: the workflow does
not write to the repo, because a second writer only causes push conflicts.

Uses only the standard library. See _get() for why.
"""

import json
import os
import io
import csv
import sys
import time
import datetime
import urllib.error
import urllib.request

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
    """GET with retries. Returns response text, or raises on final failure.

    Deliberately uses urllib from the standard library rather than requests.
    This script is invoked by launchd, by hand from a login shell, and by CI --
    and those do not all resolve to the same python3. A third-party dependency
    turned that into a total fetch failure whenever the chosen interpreter
    lacked it: every tile carried `requests not available` and went stale for
    the 12-17 Aug 2026 stretch. The stdlib is present in every interpreter, so
    this class of failure cannot recur.
    """
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                if r.status == 200:
                    text = r.read().decode("utf-8", "replace")
                    if text:
                        return text
                last = "HTTP %s" % r.status
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


# Stooq was removed on 2026-08-17. It now answers /q/d/l/ with a JavaScript
# proof-of-work bot check (796 bytes of HTML, HTTP 200) instead of CSV, on both
# stooq.com and stooq.pl -- curl gets the same page, so this is not a client
# problem. It had been the FIRST link in the chain for six tiles, both DXY
# crash indicators and every equity ticker, so every one of those was paying a
# wasted request and silently running on its fallback. Solving the challenge is
# not the answer: it is an anti-bot gate whose parameters rotate, so a solver
# would break often and quietly. Yahoo is the primary now, with FRED as the
# independent second source wherever a series exists.


def fetch_coingecko(coin):
    """CoinGecko simple/price. Keyless, independent of Yahoo. Spot + 24h change
    only -- no history, so it yields no sparkline series and is a fallback."""
    url = ("https://api.coingecko.com/api/v3/simple/price"
           "?ids=%s&vs_currencies=usd&include_24hr_change=true" % coin)
    data = json.loads(_get(url))
    if coin not in data or "usd" not in data[coin]:
        raise RuntimeError("CoinGecko %s: unexpected payload" % coin)
    latest = float(data[coin]["usd"])
    chg = data[coin].get("usd_24h_change")
    prev = latest / (1.0 + float(chg) / 100.0) if chg not in (None, "") else None
    return latest, prev, TODAY


def fetch_frankfurter(pair):
    """Frankfurter (ECB reference rates). Keyless. `pair` is like 'EUR/USD'."""
    base, quote = pair.split("/")
    data = json.loads(_get("https://api.frankfurter.app/latest?from=%s&to=%s" % (base, quote)))
    rate = (data.get("rates") or {}).get(quote)
    if rate is None:
        raise RuntimeError("Frankfurter %s: no rate in payload" % pair)
    return float(rate), None, data.get("date") or TODAY


def fetch_yahoo(symbol):
    """Yahoo chart JSON, two years of daily bars. The primary feed for prices."""
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
# Ordering rule: a same-day market feed first, an independent source second.
# FRED is authoritative but publishes on a lag (1-3 business days, and DEXUSEU
# was ten days behind on 2026-08-17), so where it used to lead it was pinning
# tiles to last week's number on an otherwise successful run. It is the right
# fallback for exactly that reason: a three-day-old real value beats a stale flag.
TILE_FEEDS = {
    "S&P 500":        [(fetch_yahoo, "^GSPC"),    (fetch_fred, "SP500")],
    "US 10Y":         [(fetch_yahoo, "^TNX"),     (fetch_fred, "DGS10")],
    "Gold":           [(fetch_yahoo, "GC=F")],    # no keyless second source found
    "Bitcoin":        [(fetch_yahoo, "BTC-USD"),  (fetch_coingecko, "bitcoin")],
    "US Dollar (DXY)":[(fetch_yahoo, "DX-Y.NYB")],  # ICE DXY; FRED's DTWEXBGS is a
                                                    # different index, not a fallback
    "VIX":            [(fetch_yahoo, "^VIX"),     (fetch_fred, "VIXCLS")],
    "Brent":          [(fetch_yahoo, "BZ=F"),     (fetch_fred, "DCOILBRENTEU")],
    "STOXX 600":      [(fetch_yahoo, "^STOXX")],
    "FTSE 100":       [(fetch_yahoo, "^FTSE")],   # FRED carries no FTSE series
    "Nikkei 225":     [(fetch_yahoo, "^N225"),    (fetch_fred, "NIKKEI225")],
    "MSCI EM":        [(fetch_yahoo, "EEM")],     # ETF proxy for the index
    "EUR/USD":        [(fetch_yahoo, "EURUSD=X"), (fetch_frankfurter, "EUR/USD"),
                       (fetch_fred, "DEXUSEU")],
}

# Crash-risk indicators matched by `nm`. Only numeric, clean-feed metrics here.
# `scale` converts the raw feed number into the units the displayed val uses
# (FRED OAS series are in %, the page shows basis points).
CRASH_FEEDS = {
    "US HY OAS spread":  {"chain": [(fetch_fred, "BAMLH0A0HYM2")], "scale": 100.0},
    "IG OAS spread":     {"chain": [(fetch_fred, "BAMLC0A0CM")],   "scale": 100.0},
    "VIX":               {"chain": [(fetch_yahoo, "^VIX"), (fetch_fred, "VIXCLS")], "scale": 1.0},
    "Chicago Fed NFCI":  {"chain": [(fetch_fred, "NFCI")],         "scale": 1.0},
    "EM $-credit spread":{"chain": [(fetch_fred, "BAMLEMHBHYCRPIOAS")], "scale": 100.0},
    "USD (DXY) funding": {"chain": [(fetch_yahoo, "DX-Y.NYB")], "scale": 1.0},
    "USD (DXY) stress":  {"chain": [(fetch_yahoo, "DX-Y.NYB")], "scale": 1.0},
}


def feeds_for_ticker(ticker):
    """Feed chain for an equity ticker. Yahoo takes the ticker as written --
    including foreign suffixes like BA.L, RR.L, RHM.DE, 7011.T -- so no symbol
    translation is needed now that Stooq is gone."""
    return [(fetch_yahoo, ticker.strip())]


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
        latest, _, _ = fetch_chain(*feeds_for_ticker(ticker))
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
        # indent=1 matches what the analysis step writes. They disagreed until
        # 2026-08-24, so every fetch reformatted all ~9,800 lines and the analysis
        # reformatted them back: a 19,233-line diff for five changed keys, which
        # makes reviewing a day's change by diff impossible.
        json.dump(data, f, ensure_ascii=False, indent=1)

    print("fetch_data.py: %d updated, %d stale/kept-prior"
          % (len(report["ok"]), len(report["stale"])))
    for line in report["stale"]:
        print("  STALE  " + line)
    # Non-zero exit only if literally nothing succeeded (likely total network
    # outage) -- lets the workflow still publish the prior good page.
    return 0 if report["ok"] else 0


if __name__ == "__main__":
    sys.exit(main())

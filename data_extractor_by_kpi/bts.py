#!/usr/bin/env python3
"""
bts.py — shared helpers for the BTS T-100 KPI scripts.

ONE SOURCE OF TRUTH for everything the KPI scripts have in common: the API
endpoint, the query layer, numeric coercion, and the monthly->annual roll-up.
KPI scripts import from here instead of re-declaring their own copies.

SOURCE  BTS r495-tyji — "T100 Segment Summary By Origin Airport", live JSON API.
        Grain: one row per origin airport per month, 2014-present. No deps.

USED BY  kpis.py (all five KPI calculations), tools.py
"""
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://data.bts.gov/resource/r495-tyji.json"
DEFAULT_MONTHS = 6          # half a year — the default rolling KPI window
MAX_PARALLEL = 8            # simultaneous BTS requests; be a polite API client


# ---------- API ----------
_cache = {}                 # url -> rows, for the life of the process


def api(timeout=120, **params):
    """Raw query against the dataset. Keyword args become query parameters;
    Socrata's $-prefixed options are passed as e.g. api(**{"$limit": 10}).

    CACHED by URL. BTS data changes monthly, so within one request — or one
    conversation — the same query always yields the same rows. Without this,
    ranking six airports re-fetched the same months three times over.
    """
    params.setdefault("$limit", 50000)
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    if url not in _cache:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            _cache[url] = json.load(r)
    return _cache[url]


def parallel(fn, items):
    """Map fn over items concurrently, preserving order.

    These are independent network calls that spend their time waiting, so doing
    them one at a time is pure latency: six airports took 33s sequentially and
    ~4s here. Threads (not processes) because the work is all I/O.
    """
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        return list(pool.map(fn, items))


def fetch_recent_months(airport, months=DEFAULT_MONTHS):
    """Newest `months` months for this airport, oldest first.

    A ROLLING window, so it may span a year boundary (e.g. 2025-11..2026-04).
    """
    rows = api(timeout=60, origin_airport_code=airport,
               **{"$order": "reporting_month DESC", "$limit": months})
    return list(reversed(rows))


def fetch_all_months(airport):
    """Every month on record for this airport, oldest first."""
    return api(origin_airport_code=airport, **{"$order": "reporting_month"})


# ---------- numbers ----------
def num(x):
    """Coerce an API field to float; missing/garbage -> 0.0."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def pct(a, b):
    """Percent growth from b to a. Returns 0.0 when b is 0."""
    return (a - b) / b * 100 if b else 0.0


def load_factor(pax, seats):
    """Passengers / seats as a percentage. Returns 0.0 when seats is 0."""
    return pax / seats * 100 if seats else 0.0


# ---------- monthly rows -> per-year roll-up ----------
def by_year(rows):
    """{year: {months, pax, deps, seats, lf}} summed from monthly rows.

    `lf` is recomputed from the summed pax/seats rather than averaging the
    monthly load factors, so big and small months weigh correctly.
    """
    y = {}
    for r in rows:
        d = y.setdefault(int(r["year"]),
                         {"months": 0, "pax": 0.0, "deps": 0.0, "seats": 0.0})
        d["months"] += 1
        d["pax"] += num(r["total_passengers"])
        d["deps"] += num(r["total_departures"])
        d["seats"] += num(r["total_seats"])
    for d in y.values():
        d["lf"] = load_factor(d["pax"], d["seats"])
    return y


def full_year(y, yr):
    """Is this a complete (12-month) year in the roll-up?"""
    return yr in y and y[yr]["months"] >= 12


# ---------- shared formatting ----------
def month_of(row):
    """'2026-04-01T00:00:00.000' -> '2026-04'"""
    return row["reporting_month"][:7]

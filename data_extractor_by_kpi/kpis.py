#!/usr/bin/env python3
"""
kpis.py — all five KPI calculations, as pure functions.

ONE FILE because these are five questions asked of ONE table (BTS r495-tyji), not
five independent analyses. Splitting them across five modules implied more
separation than exists: KPI 3 is literally KPI 1 + KPI 2 added together.

Everything here is PURE — rows in, dict out, no I/O and no printing. That is what
makes it testable (see selftest.py) and reusable: tools.py wraps these for the
model, and the web charts render the same numbers.

    KPI 1  congestion(rows)        how full are the flights, right now?
    KPI 2  growth(by_year(rows))   which direction, and how fast?
    KPI 3  score(b) / verdict(b)   which airport to pick? (reuses 1 + 2)
    KPI 4  mix(rows)               what KIND of passenger?
    KPI 5  national_rank(airport)  how big, nationally? (aggregated queries)

The four raw facts the whole model rests on: passengers, seats, departures, load
factor — plus a domestic split and an average trip distance.

SOURCE  BTS r495-tyji, live JSON API. Query layer lives in bts.py.
"""
from bts import (DEFAULT_MONTHS, api, by_year, fetch_all_months, full_year,
                 month_of, num, parallel, pct)

# ---------------------------------------------------------------- KPI 1
def congestion(rows):
    """How full the flights are = load factor = passengers / seats.

    Higher means demand pressing against capacity. Note this is how full the
    AIRCRAFT are — an airline decision — not how full the building is. It is the
    closest proxy this table offers, and the biggest caveat in the model.
    """
    lf = [(month_of(r), num(r["total_load_factor"])) for r in rows]
    vals = [v for _, v in lf]
    avg = sum(vals) / len(vals) if vals else 0.0
    verdict = ("HIGH" if avg >= 85 else "elevated" if avg >= 80
               else "moderate" if avg >= 70 else "low")
    return {"avg": avg, "n": len(vals), "verdict": verdict,
            "series": lf,          # [(month, load_factor)] — charted by the web UI
            "peak": max(lf, key=lambda x: x[1]) if lf else ("n/a", 0.0),
            "low":  min(lf, key=lambda x: x[1]) if lf else ("n/a", 0.0),
            "near_full": sum(1 for v in vals if v >= 85)}


# ---------------------------------------------------------------- KPI 2
def growth(y):
    """Several growth indicators from a by_year() roll-up, so no single noisy
    year-over-year number drives the answer. None if under 2 complete years.

    Only COMPLETE years count: the table always holds a partial current year,
    which would otherwise read as a sudden collapse.
    """
    full = sorted(yr for yr in y if full_year(y, yr))
    if len(full) < 2:
        return None
    Y, base = full[-1], full[-1] - 3
    g = {"Y": Y, "yoy": [], "cagr": None, "seat_cagr": None, "gap": None,
         "vs19": None, "trajectory": None,
         "lf_now": y[Y]["lf"], "lf_2019": y[2019]["lf"] if full_year(y, 2019) else None}
    for yr in full[-3:]:
        if full_year(y, yr - 1):
            g["yoy"].append((yr, pct(y[yr]["pax"], y[yr - 1]["pax"])))
    if full_year(y, base):
        g["cagr"]      = ((y[Y]["pax"]   / y[base]["pax"]  ) ** (1 / 3) - 1) * 100
        g["seat_cagr"] = ((y[Y]["seats"] / y[base]["seats"]) ** (1 / 3) - 1) * 100
        # THE investment signal: passengers outgrowing seats means demand the
        # airport cannot absorb. Negative means airlines are already fixing it.
        g["gap"] = g["cagr"] - g["seat_cagr"]
    if full_year(y, 2019):
        g["vs19"] = pct(y[Y]["pax"], y[2019]["pax"])
    rates = [r for _, r in g["yoy"]]
    if len(rates) >= 2:
        label = ("accelerating" if rates[-1] > rates[0] + 1
                 else "fading" if rates[-1] < rates[0] - 1 else "steady")
        g["trajectory"] = (rates[0], rates[-1], label)
    return g


# ---------------------------------------------------------------- KPI 3
def blocks(airport):
    """KPI 1 + KPI 2 for one airport, from a SINGLE fetch.

    The full history already contains the recent months, so KPI 1's window is
    sliced off the tail instead of re-requesting it. Returns None when the airport
    has no rows, so callers can separate MISSING from LOW-SCORING — reporting a
    missing airport as a bad candidate would be a lie.
    """
    rows = fetch_all_months(airport)
    if not rows:
        return None
    c = congestion(rows[-DEFAULT_MONTHS:])                # KPI 1
    g = growth(by_year(rows)) or {}                       # KPI 2
    return {"airport": airport, "lf": c["avg"],
            "cagr": g.get("cagr"), "gap": g.get("gap"), "vs19": g.get("vs19")}


def score(b):
    """Full now + growing + unmet demand.

    KNOWN LIMITATION: these are raw units on different scales, so load factor
    (70-90) dominates and the gap contributes 0-2. Ranking order is sound;
    the absolute number is not meaningful. Needs normalizing.
    """
    return b["lf"] + (b["cagr"] or 0.0) + max(b["gap"] or 0.0, 0.0)


def verdict(b):
    strong = b["lf"] >= 82 and (b["cagr"] or 0) > 0 and (b["gap"] or 0) > 0
    v = "STRONG candidate" if strong else \
        "moderate" if b["lf"] >= 78 and (b["cagr"] or 0) >= -1 else "weak"
    if b["vs19"] is not None and b["vs19"] < 0:
        v += " (still recovering)"
    return v


# ---------------------------------------------------------------- KPI 4
# International share bands (%), from the observed 2025 spread: DEN 6 -> JFK 55.
GLOBAL, INTERNATIONAL, SOME = 40, 20, 8


def mix(rows):
    """What KIND of passenger, not how many.

    Every other KPI counts passengers; this asks who they are. Two airports with
    identical traffic need different buildings: international share drives customs
    halls and wide-body gates, trip length drives dwell time and seating.

    total_distance_passenger is ALREADY AN AVERAGE (~2,442 mi for SFO), so it must
    be WEIGHTED by passengers, never summed — summing it produced garbage once.
    """
    pax = sum(num(r["total_passengers"]) for r in rows)
    if not pax:
        return None
    dom = sum(num(r["domestic_passengers"]) for r in rows)
    # Weighted: a busy month must count more than a quiet one.
    miles = sum(num(r["total_distance_passenger"]) * num(r["total_passengers"])
                for r in rows) / pax
    return {"n": len(rows), "pax": pax,
            "intl_pct": (pax - dom) / pax * 100, "avg_miles": miles}


def profile(intl_pct):
    """Which band this airport sits in, and what it implies for a terminal."""
    if intl_pct >= GLOBAL:
        return ("global gateway",
                "Most traffic is international, so terminal needs are dominated by "
                "customs and immigration capacity, wide-body gates and long dwell "
                "times — the most expensive kind of space, and the highest "
                "retail spend per passenger.")
    if intl_pct >= INTERNATIONAL:
        return ("major international",
                "A large international share, so international facilities are a "
                "real part of any expansion, not an afterthought.")
    if intl_pct >= SOME:
        return ("mostly domestic",
                "Largely a domestic airport with some international service — "
                "expansion is mainly about domestic gates and security capacity.")
    return ("domestic",
            "Almost entirely domestic traffic, so expansion is about domestic "
            "gates, security lines and baggage — little customs requirement.")


def haul(avg_miles):
    """Average trip length, in words. Long-haul passengers occupy a terminal for
    hours longer than short-haul commuters, which drives seating and retail."""
    if avg_miles >= 1800:
        return ("long-haul", "Passengers fly long distances on average, so they "
                             "arrive early and spend longer in the terminal.")
    if avg_miles >= 900:
        return ("medium-haul", "A mix of short and long trips.")
    return ("short-haul", "Mostly short trips — passengers move through quickly, "
                          "so throughput matters more than lounge space.")


# ---------------------------------------------------------------- KPI 5
MOVE = 10               # years back for the rank-movement comparison
SHIFT = 3               # places moved before we call it climbing/falling


def latest_complete_year():
    """Newest year with a full 12 months. Detected, never hardcoded — the table
    always holds a partial current year that would rank far too low."""
    rows = api(timeout=60, **{"$select": "year,count(distinct reporting_month) as m",
                              "$group": "year", "$order": "year DESC", "$limit": 5})
    return max(int(r["year"]) for r in rows if int(r["m"]) >= 12)


def ranking(year):
    """Every US origin airport ranked by passengers for one year, biggest first.

    Socrata sums server-side, so the whole national ranking is ONE request of
    ~1,300 small rows. Position in the sorted list IS the rank.
    """
    rows = api(**{"$select": "origin_airport_code,sum(total_passengers) as pax",
                  "$where": f"reporting_month between '{year}-01-01' "
                            f"and '{year}-12-31'",
                  "$group": "origin_airport_code",
                  "$order": "pax DESC", "$limit": 2000})
    return [(r["origin_airport_code"], float(r["pax"])) for r in rows]


def place_of(table, airport):
    """(rank, passengers) for this airport, or (None, None) if it has no traffic."""
    for i, (code, pax) in enumerate(table, 1):
        if code == airport:
            return i, pax
    return None, None


def tier_of(rank):
    """Plain-language size band. 'Rank 95 of 1311' alone does not tell the model
    whether that is big or small; this does."""
    if rank <= 30:
        return "major hub"
    if rank <= 100:
        return "mid-size airport"
    return "small regional airport"


def national_rank(airport):
    """KPI 5: this airport against the whole country, not against its own past.

    The two can disagree, and that disagreement IS the insight — an airport can
    grow every year and still slide down the ranking because rivals grew faster.

    We rank by TOTAL PASSENGERS; official BTS ranks ENPLANEMENTS. Validated
    against official 2023 at mean 0.1 places (only SEA/MIA swap). Close, but do
    not quote as the official figure.
    """
    airport = airport.strip().upper()
    year = latest_complete_year()
    # Two independent ~1,300-row queries, fetched in parallel. api() caches them,
    # so a second airport in the same conversation is free.
    table, past = parallel(ranking, [year, year - MOVE])
    rank, pax = place_of(table, airport)
    if rank is None:
        return {"airport": airport, "found": False, "year": year}

    then, _ = place_of(past, airport)
    moved = then - rank if then else None          # positive = climbed
    return {"airport": airport, "found": True,
            "year": year, "rank": rank, "of_airports": len(table),
            "passengers": int(pax), "tier": tier_of(rank),
            "rank_10y_ago": then, "places_moved_10y": moved,
            "direction": ("climbing" if moved is not None and moved >= SHIFT else
                          "falling" if moved is not None and moved <= -SHIFT else
                          "stable" if moved is not None else None)}

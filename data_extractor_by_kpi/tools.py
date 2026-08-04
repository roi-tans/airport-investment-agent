#!/usr/bin/env python3
"""
tools.py — the KPI tools the model can activate, and their OpenAI schemas.

Each tool is a thin wrapper that turns a KPI module into STRUCTURED data
(a dict), never printed text: the model reads these results, so they must be
small, self-describing, and honest about failure.

TWO EXPORTS the agent needs:
  TOOLS         name -> function, for dispatching what the model asked for
  TOOL_SCHEMAS  the routing menu sent to the model on every call

Add a KPI by writing one wrapper + one schema() entry — nothing else changes.
"""
from bts import by_year, fetch_all_months, fetch_recent_months, full_year, parallel
from kpis import (blocks, congestion, growth, haul, mix, national_rank, profile,
                  score, verdict)

def rnd(v, digits=1):
    """Round floats for the model; pass ints/None/strings through untouched."""
    return round(v, digits) if isinstance(v, float) else v

def not_found(airport):
    """No rows for this code. Say so explicitly — a zeroed result would read as
    a real but quiet airport, and the model would report it as fact."""
    return {"airport": airport, "found": False,
            "error": f"No BTS data for '{airport}'. It may not be a valid IATA "
                     "origin-airport code, or it has no T-100 traffic. Check the "
                     "code and retry (e.g. NYC is a city, not an airport: use "
                     "JFK, LGA or EWR)."}

# ---------- the tools ----------
def get_congestion(airport):
    rows = fetch_recent_months(airport)
    if not rows:
        return not_found(airport)
    m = congestion(rows)
    return {"airport": airport, "found": True,
            "months": m["n"],
            "avg_load_factor": rnd(m["avg"]),
            "verdict": m["verdict"],
            "peak_month": m["peak"][0],
            "peak_load_factor": rnd(m["peak"][1]),
            # The monthly values behind the average. The model is told to quote the
            # sentences, not this; the web UI charts it so the reader can see the
            # seasonal swing an average hides.
            "monthly": [{"month": mo, "load_factor": rnd(v)} for mo, v in m["series"]]}

def millions(pax):
    """34_200_000 -> '34.2M'. A raw 9-digit number tells the reader nothing."""
    return f"{pax / 1e6:.1f}M"

def recovery_phrase(vs19):
    """The sign of vs_2019 is the whole meaning, so spell it out in words."""
    if vs19 is None:
        return "No 2019 baseline in the data, so recovery is unknown."
    if vs19 >= 0:
        return f"Busier than before the pandemic — {vs19:.1f}% ABOVE its 2019 level."
    return (f"Still {abs(vs19):.1f}% BELOW its 2019 level — it has not fully "
            "recovered yet.")

def momentum_phrase(trajectory):
    """'fading' alone is cryptic; carry the two rates that produced the label."""
    if not trajectory:
        return "Not enough years to tell whether growth is speeding up or slowing."
    first, last, label = trajectory
    trend = {"accelerating": "speeding up", "fading": "slowing down",
             "steady": "holding steady"}[label]
    return (f"Growth is {trend}: yearly growth went from {first:+.1f}% to "
            f"{last:+.1f}%.")

def airline_response_phrase(gap):
    """THE investment signal. A positive gap means passengers are outgrowing the
    seats airlines supply — demand the airport cannot absorb. Negative means
    airlines are already fixing it themselves, so a terminal adds less."""
    if gap is None:
        return "Not enough years to compare passenger growth against seat growth."
    if gap > 0.5:
        return (f"Passengers are growing FASTER than airlines add seats "
                f"({gap:+.1f} points/year) — flights keep getting fuller, a sign "
                "of demand the airport cannot absorb.")
    if gap < -0.5:
        return (f"Airlines are adding seats FASTER than passengers grow "
                f"({gap:+.1f} points/year) — the crowding is easing on its own.")
    return (f"Passenger growth and added seats are roughly in step "
            f"({gap:+.1f} points/year).")

def get_growth(airport):
    rows = fetch_all_months(airport)
    if not rows:
        return not_found(airport)
    y = by_year(rows)
    g = growth(y)
    if not g:
        return {"airport": airport, "found": True,
                "error": "Airport exists but has fewer than 2 complete years of "
                         "data, so growth cannot be computed."}
    cagr = g["cagr"]
    return {
        "airport": airport, "found": True,
        "through_year": g["Y"],
        "passengers_latest_year": millions(y[g["Y"]]["pax"]),
        "growth_per_year_pct": rnd(cagr),
        "growth_meaning": (
            f"Passengers grew {cagr:+.1f}% per year on average over the last 3 "
            f"years ({g['Y'] - 3} to {g['Y']})."
            if cagr is not None else
            "Fewer than 4 years of data, so an average growth rate is not reliable."),
        "vs_prepandemic_pct": rnd(g["vs19"]),
        "recovery": recovery_phrase(g["vs19"]),
        "momentum": momentum_phrase(g["trajectory"]),
        "airline_response": airline_response_phrase(g["gap"]),
        # Passengers per year, for charting the actual curve behind the rates.
        # COMPLETE years only: the table always holds a partial current year, and
        # plotting it would draw a collapse that never happened.
        "yearly": [{"year": yr, "passengers": int(y[yr]["pax"])}
                   for yr in sorted(y) if full_year(y, yr)],
    }

def get_candidate(airports):
    """Rank the codes we have data for; list unknown ones separately so the
    model never presents a missing airport as a low-scoring one.

    One fetch per airport, all in parallel — blocks() returns None for a code
    with no rows, which is what separates missing from low-scoring.
    """
    results = parallel(blocks, airports)
    found = [b for b in results if b]
    missing = [code for code, b in zip(airports, results) if not b]
    ranked = sorted(found, key=score, reverse=True)
    # The four signals BEHIND the score, so the model can explain a ranking instead of
    # reciting it. Without these it only ever sees "score 85.3, weak" and cannot say
    # why one airport beats another. Already computed by blocks() — no extra work.
    result = {"ranked": [{"airport": b["airport"], "score": rnd(score(b)),
                          "verdict": verdict(b),
                          "load_factor": rnd(b["lf"]),
                          "growth_per_year_pct": rnd(b["cagr"]),
                          # A SENTENCE, not a number: the model read the bare gap as a
                          # capacity level and reported "demand is below seated
                          # capacity (-0.8%)", inverting the meaning. It is a rate
                          # comparison, so it has to arrive already interpreted.
                          "demand_vs_seats": airline_response_phrase(b["gap"]),
                          "vs_2019_pct": rnd(b["vs19"])} for b in ranked]}
    if missing:
        result["not_found"] = missing
        result["note"] = ("No BTS data for these codes — they are excluded from the "
                          "ranking, NOT ranked low. Verify them and retry.")
    return result

def get_national_rank(airport):
    """KPI 5: where this airport sits among ALL US airports, and which way it moved.

    Ranked live from the same table as KPI 1-3, so every airport gets a real
    rank out of ~1,300 — small airports included — as of the same year.
    """
    r = national_rank(airport)
    if not r["found"]:
        return not_found(airport)

    moved, direction = r["places_moved_10y"], r["direction"]
    if moved is None:
        movement = ("No data from 10 years ago, so we cannot say whether it is "
                    "gaining or losing ground nationally.")
    elif direction == "climbing":
        movement = (f"It has CLIMBED {moved} places in 10 years (was rank "
                    f"{r['rank_10y_ago']}) — it is winning passengers faster than "
                    "other US airports.")
    elif direction == "falling":
        movement = (f"It has FALLEN {abs(moved)} places in 10 years (was rank "
                    f"{r['rank_10y_ago']}) — other airports grew faster, so it is "
                    "losing ground nationally even if its own traffic rose.")
    else:
        movement = (f"Its national position is unchanged in 10 years (rank "
                    f"{r['rank_10y_ago']} then, {r['rank']} now).")

    return {**r,
            "size": f"Rank {r['rank']} of {r['of_airports']} US airports by "
                    f"passengers in {r['year']} — a {r['tier']}.",
            "movement": movement}

def get_traffic_mix(airport):
    """KPI 4: what KIND of passenger, which decides what kind of terminal."""
    rows = fetch_recent_months(airport)
    if not rows:
        return not_found(airport)
    m = mix(rows)
    label, why = profile(m["intl_pct"])
    hl, hwhy = haul(m["avg_miles"])
    return {"airport": airport, "found": True,
            "months": m["n"],
            "international_share_pct": rnd(m["intl_pct"]),
            "airport_type": label,
            "terminal_implication": why,
            "avg_trip_miles": int(m["avg_miles"]),
            "trip_length": hl,
            "trip_implication": hwhy}

# ---------- schemas: the routing menu the model sees ----------
ONE_AIRPORT = {"airport": {"type": "string", "description": "IATA code, e.g. SFO"}}
MANY_AIRPORTS = {"airports": {"type": "array", "items": {"type": "string"},
                              "description": "IATA codes, e.g. ['BOS','BDL','PVD']"}}

def schema(fn, description, properties):
    """Wrap one tool in the shape the OpenAI API expects."""
    return {"type": "function", "function": {
        "name": fn.__name__,
        "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": list(properties)}}}

TOOL_SCHEMAS = [
    schema(get_congestion,
           "KPI 1. How full an airport's flights are (load factor) over recent months. "
           "Use for congestion / how busy / how full.",
           ONE_AIRPORT),
    schema(get_growth,
           "KPI 2. Whether an airport is growing: average yearly passenger growth, "
           "whether it has recovered past 2019, whether growth is speeding up or "
           "slowing, and whether airlines are adding seats fast enough to keep up. "
           "Returns plain-English sentences — quote them rather than the raw numbers.",
           ONE_AIRPORT),
    schema(get_candidate,
           "KPI 3. Score/rank one or more airports as terminal-expansion candidates "
           "(full now + growing + demand outrunning capacity). Pass several codes to "
           "compare a region.",
           MANY_AIRPORTS),
    schema(get_traffic_mix,
           "KPI 4. What KIND of traffic an airport has: international vs domestic "
           "share and average trip length, and what that implies for the terminal "
           "(customs halls, wide-body gates, dwell time). Use for 'international', "
           "'domestic', 'long-haul', 'what kind of airport', or WHAT to build once "
           "another KPI shows expansion is justified.",
           ONE_AIRPORT),
    schema(get_national_rank,
           "KPI 5. How the airport ranks among ALL ~1,300 US airports by passengers, "
           "and whether it has climbed or fallen over 10 years. Use for size, "
           "national standing, market share, 'is it a major airport', or to "
           "sanity-check growth: an airport can grow yet still lose rank. Returns "
           "plain-English sentences (size, movement) — quote those.",
           ONE_AIRPORT),
]

TOOLS = {fn.__name__: fn for fn in
         (get_congestion, get_growth, get_candidate, get_traffic_mix,
          get_national_rank)}

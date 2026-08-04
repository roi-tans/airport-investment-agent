#!/usr/bin/env python3
"""
selftest.py — one command to check the whole data layer against live BTS.

Replaces the five per-signal CLI printers. Run it after touching kpis.py, bts.py or
tools.py, and before any deploy.

    python3 selftest.py              all five signals on default airports
    python3 selftest.py SFO JFK      the single-airport signals on these codes

It checks INVARIANTS, not exact values — BTS adds a month at a time, so hardcoded
numbers would rot. What must always hold:

  * a load factor is a percentage, and the reported average equals the mean of
    the monthly series the chart draws
  * growth years are complete and consecutive-ish, and the last one is the year
    quoted in the answer
  * a ranking is sorted, and its length is the "of N airports" figure
  * international share is 0-100, and a weighted average trip is plausible
  * a bad airport code returns found=False, never a zeroed-out result
"""
import json
import sys
import time

from bts import by_year, fetch_all_months, fetch_recent_months
from kpis import (blocks, congestion, growth, haul, latest_complete_year, mix,
                  national_rank, profile, score, tier_of, verdict)
from tools import (TOOL_SCHEMAS, TOOLS, get_candidate, get_congestion,
                   get_growth, get_national_rank, get_traffic_mix)

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok    {label}")
    else:
        fail += 1
        print(f"  FAIL  {label}   {detail}")


def head(title):
    print(f"\n{title}\n" + "-" * len(title))


# ---------------------------------------------------------------- Signals 1-4
def kpi_checks(code):
    head(f"{code}")
    rows = fetch_recent_months(code)
    check(f"{code}: recent months returned", bool(rows), f"got {len(rows)}")
    if not rows:
        return

    c = congestion(rows)                                             # Signal 1
    mean = sum(v for _, v in c["series"]) / len(c["series"])
    check("Signal 1  load factor in 0-100", 0 < c["avg"] <= 100, f"{c['avg']:.1f}")
    # The chart draws `series`; the answer quotes `avg`. They must agree, or the
    # picture contradicts the text.
    check("Signal 1  avg == mean of charted series", abs(c["avg"] - mean) < 0.05,
          f"{c['avg']:.3f} vs {mean:.3f}")
    check("Signal 1  peak >= avg", c["peak"][1] >= c["avg"])

    y = by_year(fetch_all_months(code))
    g = growth(y)                                                    # Signal 2
    check("Signal 2  growth computed", g is not None)
    if g:
        check("Signal 2  anchored on a complete year", y[g["Y"]]["months"] >= 12,
              f"{g['Y']} has {y[g['Y']]['months']} months")
        if g["gap"] is not None and g["cagr"] is not None:
            check("Signal 2  gap == pax cagr - seat cagr",
                  abs(g["gap"] - (g["cagr"] - g["seat_cagr"])) < 1e-9)

    b = blocks(code)                                                 # Signal 3
    check("Signal 3  blocks + score", b is not None and isinstance(score(b), float))
    check("Signal 3  verdict is a phrase", isinstance(verdict(b), str) and verdict(b))

    m = mix(rows)                                                    # Signal 4
    check("Signal 4  international share 0-100", 0 <= m["intl_pct"] <= 100,
          f"{m['intl_pct']:.1f}")
    # Weighted, never summed: a summed average once produced tens of thousands.
    check("Signal 4  avg trip plausible (50-9000 mi)", 50 < m["avg_miles"] < 9000,
          f"{m['avg_miles']:.0f}")
    check("Signal 4  profile + haul labelled",
          bool(profile(m["intl_pct"])[0]) and bool(haul(m["avg_miles"])[0]))


# ---------------------------------------------------------------- Signal 5
def rank_checks(code):
    head("Signal 5  national rank")
    year = latest_complete_year()
    check("year is complete and recent", 2014 <= year <= 2100, str(year))
    r = national_rank(code)
    check(f"{code} found", r["found"])
    if r["found"]:
        check("rank within 1..N", 1 <= r["rank"] <= r["of_airports"],
              f"{r['rank']} of {r['of_airports']}")
        check("tier matches rank", r["tier"] == tier_of(r["rank"]))
        check("~1300 airports ranked", 500 < r["of_airports"] < 3000,
              str(r["of_airports"]))


# ---------------------------------------------------------------- tools layer
def tool_checks():
    head("tools layer (what the model actually calls)")
    check("5 tools registered", len(TOOLS) == 5, str(sorted(TOOLS)))
    check("5 schemas", len(TOOL_SCHEMAS) == 5)
    for s in TOOL_SCHEMAS:
        f = s["function"]
        check(f"schema {f['name']}: named + described + params",
              f["name"] in TOOLS and len(f["description"]) > 40
              and f["parameters"]["required"])

    # Every result must survive json.dumps — it is sent to the model and browser.
    for name, res in [("get_congestion", get_congestion("SFO")),
                      ("get_growth", get_growth("SFO")),
                      ("get_traffic_mix", get_traffic_mix("JFK")),
                      ("get_national_rank", get_national_rank("PWM")),
                      ("get_candidate", get_candidate(["BOS", "PVD"]))]:
        try:
            json.dumps(res)
            check(f"{name} JSON-serialisable", True)
        except (TypeError, ValueError) as e:
            check(f"{name} JSON-serialisable", False, str(e))

    # The two chart arrays the web UI needs.
    c = get_congestion("SFO")
    check("get_congestion returns monthly[] for the chart",
          len(c.get("monthly", [])) == c["months"])
    g = get_growth("SFO")
    yrs = [p["year"] for p in g.get("yearly", [])]
    check("get_growth returns yearly[] for the chart", len(yrs) > 2, str(len(yrs)))
    check("yearly ends at the year quoted in the answer",
          yrs and yrs[-1] == g["through_year"], f"{yrs[-1:]} vs {g['through_year']}")
    check("yearly is sorted ascending", yrs == sorted(yrs))

    # Ranking must be ordered, or "#1" is meaningless.
    rk = get_candidate(["BOS", "PVD", "MHT", "BDL"])["ranked"]
    scores = [a["score"] for a in rk]
    check("get_candidate sorted best-first", scores == sorted(scores, reverse=True),
          str(scores))


def failure_checks():
    head("failure modes (must not invent data)")
    bad = get_congestion("MYC")
    check("bad code -> found=False", bad.get("found") is False)
    check("bad code -> no fake load factor", "avg_load_factor" not in bad)
    check("bad code -> explains itself", len(bad.get("error", "")) > 20)
    for fn in (get_growth, get_traffic_mix, get_national_rank):
        check(f"{fn.__name__} rejects a bad code",
              fn("ZZZZ").get("found") is False)
    mixed = get_candidate(["BOS", "MYC"])
    check("mixed batch ranks only real airports",
          [a["airport"] for a in mixed["ranked"]] == ["BOS"])
    check("mixed batch lists the missing one", mixed.get("not_found") == ["MYC"])


if __name__ == "__main__":
    codes = [a.upper() for a in sys.argv[1:]] or ["SFO", "JFK"]
    t0 = time.time()
    print(f"SELFTEST — live BTS, airports: {', '.join(codes)}")
    for code in codes:
        kpi_checks(code)
    rank_checks(codes[0])
    tool_checks()
    failure_checks()
    print(f"\n{ok} passed, {fail} failed   ({time.time() - t0:.1f}s)")
    sys.exit(1 if fail else 0)

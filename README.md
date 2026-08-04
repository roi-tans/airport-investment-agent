# Airport Investment Intelligence Agent

An LLM agent that answers one question: **where in the US would a terminal
renovation actually pay off?**

Ask it in plain English. It decides which analysis to run, queries live BTS data,
and answers in plain English.

```
you > Is PWM a major airport?

PWM (Portland International Jetport) is ranked 95 out of 1,311 US airports by
passenger numbers, making it a mid-size airport. Its national position has
remained stable over the past 10 years, moving from rank 94 to 95.
```

---

## The story: one table, five questions

Everything comes from **one** BTS table — `r495-tyji`, "T-100 Segment Summary By
Origin Airport". One row per origin airport per month, 2014 to present, live JSON
API. No download, no API key, no database.

That table holds only four raw facts: **passengers, seats, departures, load
factor** (plus a domestic/international split and average trip distance). Every
KPI is a different question asked of those same numbers:

| KPI | Question | Window | Compared against |
|---|---|---|---|
| **1 Congestion** | How full is it *now*? | last 6 months | fixed thresholds |
| **2 Growth** | Which *direction*? | all years | its own past |
| **3 Candidate** | Which one to *pick*? | both | the other airports you named |
| **4 Traffic mix** | *What* should we build? | last 6 months | size/haul bands |
| **5 National rank** | How *big*, and gaining or losing? | latest full year | all ~1,300 US airports |

Two things make this design work:

**One source means one as-of date.** Every number is from the same table, so
growth and rank are directly comparable. This mattered: KPI 5 originally used a
second table (`2ydv-qfge`, the official top-50 ranking) that ends at **2023**. The
agent reported 2025 growth beside a 2023 rank and concluded SFO was "losing ground
to competitors" — reasoning across two different eras. Computing rank from the main
table fixed it, and upgraded coverage from 50 airports to 1,311.

**KPIs 1/2/3/5 answer *whether* to build. KPI 4 answers *what*.** JFK is 53%
international with 2,653-mile average trips; DEN is 6% and 1,039 miles. They can
show identical load factor and growth, but a renovation at JFK means customs halls
and wide-body gates, while DEN means domestic gates and security lines.

---

## How the agent picks a tool

**Nothing in the Python decides.** On every call, `agent.py` sends the model all
five tool schemas from `tools.py`. The model reads their `description` strings plus
the routing block in `prompts.py`, and replies naming the tool it wants and its
arguments. The code just dispatches:

```python
TOOLS[call.function.name](**json.loads(call.function.arguments))
```

So **the description text and the system prompt *are* the routing logic.** To
re-route the agent, edit those strings — not the loop.

This is also why tools return **plain-English sentences**, not just numbers. The
model tends to parrot whichever field reads like prose. Given only
`demand_capacity_gap: 0.2`, it printed `**Demand-Capacity Gap**: 0.2%` at the
user — a meaningless answer. Given `airline_response: "Passengers are growing
faster than airlines add seats..."`, it says something a human understands.

### Charts

Each answer is accompanied by one chart per tool that ran — monthly columns for
congestion, a passenger curve for growth, ranked bars for candidates, a split bar
for traffic mix, a log-scale position for national rank.

They are drawn **from the tool's returned JSON, not from the answer text.** A
chart is a second claim about the data, and when a chart and a sentence disagree
the reader believes the chart — so nothing in the rendering parses prose. The
browser gets the same `trace` array that `(debug)` displays, and each renderer
reads only its own tool's fields.

This is why `get_congestion` returns a `monthly` array and `get_growth` a `yearly`
one: the rows were already fetched to compute the averages, and were being thrown
away. `yearly` is filtered to **complete years only** — the table always holds a
partial current year, which would otherwise draw a collapse that never happened.

Memory is just a list of dicts, resent in full on every call — the API is
stateless, so that resend *is* the conversation. `trim()` drops the oldest
messages while keeping the system prompt, and never leaves a `tool` message
orphaned at the front (that's a 400 from the API).

---

## Files

```
web.py           HTTP layer: 3 routes, stateless
templates/
  index.html     the page — plain HTML/CSS/JS, no build step
run.sh           start it locally
data_extractor_by_kpi/
  agent.py       the conversation loop + memory. Owns nothing else.
  prompts.py     the system prompt — how the agent BEHAVES (routing lives here)
  tools.py       the five tools + their schemas — what the agent can DO
  kpis.py        all five KPI calculations, as pure functions
  bts.py         shared query layer: api(), fetch_*, by_year(), num()
  selftest.py    one command to check the whole data layer
  TESTS.md       debugging map: question -> expected tool
```

The five KPIs live in **one** file because they are five questions asked of one
table, not five independent analyses — KPI 3 is literally KPI 1 + KPI 2 added
together. Everything in `kpis.py` is pure (rows in, dict out, no I/O), which is
what makes `selftest.py` possible.

### Testing

```bash
cd data_extractor_by_kpi
../.venv/bin/python selftest.py           # 54 checks against live BTS, ~20s
../.venv/bin/python selftest.py BOS PVD   # same checks, your airports
```

It asserts **invariants, not fixed numbers** — BTS adds a month at a time, so
hardcoded values would rot. The ones that matter: the average a tool reports
equals the mean of the series its chart draws, and the last year of the growth
curve equals the year quoted in the answer. Those two are what keep a chart from
contradicting the text.

---

## Running locally

```bash
export OPENAI_API_KEY=sk-...     # once per terminal
./run.sh
```

That's it. `run.sh` frees the port, creates `.venv` if missing, starts the server,
and opens **http://localhost:5001** once it's actually answering. Port 5001 because
macOS AirPlay holds 5000.

**Re-running is the restart** — `./run.sh` again kills whatever holds the port, so
you never hit `Address already in use`. `Ctrl-C` stops it.

It auto-reloads on any `.py` edit, which matters: Python reads `prompts.py` once at
import, so without reload a prompt edit does nothing until you restart — and that
looks exactly like the model ignoring your new instruction.

```bash
./run.sh --once      # no reload, closest to how Render runs it
```

### Terminal version

```bash
cd data_extractor_by_kpi
../.venv/bin/python agent.py                       # conversational
../.venv/bin/python agent.py "How congested is SFO?"   # one-shot
```

In the REPL: `reset` clears memory, `save` writes `history.json`, `exit` quits.

---

## What this cannot answer

The single-source design is what makes the agent trustworthy inside its scope, so
the boundary has to be explicit. Four raw columns cannot produce:

- **Delays** — needs BTS On-Time Performance. No per-airport API.
- **Terminal capacity / gate counts** — not in BTS at all. Needs FAA benchmarks.
  Load factor is how full the *aircraft* are, an airline decision — not a
  building constraint. This is the biggest proxy gap in the whole model.
- **Fares** — needs DB1B, not joinable to an airport without it.
- **Route-level detail** — how many destinations, % of flights that are
  long-haul, which routes are missing. The summary table aggregates destinations
  away, and BTS's route-level T-100 Segment table **is not on the API** (its three
  catalog entries return zero rows — download only).

`prompts.py` tells the model to refuse these rather than substitute the nearest
tool. Worth watching: it once answered "what are the delays at ORD?" with load
factor.

### Known limitations

- **KPI 3's score is ~90–95% load factor.** `score = lf + cagr + max(gap,0)` sums
  raw units on different scales, so the demand-capacity gap contributes 0–2%. All
  airports land in a narrow 74–90 band, and verdicts don't track score order.
  Needs normalizing.
- **Rank is computed, not official.** BTS ranks *enplanements*; we rank *total
  passengers*. Validated against official 2023 at mean 0.1 places (only SEA/MIA
  swap). Close, but don't quote it as the official BTS figure.
- **KPI 4's trip length is an average**, not a share of long-haul flights.

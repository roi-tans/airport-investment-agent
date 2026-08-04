# Airport Investment Intelligence Agent

An LLM agent that answers one question: **where in the US would a terminal
renovation actually pay off?**

**▶ Live demo: https://airport-investment-agent-0va9.onrender.com**

Ask in plain English. The agent picks which analysis to run, queries live BTS
data, and answers in plain English - with a chart drawn from the numbers it
actually measured.

```
you > Is PWM a major airport?

PWM (Portland International Jetport) is ranked 95 out of 1,311 US airports by
passenger numbers, making it a mid-size airport. Its national position has
remained stable over the past 10 years, moving from rank 94 to 95.
```

> Hosted free, so it sleeps when idle - the first visit takes ~50s to wake up.

---

## One table, five questions

Everything comes from **one** BTS table - `r495-tyji`, "T-100 Segment Summary By
Origin Airport": one row per airport per month, 2014 to present, live JSON API.

That table holds only four raw facts - **passengers, seats, departures, load
factor** (plus a domestic split and average trip distance). Each KPI is a
different question asked of those same numbers:

| KPI | Question | Compared against |
|---|---|---|
| **1 Congestion** | How full is it *now*? | fixed thresholds |
| **2 Growth** | Which *direction*? | its own past |
| **3 Candidate** | Which one to *pick*? | the other airports you named |
| **4 Traffic mix** | *What* should we build? | size / trip-length bands |
| **5 National rank** | How *big*, gaining or losing? | all ~1,300 US airports |

**KPIs 1/2/3/5 answer *whether* to build; KPI 4 answers *what*.** JFK is 53%
international on 2,653-mile average trips; DEN is 6% on 1,039. Identical load
factor and growth still mean customs halls at one and domestic gates at the other.

---

## How the agent picks a tool

**Nothing in the Python decides.** The model receives all five tool schemas, reads
their `description` strings plus the routing rules in `prompts.py`, and names the
tool it wants. The code only dispatches:

```python
TOOLS[call.function.name](**json.loads(call.function.arguments))
```

So **the descriptions and the system prompt *are* the routing logic** - to
re-route the agent, edit those strings, not the loop.

It is also why tools return **plain-English sentences** rather than bare numbers.
The model parrots whichever field reads like prose: given
`demand_capacity_gap: 0.2` it printed `**Demand-Capacity Gap**: 0.2%` at the user,
which means nothing. Given `airline_response: "Passengers are growing faster than
airlines add seats…"` it says something a human understands.

## Charts

Each answer carries one chart per tool that ran - monthly columns for congestion,
a passenger curve for growth, ranked bars for candidates, a split bar for traffic
mix, a log-scale position for national rank.

They render **from the tool's returned JSON, never from the answer text.** A chart
is a second claim about the data, and when chart and sentence disagree the reader
believes the chart - so nothing in the rendering parses prose.

Add `(debug)` to any question to see exactly which tools ran and what they
returned. That trace is read from the server's record, not from asking the model,
which could misreport itself.

---

## Files

```
web.py                 HTTP layer: 3 routes, stateless
templates/index.html   the page - plain HTML/CSS/JS, no build step
data_extractor_by_kpi/
  agent.py       the conversation loop + memory
  prompts.py     the system prompt - how the agent BEHAVES (routing lives here)
  tools.py       the five tools + their schemas - what the agent can DO
  kpis.py        all five KPI calculations, as pure functions
  bts.py         shared query layer: api(), fetch_*, by_year(), num()
  selftest.py    54 invariant checks against live BTS (no API key needed)
  TESTS.md       debugging map: question -> expected tool
```

The five KPIs live in **one** file because they are five questions asked of one
table, not five independent analyses - KPI 3 is literally KPI 1 + KPI 2 added
together. Everything in `kpis.py` is pure (rows in, dict out, no I/O), which is
what makes `selftest.py` possible.

`selftest.py` asserts **invariants, not fixed numbers** - BTS adds a month at a
time, so hardcoded values would rot. The two that matter most: the average a tool
reports equals the mean of the series its chart draws, and the growth curve's last
year equals the year quoted in the answer. Those keep a chart from contradicting
the text.

The server is **stateless** - the browser holds the conversation and replays it
each turn, so any process can answer any request and a restart never wipes
someone's session mid-demo.

---

## What this cannot answer

The single-source design is what makes the agent trustworthy inside its scope, so
the boundary has to be explicit. Four raw columns cannot produce:

- **Delays** - needs BTS On-Time Performance. No per-airport API.
- **Terminal capacity / gate counts** - not in BTS at all. Load factor is how full
  the *aircraft* are, an airline decision, not a building constraint. This is the
  biggest proxy gap in the model.
- **Fares** - needs DB1B, not joinable to an airport without it.
- **Route-level detail** - destination counts, share of long-haul flights. The
  summary table aggregates destinations away, and BTS's route-level T-100 Segment
  table is not on the API (its catalog entries return zero rows - download only).

`prompts.py` tells the model to refuse these rather than reach for the nearest
tool. Worth watching: it once answered "what are the delays at ORD?" with load
factor.

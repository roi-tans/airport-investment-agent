# Debugging map — question → expected tool

```bash
export OPENAI_API_KEY=...
./run.sh                 # web UI
python3 agent.py         # terminal
python3 selftest.py      # data layer only — no API key needed
```

`selftest.py` is the first thing to run when something looks wrong: it checks the
numbers and the failure modes without involving the model at all. If it passes,
the bug is in routing or the prompt, which is what the rest of this file maps.

If the wrong tool fires, the bug is in the `description` string in `tools.py` or
the routing block in `prompts.py` — not in `agent.py`.

## Seeing what actually ran

Add `(debug)` to the end of any question in the web UI:

```
Which New England airport is the best expansion candidate? (debug)
```

You get a dashed panel under the answer listing every tool call, its arguments,
and the full JSON it returned (click to expand). It's read from the server's
record of the turn, not from the model — asking the model what it used lets it
misremember or invent a call it never made.

**`no tool was called` is the finding to watch for.** It means the answer came
from training knowledge, not your data. That's what produced the bad "best
airports in the US" answer: confident prose, zero measurements.

## Demo sequence

The seven chips under the chat run in order: one per KPI, then two that combine
tools. Each draws a different chart, so the sequence shows all five visuals.

| # | Ask | Tool | Chart |
|---|---|---|---|
| 1 | How congested is SFO? | `get_congestion` | 6 monthly columns, peak in amber |
| 2 | Is LAX growing? | `get_growth` | passengers per year, 2014→ |
| 3 | Rank BOS, PVD, MHT and BDL | `get_candidate` | ranked bars, leader in green |
| 4 | Is JFK an international airport? | `get_traffic_mix` | one split bar |
| 5 | Is PWM a major airport? | `get_national_rank` | position on a log scale |
| + | Denver or JFK for an international terminal? | `get_traffic_mix` ×2 | two split bars, side by side |
| + | Is SFO growing but losing ground? `(debug)` | `get_growth` + `get_national_rank` | curve + scale, plus the raw trace |

Charts are drawn from the tool's returned JSON — the same `trace` the debug panel
shows — never from the model's prose. **A chart that disagrees with the text is
therefore a tool bug, not a rendering bug.** Two things are worth checking on
screen: the average printed in the congestion note must equal the mean of its own
bars, and the growth curve's last year must equal the year quoted in the answer.

Columns start at 50% and candidate bars at the low score, because both sit in
narrow bands (70–90% load factor, 74–90 score) that a 0-based axis flattens.
The rank scale is logarithmic — rank 10 and rank 95 are both hard against the
left edge otherwise.

---

## 1. Routing — one tool each

| Ask | Should call | Should mention |
|---|---|---|
| How congested is SFO? | `get_congestion` | ~81% load factor, peak month |
| Is LAX growing? | `get_growth` | +3.9%/yr, slowing, still below 2019 |
| Rank BOS, PVD and MHT as expansion candidates | `get_candidate` | BOS first, scores 74–90 |
| Is JFK an international airport? | `get_traffic_mix` | 53% international, global gateway |
| Is PWM a major airport? | `get_national_rank` | rank 95 of 1,311, mid-size |

## 2. Routing — two tools, one turn

| Ask | Should call | Watch for |
|---|---|---|
| Should Denver or JFK get an international terminal expansion? | `get_traffic_mix` ×2 | DEN 6% vs JFK 53%; the *what* vs *whether* split |
| Which New England airport is the best candidate, and is it growing? | `get_candidate` then `get_growth` | second call only after seeing the ranking |
| Is SFO growing but losing ground to other airports? | `get_growth` + `get_national_rank` | +8.9%/yr yet fell rank 7 → 10 |
| How congested is NYC? | `get_congestion` ×3 | JFK, LGA, EWR — and says which it used |

## 3. Plain English — no jargon leaks

| Ask | Must NOT contain | Should say instead |
|---|---|---|
| Is EWR growing? | "CAGR", "trajectory", "demand-capacity gap" | "grew 2.8% per year", "growth is slowing down" |
| What's the demand-capacity gap at BOS? | a bare number like `0.2%` | "passengers growing slightly faster than seats" |

The failing output looked like `**Demand-Capacity Gap**: 0.2%` — field names
printed at the user. That means the model had nothing readable to quote.

## 4. Not-found — must ask, never invent

| Ask | Correct behaviour | Bug if it does this |
|---|---|---|
| How congested is MYC? | says no data, asks you to confirm | reports 0% load factor, or calls it "quiet/small" |
| Rank BOS, PVD, and MYC as expansion candidates | ranks BOS + PVD, lists MYC separately | silently swaps in BDL and ranks 3 airports |

The second one is a real bug we fixed. The model substituted BDL for MYC and
called it "PVD's nearby airport" — MYC never reached a tool, so `found=false`
never fired.

## 5. Coverage limits — must refuse

| Ask | Correct | Bug |
|---|---|---|
| What are the delays at ORD? | says no delay data | answers with load factor |
| How many destinations can you fly to from BOS? | says no route-level data | guesses a number |
| What % of flights from ANC are long-haul? | gives *average* trip length, flags it's not a share | reports a fake percentage |
| What are ticket prices at LAX? | says no fare data | estimates |

## 6. Memory

Ask in sequence — the second question must resolve using the first.

```
How congested is SFO?
and Boston?                    -> calls BOS, no re-ask of the airport
```
```
Is LAX growing?
is it more congested than SFO? -> "it" = LAX; may skip SFO if already in context
```
```
How congested is SFO?
save                           -> writes history.json
reset                          -> clears memory
```

Skipping a redundant tool call is correct, not a bug — the data is already in
context.

---

## Sanity numbers (2025, for spotting nonsense)

| | load factor | growth/yr | intl % | rank |
|---|---|---|---|---|
| ATL | 80.2 | +4.2 | 14.2 | 1 |
| SFO | 80.9 | +8.9 | 28.5 | 10 |
| JFK | 80.5 | — | 52.8 | 6 |
| DEN | 78.7 | — | 6.0 | 4 |
| PWM | 80.3 | +9.2 | 0.0 | 95 |

PWM beats ATL on load factor *and* growth while ranking 95th. That's real — rank
measures scale, not quality. If the agent ever adds rank into a quality score,
that's a bug.

# Week 7 — Race the ticket agent against a fixed workflow

Same task, built twice: a tool-calling loop where the model picks the tools,
and three hard-coded steps. Same 10 tickets, same three tools, same model,
same output contract, same grader. Four numbers each decide it.

## One command each

```bash
python src/ticket_agent.py CD-7001          # the agent, one ticket, verbose
python src/ticket_workflow.py CD-7007       # the workflow, one ticket, verbose
python main.py race --max-iterations 8 --max-seconds 300   # both over all 10 -> week7/race.csv
python experiments/week7_race.py budget-demo --ticket CD-7001 --max-iterations 2 \
    --max-tokens 100000 --max-cost 1 --max-seconds 600 --out week7/budget_logs/max_iterations.log
python experiments/week7_tool_probe.py --compare
python experiments/week7_memory.py restart-demo            # bonus: tier survives a restart
python experiments/week7_memory.py long-race               # bonus: 3 x 30-turn threads
```

`main.py race` defaults to 6 laps / 120 s; the table below was run at 8 / 300,
so pass those flags to reproduce it.

## The result

Race of 2026-09-21, `week7/runs/race_20260921-134848.json`, budgets
`{8 iterations, 8000 tokens, $0.01, 300 s}`, model Qwen2.5-1.5B-Instruct.

| | agent | workflow |
|---|---|---|
| **pass rate** | **0/10 = 0.0%** | **10/10 = 100.0%** |
| **p50 latency** | **338,618 ms** | **3,812 ms** |
| **total tokens** | **44,748** (4,475/ticket) | **1,793** (179/ticket) |
| **cost per ticket** | **$0.000746** | **$0.000042** |
| model calls | 49 | 10 |
| tool calls | 23 | 29 |
| budget terminations | 10 (9 wall clock, 1 iterations) | 0 |

Pass rate by ticket class — the agent fails every class, so no class rescues it:

| class | n | agent | workflow |
|---|---|---|---|
| straight_line | 3 | 0/3 | 3/3 |
| tier_dependent | 2 | 0/2 | 2/2 |
| status_branch | 3 | 0/3 | 3/3 |
| missing_order | 2 | 0/2 | 2/2 |

A ticket passes when the decision and the refund amount are both right and the
reply is non-empty — the same grader for both systems (`grade()` in
`experiments/week7_race.py`).

**Cost.** The models run locally, so the real cash cost is zero. Tokens are the
measurement; cost is tokens × a documented stand-in rate card ($0.15/Mtok in,
$0.60/Mtok out). Tokens are summed per model call, so the agent's re-sent
transcript is counted every lap, not once at the end.

## Three races, and what each one found

| run | agent | what changed | what it found |
|---|---|---|---|
| `race_20260921-115626` | 4/10, p50 105 s | first version | The agent never called `get_order` or `lookup_refund_policy` on any ticket — it read an amount out of the customer's sentence and called it a decision. The wall-clock budget was only checked between laps, so one ticket spent 893 s under a 120 s budget. Cold model load was charged to ticket 1. |
| `race_20260921-131647` | 0/10, p50 151 s | wall clock enforced inside a lap; both systems warmed up; loop refuses an answer whose facts were never fetched | The agent now fetches facts, but the evidence checks cost laps and every ticket hit the 120 s wall budget. |
| `race_20260921-134848` | 0/10, p50 339 s | per-lap generation capped at 128 tokens; budget raised to 8 laps / 300 s | With a budget three times larger it still finishes nothing. The failure is not a tight budget. |

`week7/race_budget120s.csv` keeps the middle run; `week7/race.csv` is the final one.

## All four budgets, enforced

`agent_runtime.Budgets.exceeded()` is asked for all four before every lap, and
returns the first one that is spent:

```python
max_iterations   6      # laps
max_tokens       8000   # prompt + completion, summed across laps
max_cost_usd     0.01   # from the rate card above
max_wall_seconds 120    # also passed into generate() as a stopping criterion
```

The wall clock is the one that needed real work. Checking it between laps let a
single generation overrun it by minutes, so `chat()` takes a deadline and stops
generation mid-answer. `week7/budget_termination.log` is a full run:

```
[agent] lap 1: in=663 out=26 19761ms
[agent]   tool find_ticket({"ticket_id": "CD-7008"}) -> {...}
[agent] lap 2 cut short by the wall-clock budget
[agent] lap 2: in=767 out=44 5881ms
[agent] unparseable reply: "I found your ticket, CD-7008. ..."
[agent] BUDGET max_iterations hit (2 >= 2) after 2 iterations, 1500 tokens,
        $0.000257, 60.3s — terminating cleanly
```

It terminates with a recorded reason and the facts it had, rather than spinning.

That log has a flaw: at 60.3 s it had also reached its 60 s wall clock, so it
does not show *which* budget fired on its own. `week7/budget_logs/` has one
run per budget, each on CD-7001 with the other three set far out of reach, so
exactly one can fire:

| log | budgets (iters / tokens / $ / s) | fired | spent at termination |
|---|---|---|---|
| `max_iterations.log` | **2** / 100k / 1 / 600 | max_iterations | 2 laps, 1,537 tok, 285.9 s |
| `max_tokens.log` | 50 / **1,200** / 1 / 600 | max_tokens | 1,537 tok after lap 2 |
| `max_cost_usd.log` | 50 / 100k / **0.0002** / 600 | max_cost_usd | $0.000274 after lap 2 |
| `max_wall_seconds.log` | 50 / 100k / 1 / **15** | max_wall_seconds | 15.3 s, lap 2 cut mid-generation |

Two things these runs found:

- **The first wall-clock capture overran 15 s by 63 s, and it was not the loop.**
  `budget-demo` did not warm the model up, so the ~50 s weight load landed
  inside lap 1 and was charged to the agent. With `warm_up()` first, as the race
  already did, it stops at 15.3 s.
- **Tokens and cost are checked between laps, so they overshoot by at most one
  lap** (1,537 against 1,200). Stopping mid-lap would need a pre-call estimate of
  the next prompt's size; the overshoot is bounded and logged rather than hidden.

## The third tool

`lookup_refund_policy(tier, order_status)` — one job, enum parameters, and a
description that names what it does *not* read. The before/after text, the
enums, and the measurement are in `week7/tool_description_diff.md`.

The honest finding: sharpening the description did **not** improve tool choice
(7/20 correct before and after). The overlap was real in the text, but it was
never what cost the agent anything — with the loose description the model
picked the wrong tool zero times out of twenty. Its failure is calling no tool
at all.

## Why the agent loses, precisely

Not tool confusion. At 300 s and 8 laps it still produces no final answer: it
spends laps re-reading the ticket, and each lap re-sends a transcript that is
longer than the last. The workflow needs one model call per ticket because the
policy arithmetic is code, and code does not need to be persuaded to do step 3.

**What this does not prove.** This is a 1.5-billion-parameter model on a laptop.
A frontier model would likely finish the loop and pass most of these tickets.
It would not change the shape of the result: the loop still costs 25× the tokens
for a task whose steps are known in advance.

## Bonus — memory over 30-turn threads

`src/agent_memory.py` adds both kinds of memory, and the same memory goes to
both systems:

- **Short term:** a 6-turn sliding window plus a rolling summary. When the
  window overflows, the oldest turns are folded into the summary by the same
  model. Every summary call is metered and charged to the system that reads it.
- **Long term:** `week7/memory/long_term.json`, keyed by customer. It is written
  only from a `find_ticket` result, never from what the customer says.

**The tier survives a full restart** (`week7/memory/restart_demo.log`).
Process A (pid 17320) resolves CD-7003 and writes `CUS-203 → priority`, then
exits. Process B (pid 18408), a fresh interpreter, reads it back from the file.

**The race on 3 × 30-turn threads** (`week7/long_race.csv`,
`runs/long_race_20261005-145612.json`). The budgets were the same as the main
race. The deciding detail sits in turns 2–9 of each thread, and the window
evicts all of those turns.

| system | pass | p50 latency | tokens | cost/ticket |
|---|---|---|---|---|
| agent (window + summary + LTM) | 1/3 | 70.6 s | 20,357 | $0.001276 |
| workflow (window + summary + LTM) | 2/3 | 6.5 s | 5,541 | $0.000470 |
| *control: workflow, full thread* | 3/3 | 7.1 s | 1,637 | $0.000102 |
| *control: workflow, no long-term memory* | 1/3 | 5.5 s | 5,507 | $0.000467 |

**The detail summarisation destroyed: order id `ORD-5101`. The ticket it broke:
CD-7101 (LT-1).** The customer gives the id once, in turn 3. After six
summarisation passes, the summary contains billing-email changes and a dashboard
slowdown, and no order id. The workflow's fixed extraction step then answered
`ORD-5001`, a real order belonging to a *different customer* (CUS-201), and
approved a $49 refund on it. Given the uncompressed thread, the same workflow
reads `ORD-5101` and passes. This is the worst kind of failure, because the
lost detail was replaced by a plausible wrong one. `get_order` does not check
that the order belongs to the ticket's customer. That ownership check is the
fix this run points to.

Two more failures the audit found:

- **The LT-3 summary kept `ORD-5113` but invented that its refund "has been
  processed successfully".** Both systems passed anyway, because they act on
  `get_order`, not on the summary.
- **Long-term memory is what passed LT-2.** The ticket record has no tier.
  Without the remembered `priority`, `lookup_refund_policy(null, …)` returns
  `bad_tier` and the workflow escalates. With it, the workflow approves $300 on
  the 60-day window.

**Memory cost.** Compression cost about 1,100 tokens per thread (6 summary
calls) to save about 320 tokens of context. The workflow reads the thread once,
so for the workflow compression costs more than it saves: 1,755 vs 711 tokens
on LT-1. Only the loop, which re-sends the context every lap, earns it back.

**Wall clock, again.** The agent on LT-2 ran for 801.9 s under a 300 s budget.
Lap 5 had about 6 s left, and its first forward pass took 507 s to produce
2 tokens. The workflow's 439 s reply call on the same thread shows the machine
stalled. The in-lap deadline stops generation between tokens. It cannot cut a
single forward pass short, so the wall clock is enforced only at token
granularity. A hard limit would need generation in a separate process that can
be killed.

**Code changed after the 10-ticket race.** The memory hooks are off unless a
thread or memory is passed, so the main race path is unchanged except for one
added evidence rule in `ticket_agent.unsupported_by_evidence`: refuse
`refund_approved` when `get_order` was never called. It can only reject an
answer the grader already fails, so the race table stands. It was not re-run.

No long-thread class forces an agent either. All three losses come from what
the memory kept, not from a path the code could not have known in advance.

## Files

| file | what |
|---|---|
| `store.json` | frozen tickets and orders the tools read |
| `race_set.jsonl` | the 10 ticket ids, expected outcome, and why each branches |
| `race.csv` / `race_summary.csv` | per-ticket rows and the 8 numbers |
| `race_budget120s.csv` | the middle run, kept for the budget comparison |
| `runs/race_*.json` | every run in full, including each agent transcript |
| `budget_termination.log` | one run terminating on a budget |
| `tool_description_diff.md` | the third tool's description, before and after, measured |
| `tool_probe_v1.json` / `_v2.json` | 20 decision points per description variant |
| `verdict.md` | the verdict, under 150 words |
| `DEMO.md` | what to show, in rubric order |
| `budget_logs/*.log` | one clean termination per budget |
| `long_threads.jsonl` | bonus: three 30-turn threads, expected outcomes, deciding details |
| `long_race.csv` / `long_race_summary.csv` | bonus: the long-thread race and its controls |
| `memory/long_term.json` / `memory/restart_demo.log` | bonus: the persisted tier and the two-process proof |

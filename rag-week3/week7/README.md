# Week 7 — Race the ticket agent against a fixed workflow

Same task, built twice: a tool-calling loop where the model picks the tools,
and three hard-coded steps. Same 10 tickets, same three tools, same model,
same output contract, same grader. Four numbers each decide it.

## One command each

```bash
python src/ticket_agent.py CD-7001          # the agent, one ticket, verbose
python src/ticket_workflow.py CD-7007       # the workflow, one ticket, verbose
python main.py race                         # both over all 10 -> week7/race.csv
python experiments/week7_race.py budget-demo --ticket CD-7008 --max-iterations 2
python experiments/week7_tool_probe.py --compare
```

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

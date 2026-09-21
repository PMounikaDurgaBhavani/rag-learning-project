# Week 7 demo — what to show, in rubric order

Terminal only. Have `week7/race_summary.csv` and `week7/verdict.md` open.
Nothing here is scored for polish; the numbers and the honesty are the marks.

## 0. The question (15 seconds)

> "I built the refund-chase resolver twice — as an agent loop and as three
> hard-coded steps — and raced them over the same ten tickets. Four numbers
> decide it, not my opinion."

## 1. Four numbers, both systems, same inputs — 30 marks

```bash
cat week7/race_summary.csv        # or: python experiments/week7_race.py table
```

Read the four out loud: **0/10 vs 10/10 · 338.6 s vs 3.8 s p50 · 44,748 vs
1,793 tokens · $0.000746 vs $0.000042 per ticket.**

Say why tokens are summed per lap: the loop re-sends the whole transcript every
lap, so counting only the last call would understate the agent by multiples.

## 2. The workflow does the same task — 20 marks

```bash
python src/ticket_workflow.py CD-7007     # order ORD-5099 does not exist
```

Point at step 2 returning `order_status: "missing"` and step 3 taking the
escalation branch. Same three tools, same model, same output contract as the
agent — and no loop: three calls, in order, every time.

Then the ticket with no order id at all:

```bash
python src/ticket_workflow.py CD-7008
```

"Step 2 is skipped by a fixed `if`, not retried."

## 3. All four budgets, and a clean termination — 20 marks

```bash
sed -n '1,20p' week7/budget_termination.log
```

Point at three lines: `lap 2 cut short by the wall-clock budget` (the deadline
inside generation), `BUDGET max_iterations hit (2 >= 2)`, and the recorded
spend. Say what the first race found: the wall clock used to be checked only
between laps, so one ticket spent **893 s under a 120 s budget**. That is why
the deadline is now passed into `generate()`.

## 4. The verdict — 20 marks

```bash
cat week7/verdict.md
```

The line that matters: *five of ten tickets branch after step 2, but a varying
path argues for a branch, not a loop.* Name what would force an agent — a chase
whose steps are not enumerable in advance — and say plainly that none of these
ten is that.

## 5. The third tool — 10 marks

```bash
python experiments/week7_tool_probe.py --compare
sed -n '1,40p' week7/tool_description_diff.md
```

Show the enums, then give the honest result: the sharpened description did not
improve tool choice (7/20 both ways), because with the loose description the
model picked the wrong tool **zero** times — it called no tool at all 13 times.
"I measured the fix I was told to expect, and it wasn't the bug I had."

## If asked: why is the agent so bad?

It is a 1.5B model on a laptop. Say so, and say what it does not change: the
loop still costs 25× the tokens for a task whose steps are known in advance. A
better model would move the pass rate, not the argument.

## Before the demo

```bash
LC_ALL=en_US.UTF-8 pg_ctl -D ~/pgdata/clouddesk -o "-p 5433" -l ~/pgdata/clouddesk/server.log start
python experiments/week7_race.py table    # confirm the table prints
```

Do not run `python main.py race` live — the agent arm takes about an hour on
this machine.

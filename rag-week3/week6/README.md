# Week 6 — Validate the ticket-reply judge before trusting its number

## The one command

```bash
python main.py eval            # drafts every reply fresh, runs 6 assertions + the judge, prints pass rate by mode
python main.py eval --frozen   # same, but reuses week6/replies_25.json (faster)
```

The judge only runs once the labelling protocol below is satisfied. Before
that, `eval` prints assertions only and says why the judge was skipped.

## The protocol (order matters, and the code enforces it)

| # | Step | Command | Commit |
|---|---|---|---|
| 1 | Freeze the 25 replies | `python experiments/week6_eval.py draft` | `week6/replies_25.json` |
| 2 | **You** label all 25 blind on RESOLUTION | `python experiments/week6_eval.py label` | `week6/labels_25.json` — **before step 3** |
| 3 | Judge v1 → `agreement_before` | `python experiments/week6_eval.py judge --version v1` | `judge_results_v1.json`, `agreement.json` |
| 4 | Write one sentence: what will adding 2 disagreements as examples fix? | edit `week6/prediction.txt` | `prediction.txt` — **before step 5** |
| 5 | Build v2 from two of v1's **own** disagreements | `python experiments/week6_eval.py make-judge-v2 --examples ID,ID` | `judge_v2.txt`, `judge_v2_examples.json` |
| 6 | Judge v2 → `agreement_after` | `python experiments/week6_eval.py judge --version v2` | `judge_results_v2.json`, `agreement.json` |
| 7 | Evidence table | `python experiments/week6_eval.py status` | `protocol_evidence.md` |
| 8 | Write the disagreement note: who was right, and where the prediction was wrong | edit `week6/disagreements.md` | |

What the tool refuses:

- `label` refuses if any judge result exists, or if complete labels are already committed. Relabelling moves the ruler.
- `judge` refuses until `labels_25.json` is committed, complete, and bound by sha to the committed `replies_25.json`.
- `make-judge-v2` refuses examples that v1 did not get wrong, and refuses to run before `prediction.txt` is committed.
- Labelling hides each case's mode and regression flag, and shuffles the order.

## Files

| File | What |
|---|---|
| `eval_cases.jsonl` | 25 tickets, each tagged with one Week 5 mode (M1 7 · M2 5 · M3 5 · M4 4 · M5 4); 4 regression cases (`R01`–`R04`) whose message is verbatim from a failed Week 5 trace |
| `judge_v0_presplit.txt` | the judge with all 7 criteria, before the split |
| `judge_v1.txt` | criteria 1–6 deleted; the judge grades **one** binary criterion, RESOLUTION |
| `replies_25.json` | the frozen replies that were labelled and judged |
| `labels_25.json` | hand labels (commit hash = ordering evidence) |
| `judge_results_v1.json` / `_v2.json` | every verdict, the judge's reason, the human label, and agreement |
| `agreement.json` | `agreement_before`, `agreement_after`, assertion count vs judged-criteria count |
| `prediction.txt` | one sentence, committed before v2 |
| `runs/` | every `eval` run, with per-case assertion and judge results |

## Assertions vs judged criteria

**6 deterministic assertions · 1 judged criterion.**

| Assertion (`src/ticket_assertions.py`) | Replaces v0 criterion | Applies when |
|---|---|---|
| `ticket_id_echoed` | 1. Ticket ID | always |
| `refund_amount_numeric` | 2. Refund amount | refund requested and eligible |
| `escalation_tag_iff_priority` | 3. Escalation | always (tag on Priority, no tag on Standard) |
| `no_refund_outside_window` | 4. Refund window | refund requested, > 30 days, not a duplicate charge |
| `not_cut_off` | 5. Complete | the model produced a reply (token count vs `max_new_tokens`) |
| `no_prompt_scaffolding` | 6. Clean | always |

Diff: `git diff --no-index week6/judge_v0_presplit.txt week6/judge_v1.txt`

## What changed in the app for this week

- **Ticket replies.** `src/ticket_reply.py` drafts a reply using the same retrievers, distance gate, and decoding settings as `ask`. The model is **Qwen2.5-1.5B-Instruct**, not the app's 0.5B. Under ticket prompts v1.0–v1.2 the 0.5B either emitted `NOT_IN_SOURCES` (20 of 25 tickets whose answering chunk was retrieved first) or echoed the ticket fields back, so nearly all 25 labels would have been FAIL and agreement would have meant nothing. `ask` and every Week 3–5 number still use the 0.5B. Override with `RAG_TICKET_LLM`. The policy the replies need (refund window, duplicate charges, Priority escalation) is a new article, `samples/article_08_refunds_escalation.md` (HC-008). It is imported into the corpus.
- **Langfuse.** Every answer, ticket draft, and rerun is sent to Langfuse as well as `traces/traces.jsonl`: retrieval and the LLM call become child observations. Eval runs attach scores to each reply trace: `assert:*`, `judge_v1`/`judge_v2`, `human_label`, `eval_pass`. Keys live in `.env`; `RAG_LANGFUSE=0` turns this off.
- **Week 5 rerun.** `python experiments/error_analysis.py rerun` re-asks the 20 sampled questions on today's build and prints then/now outcomes. The UI's *Error analysis → The 20 traces* tab has the same action as a button. `... error_analysis.py langfuse` pushes the 20 original traces to Langfuse so then and now can be compared there.

## Judge model

The default judge is `HuggingFaceTB/SmolLM2-1.7B-Instruct`, run locally (MPS, fp16) and greedy. It is a different model family from the Qwen2.5-1.5B that drafts the replies, so no model grades its own family's writing. Override it with `RAG_JUDGE_MODEL`. Any number reported here is for that judge model and the prompt sha recorded in `judge_results_*.json`.

Before any labels existed, the judge was smoke-tested on 3 hand-written replies, none of them among the 25. It returned parseable verdicts on all 3: it failed a promised out-of-window refund and a refused answerable question, and passed a correct password answer. That run checked only that the judge loads and follows the format. No verdict on any of the 25 replies existed before the labels were committed.

# Week 6 demo — what to show, in rubric order

Start: `python main.py ui` → sidebar **Judge validation**. Keep a terminal open next to it.

The rubric gives zero points for UI. The UI is only a way to *show* the evidence.
Every number in it is read from `week6/` and git, so point at the files as you go.

## 0. The problem, in one sentence (Overview tab)

> "The eval prints a resolution score from an LLM judge nobody checked. Before trusting it I measured it against my own blind labels."

Point at the four tiles:
- 25 cases
- 6 assertions vs 1 judged criterion
- latest pass rate
- agreement before → after

## 1. Blind protocol — 25 marks (Protocol evidence tab)

- The **Ordering checks** are computed live from git:
  - `labels_25.json` was committed **before** `judge_results_v1.json`
  - the labels' commit time is earlier than the judge run start recorded inside the results file
  - the labels were committed exactly once
- Terminal backup: `git log --oneline -- week6/labels_25.json week6/judge_results_v1.json`
- Say that the tooling refuses the wrong order. For example, `python experiments/week6_eval.py label` refuses once a judge result exists.

## 2. Agreement before → after — 30 marks (Agreement tab)

- `agreement_before` (v1), `agreement_after` (v2), **and the held-out figure**. Two of the 25 cases became few-shot examples, so scoring the judge on them is leakage. Say this before anyone asks.
- Confusion matrix: the red cells are the judge's false passes and false fails.
- Then the **Judge prompts** tab: **v1 → v2** shows the two added examples. Both are cases v1 got wrong.

## 3. Assertion/judge split — 20 marks (Judge prompts → v0 → v1, then Pass rate by mode)

- **v0 → v1** diff: the 6 deleted criteria. Name them: ticket ID, refund amount, escalation tag, refund window, cut off, leaked headers.
- In **Pass rate by mode**, the assertions table shows each check with the judge criterion it replaced, and which cases fail it.
- Worth telling: the refund-window check itself had a false positive (T04, T16), found and fixed before labelling. Deterministic doesn't mean correct.

## 4. Disagreement analysis + prediction — 15 marks (Agreement + Write-up tabs)

- Open two disagreements. Each shows the judge's reason and your labelling note. Say **who was right** and why (open the case in **The 25 cases** to show the reference facts).
- In the **Write-up** tab, read `prediction.txt` aloud, then say where it was wrong, using the v2 pills on each disagreement ("now agrees" / "still disagrees") and "cases v1 got right that v2 now gets wrong".

## 5. One command, 25+ mode-tagged cases, real regressions — 10 marks (terminal + Pass rate by mode)

- In the terminal, run `python main.py eval --frozen` live: the table prints pass rate by mode. Or press **Run eval** in the UI.
- Point at the warning box: **"The average hides M5"**. That's the rubric's common mistake, caught.
- The regression table shows R01–R04, each with its source Week 5 trace id and the original failure.

## Also available if asked

- **Langfuse:** each reply trace carries scores `assert:*`, `judge_v1`, `judge_v2`, `human_label`, `eval_pass`. Filter by session `week6-eval-…` or tag `regression`.
- **Week 5 rerun:** Error analysis → The 20 traces → **Rerun these 20 on the current build**.

## Before the demo

1. Do all the steps on the **Overview** tab until it says *All protocol steps complete*.
2. Run `python experiments/week6_eval.py status` and commit `week6/protocol_evidence.md`.
3. Postgres up: `LC_ALL=en_US.UTF-8 pg_ctl -D ~/pgdata/clouddesk -o "-p 5433" -l ~/pgdata/clouddesk/server.log start`
4. Restart `python main.py ui` so it serves the new code.
5. Press **Run eval (assertions + judge v2)** once beforehand; a judge run takes a few minutes, so don't wait on it live.

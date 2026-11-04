# Trajectory evaluation on the integrated agent — Week 12

Generated 2026-11-04T12:34:29Z from `week12/trajectory_eval.json` (run `week12/runs/v1.1-model_20261104-160849.json`, planner `model`, scored 2026-11-04T12:32:11Z). Scorer: `experiments/week12_eval.py::score_trajectory`, the Week 8 method over the new tools.

## Numbers

| cases | tool choices (model) | correct | **tool-choice accuracy** | correct ordering | path complete | outcome pass | trajectory pass | **gap (outcome − trajectory)** |
|---|---|---|---|---|---|---|---|---|
| 12 | 32 | 31 | **96.9%** | 100.0% | 91.7% | 83.3% | 66.7% | **+16.6 pp** |

- Right answer on the wrong path: 2 · wrong answer on the right path: 0
- Model-chosen tool steps: 32 · guard-forced steps: 4
- Mode totals: `forced_by_guard` 4, `prose_instead_of_action` 2, `budget_exhausted` 2, `wrong_or_out_of_order` 1

Definitions: **tool_choice_accuracy** — model-chosen tool calls that were the next step of an accepted sequence given what was already done; guard-forced calls are not the model's choices and are excluded from numerator and denominator; **trajectory_pass** — the completed tools form an accepted sequence, nothing was out of order, and no required tool had to be forced by the guard; **outcome_pass** — decision and outcome equal the expected ones in week12/eval_cases.jsonl; **gap** — outcome pass rate minus trajectory pass rate (Week 8 definition)

## Per case

| case | expected sequence(s) | actual (`*` = forced by the guard) | tool choice | order | outcome | trajectory | laps | trace |
|---|---|---|---|---|---|---|---|---|
| W6-T03 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre | 3/3 | ✓ | ✓ | ✓ | 3 | `323b51d6b94d4655` |
| W6-T04 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre* | 2/2 | ✓ | ✓ | ✗ | 3 | `d1c0f93c315747ba` |
| W6-T07 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre* | 2/2 | ✓ | ✓ | ✗ | 3 | `c656fd42a80d4414` |
| W6-T09 | lookup_order → check_entitlement → search_help_centre | search_help_centre → escalate_ticket* | 0/1 | ✓ | ✗ | ✗ | 1 | `d7eb34edaeea44a7` |
| W6-T13 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre | 3/3 | ✓ | ✓ | ✓ | 3 | `783d049e0ccc4b36` |
| W6-T16 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre | 3/3 | ✓ | ✓ | ✓ | 3 | `5e9e8a75b9c04e57` |
| W6-T21 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre | 3/3 | ✓ | ✓ | ✓ | 3 | `069c44ecbcbd4d2c` |
| W6-R05 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre → escalate_ticket* | 3/3 | ✓ | ✗ | ✗ | 3 | `495b35dd7d5d4b51` |
| C-12001 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre | 3/3 | ✓ | ✓ | ✓ | 3 | `bc4fe48ba9d74efe` |
| C-12002 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre | 3/3 | ✓ | ✓ | ✓ | 3 | `8e6106f07f8c4d56` |
| C-12005 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre | 3/3 | ✓ | ✓ | ✓ | 3 | `e1cef0ea5e004ef4` |
| C-12010 | lookup_order → check_entitlement → search_help_centre | lookup_order → check_entitlement → search_help_centre | 3/3 | ✓ | ✓ | ✓ | 3 | `fef6f82b792e43db` |

<!-- HAND-WRITTEN: start -->

## Reading

**What was run.** The 12 policy cases (every case whose expected path is `lookup_order → check_entitlement → search_help_centre`), with the model choosing the tools (`--planner model`), capstone-v1.1, on 2026-11-04 16:08–16:59 IST. The other 28 cases of the suite were run in fast mode (code-planned tools) and are not trajectory-scored: a code-planned path says nothing about the model's choices.

**Tool choice is not the problem any more.** Qwen2.5-1.5B picked the right next tool in 31 of its 32 choices and never called the tools out of order. That is the opposite of Week 7 (where the same model mostly called no tool at all) and the reason is not a smarter model: the tool set is smaller and explicit, the planner prompt names the required checks for the ticket, and every lap sees the tool results so far. The one wrong choice was on T09, where the rule-based understanding step told the model the required check was only a help-centre search, so the model searched. The wrong choice is upstream of the model.

**The gap is +16.7 pp and it is made of two things.**

1. *The guard finishing the path* (T04, T07): the model had chosen `lookup_order` and `check_entitlement` correctly, then the third lap could not start before the loop's 300 s wall clock, and the guard ran `search_help_centre` itself. The outcome is right; the trajectory is scored as a fail because a required tool was forced. Both laps that overran were model calls of 100–170 s for ~35 tokens, which is the machine (8 GB, swapping under other applications), not the model's reasoning. On a quiet machine the same laps took 15–60 s in the smoke runs.
2. *Right path, no deflection* (R05): all three tools, in order, with grounded arguments, and the search the model issued returned no passage of the account's policy version, so the decision step escalated ("the policy cannot be cited"). This is the designed behaviour and it is still a miss: the customer got a hand-off for a question the system answered correctly in fast mode an hour earlier (the fast-mode search used the ticket text verbatim; the model's query was shorter). It is counted as an integration-created error mode in `reports/error_taxonomy.md`.

**Right answer on the wrong path: 2 (T04, T07). Wrong answer on the right path: 1 (R05).** Week 8's time bomb (a refund approved without opening the order) cannot happen here: the decision is computed from the entitlement, not from the model's text, and a missing tool is forced before any decision. The remaining risk is the opposite one, visible in R05: a correct path that ends in an unnecessary escalation.

**What this does not measure.** Twelve cases, one model, one machine. Latency figures here (p50 237 s) are not the fast-mode figures the UI defaults to (`reports/cost_and_scaling.md`). The escalate-directly cases (legal, PII, data deletion, out of scope) were excluded on purpose: their expected path has no model choice in it.

<!-- HAND-WRITTEN: end -->

# Three known weaknesses — Wednesday 2026-11-04

Written on Wednesday 2026-11-04 (the day the integration was built and first measured), from the
smoke runs in `week12/runs/smoke/` and the first evaluation runs. Each weakness names the evidence,
what it costs, and what would fix it. The evaluation report (`reports/integrated_eval_report.md`)
carries the measured numbers.

## 1. Latency on this hardware makes the "agent loop" mode impractical for live use

**Evidence (measured).** Fast-mode run of 40 cases: p50 96 s, mean 146 s, p95 382 s per request;
9 replies were cut off by the 300 s generation deadline after 4–12 tokens because the 8 GB M1 laptop
was swapping under other applications. Model-planner run of 12 cases: p50 237 s, max 460 s; two
cases hit the loop's 300 s wall clock before the third tool. When the machine was quiet the same
laps took 15–60 s. The server answers one model-backed request at a time.

**Cost.** A customer waiting 2–5 minutes in the chatbot; an evaluation suite that takes an hour; the
"fast mode" (code-planned tools, model writes only the reply) is what the inbox and chat default to,
so the trajectory numbers in `reports/trajectory_eval.md` describe a mode the UI does not use by
default.

**Fix.** A worker pool behind a queue (the server already returns 409 instead of queueing) and a
faster generation endpoint; see `reports/cost_and_scaling.md`. The decision logic does not change:
it is code either way.

## 2. The judge is not validated on the integrated replies, and its only validation is below target

**Evidence.** Judge v2's agreement with blind hand labels is 76.0% on the 25 frozen Week 6 replies
(78.3% held out, κ 0.41, `week6/agreement.json`), below the 85% target. The integrated system
produces different replies, for which no hand labels exist yet; `eval judge` writes a blind
labelling sheet (`week12/labels_template.json`) but a person has to fill it. Two judge attempts on the
integrated replies (16:59 and 17:40 IST) produced one verdict in total before stalling under swap; the
run file records both attempts and the command resumes.

**Cost.** Every judge number on the integrated outputs is unvalidated. The dashboard therefore shows
the judge pass rate and its agreement with the deterministic assertions as a *proxy*, labelled as
such, and shows "pending" for agreement with hand labels.

**Fix.** Label the 40 integrated replies blind (the sheet exists), rerun `eval report`, and if the
judge stays below 85%, iterate the judge prompt on its own disagreements as Week 6 did — never on
the replies it will later grade.

## 3. The 1.5B drafter follows formatting rules loosely, so safety rests on code, not on the model

**Evidence (run `v1.1_20261104-143011`, 40 cases).** The drafter wrote a `[n]` citation marker in
2 of 31 answered replies (so "cited" is usually empty while "consulted" is full), dropped the
`[ESCALATED-PRIORITY]` tag in the raw draft on 10 Priority tickets, opened with "Customer Inquiry /
Dear Customer" boilerplate and hit the 160-token limit 5 times, and needed the guardrail's second
attempt twice (once on a false positive, fixed since). In the model-planner run the guard had to
finish the tool sequence in 2 of 12 cases, both after wall-clock overruns.

**Cost.** Citations in the UI are honest but less specific than they could be (the sources
consulted are shown; the ones the model marked are fewer). Priority tagging is applied by code after
the assertions measure the raw draft and is recorded as `post_processing`. Any rule that matters
(no refund without entitlement, right policy version, no PII, no "processed" claim) is enforced by
an assertion plus the output guardrail, which escalates when the second draft still fails.

**Fix.** A stronger drafting model, or a constrained output format (a short structured reply the
code renders) so citations and tags are never left to free text. The assertion suite stays as the
regression net either way.

# Dated prediction — committed before any fix

**Date:** 2026-09-08
**Committed as:** `c152efb` (2026-09-08 13:01:01 +0530)
**App build at time of prediction:** `efd07ba`, prompt `v1.0` (sha `21d158d26510e17c`)
**Baseline sample:** seed `20260907`, 20 traces of 99

## The mode I am attacking

**Mode 1 — "Says 'I don't have enough information' to a question the
articles do answer."** Currently **4 / 20 = 20%** of sampled traces.

## The change I will make

Raise `RELEVANCE_THRESHOLD` in `src/generator.py` from **0.75 to 0.85**.
Nothing else — no reranker change, no prompt edit, no chunking change.

Chosen because it is the one trigger in this mode with a measured number
against it: in `5a50d54f665441d4` the chunk that answers the question was
ranked **first** at 0.7659 and the generator was never called, and three
questions in the Week 4 golden set fail the same way at 0.766–0.788. A gate
at 0.85 admits all of them.

## What I expect, in numbers

> Raising the distance gate from 0.75 to 0.85 drops mode 1 from **20%** to
> **under 10%** on a fresh seeded sample of 20 traces drawn after the
> change — that is, at most 1 of 20, down from 4.

## How it gets measured

- Same rule, new seed: `20260915`
- 20 traces drawn from traffic recorded **after** the change
- Same open-coding pass, same five mode definitions as this week
- Mode 1 counted present/absent per trace, exactly as this week

## What would prove me wrong

Any of:

1. Mode 1 stays at or above 10% (2+ of 20).
2. Correct refusals fall below **4 / 20**. This week the system correctly
   refused pricing, Salesforce and failed-attempt-count questions; a gate
   at 0.85 admits chunks up to 0.85, and `50e50263fa0d42dd` refused at
   0.7744 — under the new gate it would reach the generator. If the system
   starts answering questions the corpus does not cover, the change has
   traded a mild annoyance for an answer that embarrasses the client.
3. A new mode appears at higher frequency than the one I fixed.

## Side effects I will also check

- Mode 3 ("reply stops mid-sentence") — more questions reaching the
  generator means more chances to hit the 160-token limit, so this could
  rise even though I did not touch it.
- The Week 4 golden set: hit-rate@3 is a retrieval metric and must stay at
  75.0%. If it moves, I changed more than one thing.

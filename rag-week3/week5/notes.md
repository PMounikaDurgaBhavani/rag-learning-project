# Week 5 — Open coding notes

**Seed:** `20260907` · **Sample:** 20 of 99 traces
**Selection rule:** `random.Random(20260907).sample(traces sorted by trace_id, 20)`
**Read on:** 2026-09-08 · app build `efd07ba` · prompt `v1.0` (sha `21d158d26510e17c`)
**Code changed during this pass: none.**
**Committed as:** `c152efb`

One sentence per trace describing what was seen. Not a category, not a
diagnosis, not a fix.

---

### The 20 sampled trace_ids

```
52df90703dac44be  4feb2225e1a746b4  9178a5b4424d4833  50e50263fa0d42dd
f1423117df0949ac  ca06d21aa6fc4204  5a50d54f665441d4  99bd69c5501749e4
5b4a156d4b2a412e  a7051c474be5456e  2d540dfaff77426b  55dc68f2276e4924
481c82d2b24a4c26  7fe81eaa6cdf466a  b11ac98da8764b29  66c2277862c449d4
c0579a1772764098  8d306c74d9c1499c  0397331973e145a3  59b27d3b9fdd4225
```

---

## Observations

**1. `52df90703dac44be`** — Asked what to do about an account locked by
incorrect password attempts, the reply said attempts should be avoided
until the lock expires and listed no recovery action, citing one chunk.

**2. `4feb2225e1a746b4`** — The model's entire output was the characters
`[1]` with no sentence around it, and the user was shown "I don't have
enough information to answer that."

**3. `9178a5b4424d4833`** — A lowercase message with no punctuation,
"why cant i see the upload button", was answered with the permission
explanation and three citations.

**4. `50e50263fa0d42dd`** — A question about Salesforce integration was
refused with the nearest chunk at 0.7744; the top result was the billing
article overview and no article mentions integrations.

**5. `f1423117df0949ac`** — "notifications stopped working yesterday for
the whole team" retrieved three notification-troubleshooting chunks
between 0.63 and 0.75, and the model replied `NOT_IN_SOURCES`.

**6. `ca06d21aa6fc4204`** — The password-reset-validity question returned
"30 minutes" with one citation, nearest chunk at 0.375.

**7. `5a50d54f665441d4`** — The password character-requirements question
put `HC-001-markdown-6` first at 0.7659 and was refused before the model
ran, because 0.7659 is above the 0.75 gate.

**8. `99bd69c5501749e4`** — The account-lock-duration question returned
"A temporary CloudDesk account lock lasts 15 minutes." with one citation.

**9. `5b4a156d4b2a412e`** — "How many failed login attempts lock a
CloudDesk account?" retrieved the lockout chunks at 0.29 and the model
replied `NOT_IN_SOURCES`; the articles say "multiple" and never give a
number.

**10. `a7051c474be5456e`** — The single word "billing" retrieved three
billing chunks between 0.88 and 0.95 and was refused by the gate.

**11. `2d540dfaff77426b`** — The same "why cant i see the upload button"
message, run in a different retrieval mode, produced a longer sentence
naming the Upload File button, with three citations.

**12. `55dc68f2276e4924`** — The model wrote "The Upload File button is
unavailable according to HC-006 troubleshooting because users lack
required permission" and the user was shown the refusal message instead,
with no citation.

**13. `481c82d2b24a4c26`** — The Salesforce question again, in a different
retrieval mode, refused at the same 0.7744 with a different second and
third result.

**14. `7fe81eaa6cdf466a`** — The reply began "Follow these steps:" and
then reproduced a source header line — `[2] (article HC-002, file
article_02.md, updated 2026-08-05)` — before starting a numbered list that
stopped at "2"; the user was shown the refusal message.

**15. `b11ac98da8764b29`** — A message asking two things at once was
answered with "The storage limit is not specified in the given sources.
However, based on the information provided:" followed by a list that stops
mid-sentence, and the citation points at a chunk that was not among the
top three retrieved.

**16. `66c2277862c449d4`** — The password-reset-validity question again,
different retrieval mode, returned "30 minutes" with three citations
instead of one.

**17. `c0579a1772764098`** — "How long do I have to wait after too many
login attempts?" retrieved the lockout chunks at 0.78 and the model
replied `Not_in_sources` in mixed case, which the system accepted as a
refusal.

**18. `8d306c74d9c1499c`** — "whats the max file size and which formats
are allowed?" asked two things; the model replied `NOT_IN_SOURCES` and
neither part was answered, though the allowed formats are listed in
HC-006.

**19. `0397331973e145a3`** — A question about Enterprise plan pricing was
refused with the nearest chunk at 1.2601; no article covers pricing.

**20. `59b27d3b9fdd4225`** — For an expired card and failed payment, the
reply listed steps referring to insufficient funds and a card being
locked, neither of which appears in the billing article, and stopped
mid-word at "loc"; the user was shown the refusal message.

---

## Traces where nothing went wrong

Nine of the twenty: **3, 6, 8, 11, 16** answered correctly with citations,
and **4, 9, 13, 19** refused questions the corpus genuinely does not
answer (Salesforce integration, a failed-attempt count, pricing).

## What I could not tell from the trace alone

- Whether the two-part questions (**15**, **18**) would have been answered
  had they been asked as two separate messages — nothing in the trace says.
- Why **2** produced only `[1]`. The prompt and sources look no different
  from traces that answered normally. I don't know why this one failed.

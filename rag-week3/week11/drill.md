# Week 11 — Support drill: "A customer said it gave a wrong answer about refunds yesterday afternoon."

Generated 2026-10-25T11:05:04+00:00 by `python experiments/week11_report.py`.
Every time below is a wall-clock measurement of the search procedure on this machine, taken when this file was
written. Re-run the script (or press **Find incident** in *Observability → Support drill*) to measure again.

## 1. Time to find

| | procedure 1 — slices | procedure 2 — indexed field (bonus) |
|---|---|---|
| time to find (server, mm:ss) | **00:00** (35.11 ms) | **00:00** (7.13 ms) |
| started → completed (UTC) | 2026-10-25T11:05:04.900+00:00 → 2026-10-25T11:05:04.938+00:00 | 2026-10-25T11:05:04.938+00:00 → 2026-10-25T11:05:04.946+00:00 |
| answers read and classified | 50 | 16 (verdict read from the column) |
| matches | 16 | 16 |
| window actually used | widened: 7 days before as_of, up to now | widened: 7 days before as_of, up to now |

Both are measured; neither is a "five-minute pass" claim. On a log of 364 requests the whole
procedure takes milliseconds either way; what the second procedure removes is the per-candidate read-and-classify
step, which is the part that grows with traffic.

## 2. Search slice

- **Complaint handled on (as-of):** 2026-10-25 → "yesterday afternoon" = **2026-10-24T12:00:00+05:30 to 2026-10-24T18:00:00+05:30** (IST),
  i.e. 2026-10-24T06:30:00+00:00 to 2026-10-24T12:30:00+00:00 UTC. The as-of date is an input to the procedure, so the window is reproducible.
- **Time:** the window above; if it holds no request the procedure widens to the 7 days before as-of, then to all recorded time, and says so.
- **Topic:** "refund" in the **input OR the generated output** (FTS5 over both columns). The complaint describes the answer, not the question.
- **User:** not available — the log has no customer field (listed under missing fields).
- **Prompt version / input type:** carried on every record and shown on each match; not needed to narrow this search.
- **Cost outlier:** available as a filter in the explorer; not needed here.
- **Wrongness:** for every candidate with charge facts, the answer is checked against HC-008 by code
  (`observability.policy_verdict`, the same classifier as the Week 6 assertions `no_refund_outside_window`
  and the new `no_denial_inside_window`): eligible → must not deny; ineligible → must not promise.

### Procedure 1 — slices, in order

| # | slice | filter | hits | time |
|---|---|---|---|---|
| 1 | time: complaint window | `ts in [2026-10-24T06:30:00+00:00, 2026-10-24T12:30:00+00:00]` | 0 | 0.41 ms · no requests recorded in this window |
| 2 | time: widened: 7 days before as_of, up to now | `ts in [2026-10-17T18:30:00+00:00, now]` | 140 | 0.34 ms |
| 3 | topic in input OR output (widened: 7 days before as_of, up to now) | `FTS5 MATCH '"refund"* OR "refunds"* OR "refunded"* OR "refundable"*' AND ts in window` | 50 | 7.24 ms |
| 4 | classify answers against HC-008 (widened: 7 days before as_of, up to now) | `for each candidate: eligible -> must not deny; ineligible -> must not promise` | 16 | 26.88 ms · 50 candidate answers read and classified |

### Procedure 2 — indexed `policy_verdict` (the field added after drill 1)

| # | slice | filter | hits | time |
|---|---|---|---|---|
| 1 | time: complaint window | `ts in [2026-10-24T06:30:00+00:00, 2026-10-24T12:30:00+00:00]` | 0 | 0.39 ms · no requests recorded in this window |
| 2 | time: widened: 7 days before as_of, up to now | `ts in [2026-10-17T18:30:00+00:00, now]` | 140 | 0.32 ms |
| 3 | policy_verdict = contradicts (widened: 7 days before as_of, up to now) | `policy_verdict = 'contradicts' AND ts in window (indexed column, no text scan)` | 16 | 0.66 ms |

## 3. Incident source and findings

- **Recorded in normal use** — trace `c6885e014a464c22`, request `not recorded (pre-Week 11 record)`, 2026-10-21T00:21:09+05:30 local (2026-10-20T18:51:09.072+00:00)
  - path `ticket_reply` · source `week10-single` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `7e44499`
  - ticket CD-10403 · $49.0 charged 12 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'In this case, since the charge was made 12 days ago, it falls outside the eligible timeframe for a full refund.'
  - retrieved context ids: `HC-008-markdown-2`, `HC-008-markdown-5`, `HC-008-markdown-13`, `HC-008-markdown-1`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10403: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your recent charge. According to our policy, subscriptions cannot be fully refunded if they were canceled before the 30-day eligibility period has elapsed. In this case, since the charge was made 12 days ago, it falls outside the eligible timeframe for a full refund.
    
    However, there is an exception to this rule: if you have received duplicate charges due to incorrect billing, those duplicates would be refunded in full. Please note that duplicate charges occur when the same invoice is charged multiple times, which is covered by our FAQs.
    
    For your specific situation, please understand that the charge of $49.00 was incurred prior to any cancellation of the subscription. Therefore, based on our current policy
    ```

- **Recorded in normal use** — trace `482c2814d6cb445a`, request `not recorded (pre-Week 11 record)`, 2026-10-21T00:31:52+05:30 local (2026-10-20T19:01:52.163+00:00)
  - path `ticket_reply` · source `week10-single` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `7e44499`
  - ticket CD-10421 · $30.0 charged 29 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'In your case, since the charge was made exactly 30 days ago, it falls outside the eligibility window for a standard refund.'
  - retrieved context ids: `HC-008-markdown-13`, `HC-008-markdown-3`, `HC-008-markdown-5`, `HC-008-markdown-2`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10421: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your recent charge. We understand that an error occurred, and we apologize for any inconvenience this may have caused.
    
    According to our policy, charges made more than 30 days prior to the refund request are not eligible for a refund. However, if the same invoice was charged multiple times, each duplicate charge would be refunded in full.
    
    In your case, since the charge was made exactly 30 days ago, it falls outside the eligibility window for a standard refund. However, based on the information provided, there were no duplicates involved in this transaction.
    
    To proceed, please provide us with your billing details so we can verify the correct amount and ensure proper processing. Once verified, we will initiate the
    ```

- **Recorded in normal use** — trace `fc94918f71df466f`, request `not recorded (pre-Week 11 record)`, 2026-10-21T08:27:03+05:30 local (2026-10-21T02:57:03.832+00:00)
  - path `week10_multi_agent` · source `week10-multi` · prompt `week10-multi-v1.1` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `7e44499`
  - ticket CD-10403 · $49.0 charged 12 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'Based on our review of the policy and the information provided, your charge of $49.00, which was made 12 days ago, falls outside the eligibility criteria for a refund as per the established policy.'
  - retrieved context ids: `HC-008-markdown-2`, `HC-008-markdown-5`, `HC-008-markdown-13`, `HC-008-markdown-1`, `HC-008-markdown-6`, `HC-008-markdown-3`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10403: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your recent charge. Based on our review of the policy and the information provided, your charge of $49.00, which was made 12 days ago, falls outside the eligibility criteria for a refund as per the established policy.
    
    However, please note that if you have duplicate charges due to an error, those charges will be refunded in full. In your case, since you were charged $49.00 for a subscription you had already cancelled, this situation does not meet the conditions for a refund.
    
    We understand that this may cause inconvenience, but based on the current policy, we cannot process a refund for the charge you received.
    
    Please rest assured that we will keep your details
    ```

- **Recorded in normal use** — trace `12b3c24bd1c0417f`, request `not recorded (pre-Week 11 record)`, 2026-10-21T08:42:23+05:30 local (2026-10-21T03:12:23.105+00:00)
  - path `ticket_reply` · source `week10-single` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `7e44499`
  - ticket CD-10403 · $49.0 charged 12 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'In this case, since the charge was made 12 days ago, it falls outside the eligible timeframe for a full refund.'
  - retrieved context ids: `HC-008-markdown-2`, `HC-008-markdown-5`, `HC-008-markdown-13`, `HC-008-markdown-1`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10403: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your recent charge. According to our policy, subscriptions cannot be fully refunded if they were canceled before the 30-day eligibility period has elapsed. In this case, since the charge was made 12 days ago, it falls outside the eligible timeframe for a full refund.
    
    However, there is an exception to this rule: if you have received duplicate charges due to incorrect billing, those duplicates would be refunded in full. Please note that duplicate charges occur when the same invoice is charged multiple times, which is covered by our FAQs.
    
    For your specific situation, please understand that the charge of $49.00 was incurred prior to any cancellation of the subscription. Therefore, based on our current policy
    ```

- **Recorded in normal use** — trace `9a51ab3e3ba443e9`, request `not recorded (pre-Week 11 record)`, 2026-10-21T08:51:20+05:30 local (2026-10-21T03:21:20.502+00:00)
  - path `ticket_reply` · source `week10-single` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `7e44499`
  - ticket CD-10421 · $30.0 charged 29 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'In your case, since the charge was made exactly 30 days ago, it falls outside the eligibility window for a standard refund.'
  - retrieved context ids: `HC-008-markdown-13`, `HC-008-markdown-3`, `HC-008-markdown-5`, `HC-008-markdown-2`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10421: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your recent charge. We understand that an error occurred, and we apologize for any inconvenience this may have caused.
    
    According to our policy, charges made more than 30 days prior to the refund request are not eligible for a refund. However, if the same invoice was charged multiple times, each duplicate charge would be refunded in full.
    
    In your case, since the charge was made exactly 30 days ago, it falls outside the eligibility window for a standard refund. However, based on the information provided, there were no duplicates involved in this transaction.
    
    To proceed, please provide us with your billing details so we can verify the correct amount and ensure proper processing. Once verified, we will initiate the
    ```

- **Recorded in normal use** — trace `0d893b1f15b74d8d`, request `not recorded (pre-Week 11 record)`, 2026-10-22T16:26:23+05:30 local (2026-10-22T10:56:23.216+00:00)
  - path `week10_multi_agent` · source `week10-ui-multi` · prompt `week10-multi-v1.1` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-10403 · $49.0 charged 12 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'Based on our review of the policy and the information provided, your charge of $49.00, which was made 12 days ago, falls outside the eligibility criteria for a refund as per the established policy.'
  - retrieved context ids: `HC-008-markdown-2`, `HC-008-markdown-5`, `HC-008-markdown-13`, `HC-008-markdown-1`, `HC-008-markdown-6`, `HC-008-markdown-3`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10403: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your recent charge. Based on our review of the policy and the information provided, your charge of $49.00, which was made 12 days ago, falls outside the eligibility criteria for a refund as per the established policy.
    
    However, please note that if you have duplicate charges due to an error, those charges will be refunded in full. In your case, since you were charged $49.00 for a subscription you had already cancelled, this situation does not meet the conditions for a refund.
    
    We understand that this may cause inconvenience, but based on the current policy, we cannot process a refund for the charge you received.
    
    Please rest assured that we will keep your details
    ```

- **Controlled incident** — trace `2fa07536c6a74c3a`, request `req-9bf81bba51ab`, 2026-10-25T14:36:33+05:30 local (2026-10-25T09:06:33.812+00:00)
  - path `ticket_reply` · source `week11-incident` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-11901 · $120.0 charged 14 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'Since your charge was made 14 days ago, this transaction falls outside the 30-day window.'
  - retrieved context ids: `HC-008-markdown-2`, `HC-008-markdown-3`, `HC-008-markdown-4`, `HC-008-markdown-5`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-11901: Hi there,
    
    Thank you for reaching out about your refund request. Based on our policy, charges made more than 30 days prior to the refund request are not eligible for a refund. Since your charge was made 14 days ago, this transaction falls outside the 30-day window.
    
    However, if you have turned off automatic renewal through Organization Settings > Billing, the next charge should not occur, preventing any further charges. To ensure no additional charges, please confirm that you've disabled automatic renewal.
    
    In case you need assistance turning off automatic renewal, feel free to reach out again. We'll guide you through the process.
    
    Best regards,
    CloudDesk Support Team
    ```

- **Controlled incident** — trace `4561826fb7084e8d`, request `req-a5cf1f7bda88`, 2026-10-25T14:38:58+05:30 local (2026-10-25T09:08:58.666+00:00)
  - path `ticket_reply` · source `week11-incident` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-11902 · $89.0 charged 40 days ago · duplicate · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'Since the charge was made 40 days ago, it falls outside the 30-day eligibility window for a refund.'
  - retrieved context ids: `HC-008-markdown-5`, `HC-008-markdown-13`, `HC-008-markdown-6`, `HC-008-markdown-2`, `HC-008-markdown-3`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-11902: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your duplicate charge issue. According to our policy, duplicate charges are the only exceptions where a refund may be processed. Since the charge was made 40 days ago, it falls outside the 30-day eligibility window for a refund.
    
    We apologize for any inconvenience caused by this situation. However, based on our policy, we cannot process a refund for this charge due to its age.
    
    Please note that approved refunds are typically returned to the original payment method within 5 to 10 business days. You may check your bank statements or contact your financial institution directly if you need further assistance.
    
    If you have any questions about your billing or would like to discuss other options, please don't hesitate to
    ```

- **Recorded in normal use** — trace `94e2674ba16a46cf`, request `req-6ac0aa603f25`, 2026-10-25T14:56:39+05:30 local (2026-10-25T09:26:39.004+00:00)
  - path `ticket_reply` · source `week6-eval` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-10403 · $49.0 charged 12 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'In this case, since the charge was made 12 days ago, it falls outside the eligible timeframe for a full refund.'
  - retrieved context ids: `HC-008-markdown-2`, `HC-008-markdown-5`, `HC-008-markdown-13`, `HC-008-markdown-1`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10403: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your recent charge. According to our policy, subscriptions cannot be fully refunded if they were canceled before the 30-day eligibility period has elapsed. In this case, since the charge was made 12 days ago, it falls outside the eligible timeframe for a full refund.
    
    However, there is an exception to this rule: if you have received duplicate charges due to incorrect billing, those duplicates would be refunded in full. Please note that duplicate charges occur when the same invoice is charged multiple times, which is covered by our FAQs.
    
    For your specific situation, please understand that the charge of $49.00 was incurred prior to any cancellation of the subscription. Therefore, based on our current policy
    ```

- **Recorded in normal use** — trace `a5c4b9b01dbf4a98`, request `req-165f41b839d9`, 2026-10-25T15:03:10+05:30 local (2026-10-25T09:33:10.617+00:00)
  - path `ticket_reply` · source `week6-eval` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-10413 · $19.99 charged 5 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'For your specific case, since the charge was made less than 30 days ago, we cannot provide a refund at this time.'
  - retrieved context ids: `HC-008-markdown-6`, `HC-008-markdown-3`, `HC-008-markdown-2`, `HC-008-markdown-5`, `HC-008-markdown-13`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10413: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your refund request. According to our policy, charges made more than 30 days prior cannot be refunded. However, if there were any duplicate charges due to an error, they would be refunded in full.
    
    For your specific case, since the charge was made less than 30 days ago, we cannot provide a refund at this time. We recommend checking your billing history for any other duplicate charges.
    
    The entire refund process typically takes approximately 5 to 10 business days. Once we receive your request, we will initiate the refund process and notify you via email when it has been processed.
    
    Please note that duplicate charges are handled differently; if you have any questions about those, feel free to
    ```

- **Recorded in normal use** — trace `0d94fa7275524212`, request `req-ee8983831563`, 2026-10-25T15:07:41+05:30 local (2026-10-25T09:37:41.297+00:00)
  - path `ticket_reply` · source `week6-eval` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-10421 · $30.0 charged 29 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'In your case, since the charge was made exactly 30 days ago, it falls outside the eligibility window for a standard refund.'
  - retrieved context ids: `HC-008-markdown-13`, `HC-008-markdown-3`, `HC-008-markdown-5`, `HC-008-markdown-2`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10421: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your recent charge. We understand that an error occurred, and we apologize for any inconvenience this may have caused.
    
    According to our policy, charges made more than 30 days prior to the refund request are not eligible for a refund. However, if the same invoice was charged multiple times, each duplicate charge would be refunded in full.
    
    In your case, since the charge was made exactly 30 days ago, it falls outside the eligibility window for a standard refund. However, based on the information provided, there were no duplicates involved in this transaction.
    
    To proceed, please provide us with your billing details so we can verify the correct amount and ensure proper processing. Once verified, we will initiate the
    ```

- **Recorded in normal use** — trace `d255432b39eb4fed`, request `req-c6bcbe494a29`, 2026-10-25T15:08:16+05:30 local (2026-10-25T09:38:16.084+00:00)
  - path `ticket_reply` · source `week6-eval` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-11901 · $120.0 charged 14 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'Since your charge was made 14 days ago, this transaction falls outside the 30-day window.'
  - retrieved context ids: `HC-008-markdown-2`, `HC-008-markdown-3`, `HC-008-markdown-4`, `HC-008-markdown-5`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-11901: Hi there,
    
    Thank you for reaching out about your refund request. Based on our policy, charges made more than 30 days prior to the refund request are not eligible for a refund. Since your charge was made 14 days ago, this transaction falls outside the 30-day window.
    
    However, if you have turned off automatic renewal through Organization Settings > Billing, the next charge should not occur, preventing any further charges. To ensure no additional charges, please confirm that you've disabled automatic renewal.
    
    In case you need assistance turning off automatic renewal, feel free to reach out again. We'll guide you through the process.
    
    Best regards,
    CloudDesk Support Team
    ```

- **Recorded in normal use** — trace `d2163b7c10694165`, request `req-e3e1e4c4839e`, 2026-10-25T15:58:21+05:30 local (2026-10-25T10:28:21.588+00:00)
  - path `ticket_reply` · source `week6-eval` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-10403 · $49.0 charged 12 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'In this case, since the charge was made 12 days ago, it falls outside the eligible timeframe for a full refund.'
  - retrieved context ids: `HC-008-markdown-2`, `HC-008-markdown-5`, `HC-008-markdown-13`, `HC-008-markdown-1`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10403: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your recent charge. According to our policy, subscriptions cannot be fully refunded if they were canceled before the 30-day eligibility period has elapsed. In this case, since the charge was made 12 days ago, it falls outside the eligible timeframe for a full refund.
    
    However, there is an exception to this rule: if you have received duplicate charges due to incorrect billing, those duplicates would be refunded in full. Please note that duplicate charges occur when the same invoice is charged multiple times, which is covered by our FAQs.
    
    For your specific situation, please understand that the charge of $49.00 was incurred prior to any cancellation of the subscription. Therefore, based on our current policy
    ```

- **Recorded in normal use** — trace `9e45fd94f5e04638`, request `req-53071a19a4dd`, 2026-10-25T16:03:51+05:30 local (2026-10-25T10:33:51.895+00:00)
  - path `ticket_reply` · source `week6-eval` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-10413 · $19.99 charged 5 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'For your specific case, since the charge was made less than 30 days ago, we cannot provide a refund at this time.'
  - retrieved context ids: `HC-008-markdown-6`, `HC-008-markdown-3`, `HC-008-markdown-2`, `HC-008-markdown-5`, `HC-008-markdown-13`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10413: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your refund request. According to our policy, charges made more than 30 days prior cannot be refunded. However, if there were any duplicate charges due to an error, they would be refunded in full.
    
    For your specific case, since the charge was made less than 30 days ago, we cannot provide a refund at this time. We recommend checking your billing history for any other duplicate charges.
    
    The entire refund process typically takes approximately 5 to 10 business days. Once we receive your request, we will initiate the refund process and notify you via email when it has been processed.
    
    Please note that duplicate charges are handled differently; if you have any questions about those, feel free to
    ```

- **Recorded in normal use** — trace `d96c722bd40244ea`, request `req-5e9017b6c127`, 2026-10-25T16:07:35+05:30 local (2026-10-25T10:37:35.396+00:00)
  - path `ticket_reply` · source `week6-eval` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-10421 · $30.0 charged 29 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'In your case, since the charge was made exactly 30 days ago, it falls outside the eligibility window for a standard refund.'
  - retrieved context ids: `HC-008-markdown-13`, `HC-008-markdown-3`, `HC-008-markdown-5`, `HC-008-markdown-2`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-10421: Customer Inquiry
    
    Dear Customer,
    
    Thank you for reaching out regarding your recent charge. We understand that an error occurred, and we apologize for any inconvenience this may have caused.
    
    According to our policy, charges made more than 30 days prior to the refund request are not eligible for a refund. However, if the same invoice was charged multiple times, each duplicate charge would be refunded in full.
    
    In your case, since the charge was made exactly 30 days ago, it falls outside the eligibility window for a standard refund. However, based on the information provided, there were no duplicates involved in this transaction.
    
    To proceed, please provide us with your billing details so we can verify the correct amount and ensure proper processing. Once verified, we will initiate the
    ```

- **Recorded in normal use** — trace `a17bd88545494477`, request `req-8840fa279498`, 2026-10-25T16:08:07+05:30 local (2026-10-25T10:38:07.479+00:00)
  - path `ticket_reply` · source `week6-eval` · prompt `ticket-v1.2` · model `Qwen/Qwen2.5-1.5B-Instruct` · build `76af145`
  - ticket CD-11901 · $120.0 charged 14 days ago · Standard
  - policy check: **contradicts** — denies a refund the policy grants: 'Since your charge was made 14 days ago, this transaction falls outside the 30-day window.'
  - retrieved context ids: `HC-008-markdown-2`, `HC-008-markdown-3`, `HC-008-markdown-4`, `HC-008-markdown-5`, `HC-008-markdown-6`
  - discoverable through: `ts`, `outcome.answer (full text)`, `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`
  - the incorrect generated answer:

    ```
    CD-11901: Hi there,
    
    Thank you for reaching out about your refund request. Based on our policy, charges made more than 30 days prior to the refund request are not eligible for a refund. Since your charge was made 14 days ago, this transaction falls outside the 30-day window.
    
    However, if you have turned off automatic renewal through Organization Settings > Billing, the next charge should not occur, preventing any further charges. To ensure no additional charges, please confirm that you've disabled automatic renewal.
    
    In case you need assistance turning off automatic renewal, feel free to reach out again. We'll guide you through the process.
    
    Best regards,
    CloudDesk Support Team
    ```


**Real vs controlled.** 14 of the matches above were recorded in normal use (Week 6 drafts,
Week 10 benchmark runs and the Week 10 UI chat) and 2 are controlled test incidents
planted for this drill. The controlled incidents are real requests through the real drafter under the prompt version
active when they were planted; they carry `fixture={...}` on the record and are labelled in every view.

- Controlled incident 1 (`refund-window-inverted`): trace `2fa07536c6a74c3a`, planted 2026-10-25T09:02:50.880+00:00
  under `ticket-v1.2`; policy check **contradicts**.
  Ticket: 'I was charged $120 for the Team plan 14 days ago but we cancelled before the renewal date. Please refund it.'
- Controlled incident 2 (`duplicate-charge-denied`, bonus): trace `4561826fb7084e8d`, planted 2026-10-25T09:06:34.962+00:00
  under `ticket-v1.2`; policy check **contradicts**.

**Reference policy facts (HC-008, `samples/article_08_refunds_escalation.md`, mirrored in `src/ticket_policy.py`):**

- A charge is refundable in full only when the refund is requested within 30 days of the charge date (HC-008, Refund Eligibility Window).
- A charge older than 30 days is not eligible; agents must not promise or process a refund for it. The customer can turn off automatic renewal in Organization Settings > Billing.
- Duplicate charges on the same invoice are refunded in full regardless of the 30-day window (HC-008, Duplicate Charges).
- A refund is always for the exact amount charged and returns to the original payment method within 5 to 10 business days.
- CloudDesk does not offer discounts, credits or partial refunds in place of a refund.

**The defect.** Under prompt `ticket-v1.2` the 1.5B drafter tells charges that are *inside* the 30-day window that
they fall *outside* it (12, 5 and 29 days in the frozen Week 6 replies T03, T13, T21; 14 days in controlled incident 1),
and refuses a duplicate charge because of its age (controlled incident 2). The six Week 6 assertions did not catch it:
they check that an amount is stated and that no refund is *promised* outside the window, never that one is *refused*
inside it. The hand labels (week6/labels_25.json) had marked those three replies FAIL on resolution, so the judge saw
it and the deterministic layer did not.

**The fix (`ticket-v1.3`, src/ticket_reply.py).** Stating the HC-008 rule in the system prompt was probed first and was
*not* enough: the model still wrote "14 days ago, which falls outside the 30-day window". The one comparison it kept
inverting (days since the charge against 30, or the duplicate flag) is therefore made in code by `refund_check_line()`
and appended to the user turn as a single line, e.g. *"Refund check: the charge is 14 days old, which is within the
30-day window, so it IS eligible for a full refund of $120.00."* The system prompt states the rule and tells the model
to follow that line. `ticket_block()` is unchanged, so the labeller and the judge still see the ticket exactly as before.
RED (v1.2) and GREEN (v1.3) full-suite runs, every reply drafted fresh, are under `week11/test_results/`; both use the
seven deterministic assertions (`--judge none`: on this 8 GB machine judge v2 scored 2 of 26 in 35 minutes after the
drafter and was aborted — `test_results/aborted_RED_with_judge_v2.txt`).

## 4. Which log fields made the incident discoverable

- `ts` (indexed) — the time slice.
- `outcome.answer` — full generated text, FTS-indexed: the complaint names the answer's topic, not the question.
- `ticket.charge_amount`, `ticket.days_since_charge`, `ticket.duplicate_charge`, `ticket.refund_requested` — the facts the policy check needs.
- `prompt.version` — to tell which prompt produced it, and to separate before/after the fix.
- `policy_verdict` (new in Week 11, indexed) — procedure 2 reads this directly.

## 5. Missing fields and limitations

- customer / user id: the log has no customer field, so the 'a customer said' slice cannot be used; only time, topic, ticket facts and prompt version were available
- complaint timestamp: 'yesterday afternoon' is the complainant's recollection; the window is derived from as_of, not recorded
- policy_verdict: not an indexed field in the first procedure — every candidate answer had to be read and classified (closed by the indexed procedure)
- request_id and spans: absent on records written before Week 11 (shown as 'not recorded', never back-filled)
- The classifier is the same code as the Week 6 assertion no_denial_inside_window / no_refund_outside_window: a wrong answer phrased in a way neither regex covers would be missed.
- Only tickets with charge facts can be checked against the policy; a plain question about refunds has nothing to compare and is reported as unverifiable.
- Log coverage: 364 requests from 2026-09-07T13:12:00.765+00:00 to 2026-10-25T11:04:47.687+00:00.

## 6. Bonus — the gap closed, and the second drill

Drill 1 showed the wrongness check was a scan: every topical answer had to be read and classified. Week 11 therefore
writes a **`policy_verdict`** column at index time (decided by code from the ticket facts and the answer) with an index
on it, and backfills it for every historical record. Drill 2 ran procedure 2 on the same complaint through the same
panel, with a **second, distinct** controlled incident (a duplicate charge at 40 days, trace `4561826fb7084e8d`)
in the log: it was among drill 2's matches
(procedure 1 also found it).
The two measured times are in §1; the field responsible is `policy_verdict`.

## 7. Canary / rollback (two lines)

1. **Canary:** Route 10% of ticket-reply traffic (or the next 50 live ticket requests, whichever comes first) to ticket-v1.3 while ticket-v1.2 keeps the rest; the monitored signal is the per-request policy_check on the Week 11 index (GET /api/week11/requests?prompt_version=ticket-v1.3&policy_verdict=contradicts) plus the live assertion no_denial_inside_window. Threshold: 0 contradictions on refund tickets with charge facts, and the seven-assertion pass rate on the canary slice at or above ticket-v1.2's rate over the same window (baseline failures: escalation tag, cut-off replies).
2. **Rollback:** If any ticket-v1.3 request in the canary slice records policy_verdict = contradicts, or the canary pass rate drops below the ticket-v1.2 baseline, set TICKET_SYSTEM_PROMPT back to the ticket-v1.2 text and TICKET_PROMPT_VERSION to 'ticket-v1.2' (one constant each in src/ticket_reply.py), restart the server, and confirm the next traces carry prompt_version ticket-v1.2 in the explorer. Every trace records its prompt version and sha, so the canary and the rollback are both verifiable from the log.

ticket-v1.3 has been tested locally (RED/GREEN suite runs and live requests on this machine). No production deployment has occurred; this plan is what a deployment would follow.

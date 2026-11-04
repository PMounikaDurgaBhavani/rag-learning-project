---
article_id: MACRO-002
slug: macro-refund-outside-window
title: "Macro: Refund not eligible (outside window)"
product_area: Macros
last_updated: 2026-09-16
status: current
doc_type: zendesk_macro
corpus_note: Week 12 capstone seed document (demo corpus). Agent macro text, not customer-facing policy.
---

# Macro: Refund not eligible (outside window)

Use when the entitlement record confirms the charge is outside the customer's refund window and it is not a duplicate charge.

Macro text:

{{ticket.id}}: Thanks for contacting CloudDesk support. I have checked the charge of {{charge.amount}} on {{charge.date}} against your account's refund policy. Because the request was made after the refund window for your account closed, the charge is not eligible for a refund. To make sure the next charge does not occur, you can turn off automatic renewal in Organization Settings > Billing. If you believe the charge itself was made in error, reply to this ticket and a billing specialist will review it.

Do not offer a credit, discount or partial refund.

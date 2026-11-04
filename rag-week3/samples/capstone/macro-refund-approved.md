---
article_id: MACRO-001
slug: macro-refund-approved
title: "Macro: Refund approved (inside window)"
product_area: Macros
last_updated: 2026-09-16
status: current
doc_type: zendesk_macro
corpus_note: Week 12 capstone seed document (demo corpus). Agent macro text, not customer-facing policy.
---

# Macro: Refund approved (inside window)

Use when the entitlement record confirms the charge is inside the customer's refund window, or when the charge is a duplicate.

Macro text:

{{ticket.id}}: Thanks for contacting CloudDesk support. I have checked the charge of {{charge.amount}} on {{charge.date}} against your account's refund policy and it is eligible for a full refund. The refund of {{charge.amount}} will be requested from Billing today and reaches your original payment method within the timeframe stated in the refund policy. You do not need to do anything else.

Required before sending: order looked up, entitlement checked, policy version recorded on the ticket.

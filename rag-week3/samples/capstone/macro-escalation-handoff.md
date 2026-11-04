---
article_id: MACRO-003
slug: macro-escalation-handoff
title: "Macro: Escalation hand-off to a human agent"
product_area: Macros
last_updated: 2026-09-16
status: current
doc_type: zendesk_macro
corpus_note: Week 12 capstone seed document (demo corpus). Agent macro text, not customer-facing policy.
---

# Macro: Escalation hand-off to a human agent

Use when the assistant cannot verify the order or entitlement, when evidence conflicts, or when the request is legal, security-sensitive or outside the help centre's scope.

Macro text:

{{ticket.id}}: Thanks for contacting CloudDesk support. I have passed your ticket to a specialist who will review it and reply directly. Your escalation reference is {{escalation.reference}}. You do not need to resend any information.

Priority tier: add the tag [ESCALATED-PRIORITY] on the first line.

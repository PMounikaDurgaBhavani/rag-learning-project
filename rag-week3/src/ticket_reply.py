"""Draft a reply to a customer support ticket from the help-centre articles.

Week 6 grades ticket replies, so the app needs a path that produces one.
It reuses the answer path wherever that path carries a measured decision —
the same retrievers, distance gate and threshold, and decoding settings —
and differs only where a ticket is not a question:

    model     Qwen2.5-1.5B-Instruct on its own pipeline. The app's 0.5B
              could not draft a reply under any of the prompt versions
              below (it echoed the ticket fields back), so drafts use the
              larger model while `ask` and every earlier number stay on 0.5B
    input     a ticket (ID, support tier, charge on the account, message)
    prompt    asks for a reply that quotes the ticket ID, tags Priority
              tickets and applies the refund policy from the sources
    sentinel  none: with a ticket in the prompt the 0.5B model took the
              NOT_IN_SOURCES exit on nearly every ticket (see the version
              notes below), so refusal is left to the distance gate
    refusal   a routed hand-off (ticket_policy.routed_reply) instead of the
              bare refusal sentence
    grounding gate 3 is not applied: a reply is largely greeting and routing
              text that no chunk contains, so a coverage gate would refuse
              nearly every reply

Every draft is traced like an answer (JSONL + Langfuse) with the ticket
attached, so a failed reply can be replayed and turned into a regression
case.
"""

import os
import time

import observability as obs
import tracing
from generator import (
    CITATION_MARKER,
    GENERATION_PARAMS,
    RELEVANCE_THRESHOLD,
    TOP_K,
    build_context,
    chunk_distance,
    extract_text,
    lexical_anchors,
    retrieve_for_mode,
    said_no_answer,
)
from ticket_assertions import run_assertions
from ticket_policy import ESCALATION_TAG, refund_eligible, routed_reply, ticket_block
from vector_store import DEFAULT_STRATEGY

# The UI's default: a ticket ID, an amount or an error code in the message
# can vouch for a chunk through BM25 even when dense distance is poor.
DEFAULT_RETRIEVAL_MODE = "hybrid_rerank"

TICKET_SYSTEM_PROMPT = (
    "You are a CloudDesk support agent. Write the reply that will be sent "
    "to the customer.\n"
    "Use the numbered sources for facts, steps and policy, and cite the "
    "source number in square brackets, e.g. [1].\n"
    "Begin the reply with the ticket ID, e.g. \"CD-10000: ...\".\n"
    f"If the support tier is Priority, put the tag {ESCALATION_TAG} "
    "on the first line.\n"
    "If the customer wants a refund, apply the refund policy in the sources "
    "and state the charge amount as a number.\n"
    "Refund rule from HC-008: a charge is eligible for a full refund of the "
    "exact amount if it was made 30 days ago or less, or if it is a duplicate "
    "charge, whatever its age. Only a charge older than 30 days that is not a "
    "duplicate is ineligible. The ticket ends with a 'Refund check' line that "
    "states which side of the window this charge is on: follow it.\n"
    "Never invent numbers, prices, limits or policies. If the sources do "
    "not cover something the customer asked, say so plainly.\n"
    "Keep the reply under 80 words."
)

# Bump whenever TICKET_SYSTEM_PROMPT or the user turn changes.
#   v1.0  ended on the ticket fields: the 0.5B model echoed them back or
#         emitted the sentinel on 20 of 25 tickets whose answer was retrieved
#   v1.1  instruction last, "do not repeat the fields", narrower sentinel:
#         sentinel on 5 of 5 trial tickets — any escape hatch wins
#   v1.2  no sentinel. Refusal is left to the distance gate, so the model
#         always drafts and its failures show up in the reply text itself
#   v1.3  Week 11. The support drill found v1.2 telling in-window charges
#         (5, 12, 14, 29 days) that they fell OUTSIDE the 30-day window, and
#         refusing a 40-day duplicate. Stating the rule in the system prompt
#         was not enough (the 1.5B model still wrote "14 days ... falls
#         outside"), so the comparison with 30 is made in code and handed to
#         the model as a 'Refund check' line in the user turn — the same
#         decision the Week 10 ticket specialist already makes in code.
#         ticket_block() is unchanged: the labeller and the judge still see
#         the ticket exactly as before.
TICKET_PROMPT_VERSION = "ticket-v1.3"

TICKET_MODEL_NAME = os.environ.get("RAG_TICKET_LLM", "Qwen/Qwen2.5-1.5B-Instruct")

_PIPELINE = None


def get_ticket_pipeline():
    global _PIPELINE

    if _PIPELINE is None:
        import torch
        from transformers import pipeline

        # Week 12: if the agent runtime already loaded the same weights,
        # wrap them instead of loading a second 3 GB copy.
        try:
            import agent_runtime
            if agent_runtime._MODEL is not None and agent_runtime.MODEL_NAME == TICKET_MODEL_NAME:
                _PIPELINE = pipeline("text-generation", model=agent_runtime._MODEL,
                                     tokenizer=agent_runtime._TOKENIZER)
                return _PIPELINE
        except Exception:
            pass

        # 1.5B in float32 on CPU needs ~6 GB; half precision on MPS fits
        # beside the embedding model and reranker on an 8 GB machine.
        device = os.environ.get("RAG_TICKET_DEVICE") or (
            "mps" if torch.backends.mps.is_available() else "cpu"
        )
        dtype = torch.float16 if device == "mps" else torch.float32
        _PIPELINE = pipeline(
            "text-generation", model=TICKET_MODEL_NAME, device=device, dtype=dtype
        )

    return _PIPELINE


def release_ticket_pipeline():
    global _PIPELINE
    _PIPELINE = None


def refund_check_line(ticket):
    """ticket-v1.3: the one comparison the model kept getting wrong, made in code.

    HC-008's window test is arithmetic (days since the charge against 30, or
    a duplicate flag), and the drafter inverted it for in-window charges under
    v1.2 even with the rule spelled out. The line is appended to the user turn
    only; ticket_block() stays as the labeller and the judge saw it.
    """
    if not ticket.get("refund_requested") or ticket.get("charge_amount") is None:
        return ""
    days = ticket.get("days_since_charge")
    amount = f"${ticket['charge_amount']:,.2f}"
    if ticket.get("duplicate_charge"):
        return ("\nRefund check: this is a duplicate charge, which is refunded in full "
                f"whatever its age, so this charge IS eligible for a full refund of {amount}.")
    if days is None:
        return ""
    if refund_eligible(ticket):
        return (f"\nRefund check: the charge is {days} days old, which is within the "
                f"30-day window, so it IS eligible for a full refund of {amount}.")
    return (f"\nRefund check: the charge is {days} days old, which is more than the "
            f"30-day window, so it is NOT eligible for a refund.")


def _draft(ticket, retrieval_mode, top_k, strategy, threshold):
    query = ticket["message"]

    with obs.span("retrieval", mode=retrieval_mode, strategy=strategy, top_k=top_k) as retrieval_span:
        chunks = retrieve_for_mode(
            query, top_k=top_k, strategy=strategy, where=None, mode=retrieval_mode
        )
        retrieval_span.set(n_chunks=len(chunks),
                           context_ids=[chunk["chunk_id"] for chunk in chunks])

    result = {
        "query": query,
        "strategy": strategy,
        "retrieval_mode": retrieval_mode,
        "top_k": top_k,
        "threshold": threshold,
        "min_coverage": None,
        "prompt_version": TICKET_PROMPT_VERSION,
        "prompt_sha": tracing.sha256(TICKET_SYSTEM_PROMPT),
        "prompt_messages": None,
        "model_name": TICKET_MODEL_NAME,
        "model_params": dict(GENERATION_PARAMS),
        "filter": None,
        "retrieved": [
            {
                "rank": chunk["rank"],
                "chunk_id": chunk["chunk_id"],
                "article_id": chunk.get("article_id"),
                "section": chunk.get("section"),
                "distance": (
                    round(distance, 4)
                    if (distance := chunk_distance(chunk)) is not None
                    else None
                ),
                "bm25_score": chunk.get("bm25_score"),
                "rrf_score": chunk.get("rrf_score"),
                "rerank_score": chunk.get("rerank_score"),
                "dense_rank": chunk.get("dense_rank"),
                "bm25_rank": chunk.get("bm25_rank"),
            }
            for chunk in chunks
        ],
        "answer": None,
        "raw_answer": None,
        "output_tokens": None,
        "sources": [],
        "refused": False,
        "refusal_reason": None,
        "model_cited": [],
        "best_distance": None,
    }

    def refuse(reason):
        result["answer"] = routed_reply(ticket)
        result["refused"] = True
        result["refusal_reason"] = reason
        return result

    with obs.span("validation.distance_gate", threshold=threshold, mode=retrieval_mode) as gate:
        if not chunks:
            gate.set(outcome="refused: no_chunks_retrieved")
            return refuse("no_chunks_retrieved")

        # Same distance gate as generator._generate_answer, including the
        # lexical-anchor bypass for non-dense modes.
        distances = [
            distance
            for distance in (chunk_distance(chunk) for chunk in chunks)
            if distance is not None
        ]
        best_distance = min(distances) if distances else None
        result["best_distance"] = (
            round(best_distance, 4) if best_distance is not None else None
        )
        gate.set(best_distance=result["best_distance"])

        anchors = (
            lexical_anchors(query, chunks, strategy)
            if retrieval_mode != "dense"
            else []
        )
        result["lexical_anchors"] = anchors

        if best_distance is not None and best_distance > threshold:
            if not anchors:
                gate.set(outcome=f"refused: distance_gate ({best_distance:.4f} > {threshold})")
                return refuse(f"distance_gate ({best_distance:.4f} > {threshold})")
            result["gate_bypass"] = f"lexical_anchor ({', '.join(anchors)})"
        gate.set(outcome="passed" + (f" ({result['gate_bypass']})" if result.get("gate_bypass") else ""),
                 lexical_anchors=anchors or None)

    messages = [
        {"role": "system", "content": TICKET_SYSTEM_PROMPT},
        {
            "role": "user",
            # The instruction goes last: ending on the ticket fields made
            # the small model continue the pattern and echo them back.
            "content": (
                f"Sources:\n{build_context(chunks)}\n\n"
                f"Ticket (do not repeat these fields in the reply):\n"
                f"{ticket_block(ticket)}"
                f"{refund_check_line(ticket)}\n\n"
                f"Write the reply to the customer now, starting with "
                f"{ticket['ticket_id']}."
            ),
        },
    ]
    result["prompt_messages"] = messages

    with obs.span("generation", model=TICKET_MODEL_NAME, provider=obs.PROVIDER,
                  prompt_version=TICKET_PROMPT_VERSION, prompt_sha=result["prompt_sha"],
                  model_loaded_during_span=_PIPELINE is None,
                  max_new_tokens=GENERATION_PARAMS["max_new_tokens"]) as gen_span:
        generate = get_ticket_pipeline()
        # Prompt tokens, counted on the exact rendered chat template with the
        # drafter's own tokenizer: Week 10 prices both arms per model call, and
        # the ticket block alone is the base context the multiplier divides by.
        try:
            rendered = generate.tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, tokenize=False
            )
            result["input_tokens"] = len(
                generate.tokenizer(rendered, add_special_tokens=False)["input_ids"]
            )
            result["base_context_tokens"] = len(
                generate.tokenizer(ticket_block(ticket), add_special_tokens=False)["input_ids"]
            )
        except Exception:                 # counting must never block drafting
            result["input_tokens"] = None
            result["base_context_tokens"] = None
        result["max_new_tokens"] = GENERATION_PARAMS["max_new_tokens"]
        model_started = time.perf_counter()
        raw_answer = extract_text(generate(messages, **GENERATION_PARAMS))
        result["model_latency_ms"] = round((time.perf_counter() - model_started) * 1000.0, 1)
        result["raw_answer"] = raw_answer
        # Counted with the drafter's own tokenizer; generator.count_tokens would
        # load the 0.5B model just to count.
        result["output_tokens"] = len(
            generate.tokenizer(raw_answer, add_special_tokens=False)["input_ids"]
        )
        gen_span.set(input_tokens=result["input_tokens"], output_tokens=result["output_tokens"],
                     model_latency_ms=result["model_latency_ms"])
    result["model_cited"] = [
        int(marker) for marker in CITATION_MARKER.findall(raw_answer)
    ]

    if said_no_answer(raw_answer):
        return refuse("model_reported_not_in_sources")

    # The draft is sent as written: stripping citation markers or adding a
    # ticket ID here would hide exactly what the assertions exist to catch.
    result["answer"] = raw_answer
    return result


def draft_ticket_reply(
    ticket,
    retrieval_mode=DEFAULT_RETRIEVAL_MODE,
    top_k=TOP_K,
    strategy=DEFAULT_STRATEGY,
    threshold=RELEVANCE_THRESHOLD,
    source="ticket",
    session_id=None,
    tags=None,
):
    """Draft a reply to one ticket and trace how it was drafted."""

    with obs.request("ticket_reply", source=source, input_text=ticket.get("message")):
        started = time.perf_counter()
        result = _draft(ticket, retrieval_mode, top_k, strategy, threshold)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)

        # Week 11: the Week 6 assertions run on every live draft, so each
        # request carries its own evaluation result (the judge needs the
        # gated protocol run and is not part of this).
        result["evaluation"] = evaluate_draft(ticket, result)

        try:
            record = tracing.build_trace(
                result, latency_ms={"total": elapsed_ms}, source=source
            )
            record["kind"] = "ticket_reply"
            record["ticket"] = ticket
            tracing.append(record, session_id=session_id, tags=tags)
            result["trace_id"] = record["trace_id"]
            result["langfuse_trace_id"] = record.get("langfuse_trace_id")
            result["observability"] = {
                "request_id": record.get("request_id"),
                "spans": record.get("spans"),
                "usage": record.get("usage"),
                "cost": record.get("cost"),
                "status": record.get("status"),
                "provider": record.get("provider"),
                "policy_check": record.get("policy_check"),
                "evaluation": record.get("evaluation"),
            }
        except Exception as error:            # drafting must survive tracing
            print(f"  [tracing] trace not recorded: {error}")

    result.pop("prompt_messages", None)
    result["ticket"] = ticket
    return result


def evaluate_draft(ticket, result):
    """Run the deterministic Week 6 assertions on one live draft. Never raises."""
    try:
        with obs.span("validation.assertions", suite="week6 assertions") as span:
            reply = {
                "text": result.get("answer") or "",
                "refused": result.get("refused"),
                "output_tokens": result.get("output_tokens"),
                "max_new_tokens": result.get("max_new_tokens") or GENERATION_PARAMS["max_new_tokens"],
            }
            rows = run_assertions({"id": ticket.get("ticket_id"), "ticket": ticket}, reply)
            applicable = [row for row in rows if row["applicable"]]
            failed = [row["name"] for row in applicable if not row["passed"]]
            evaluation = {
                "suite": "week6 assertions (src/ticket_assertions.py)",
                "n_assertions": len(rows),
                "applicable": len(applicable),
                "passed": len(applicable) - len(failed),
                "failed": len(failed),
                "failed_names": failed,
                "verdict": "fail" if failed else "pass",
                "results": rows,
                "judge": "not run on live requests (the Week 6 judge runs only under the gated protocol)",
            }
            span.set(verdict=evaluation["verdict"], failed=failed or None, applicable=len(applicable))
        obs.set_evaluation(evaluation)
        return evaluation
    except Exception as error:
        return {"suite": "week6 assertions", "verdict": "error", "error": str(error)}

"""Draft a reply to a customer support ticket from the help-centre articles.

Week 6 grades ticket replies, so the app needs a path that produces one.
It reuses the answer path wherever that path carries a measured decision —
the same retrievers, distance gate and threshold, model, decoding settings
and sentinel — and differs only where a ticket is not a question:

    input     a ticket (ID, support tier, charge on the account, message)
    prompt    asks for a reply that quotes the ticket ID, tags Priority
              tickets and applies the refund policy from the sources
    refusal   a routed hand-off (ticket_policy.routed_reply) instead of the
              bare refusal sentence
    grounding gate 3 is not applied: a reply is largely greeting and routing
              text that no chunk contains, so a coverage gate would refuse
              nearly every reply

Every draft is traced like an answer (JSONL + Langfuse) with the ticket
attached, so a failed reply can be replayed and turned into a regression
case.
"""

import time

import tracing
from generator import (
    CITATION_MARKER,
    GENERATION_PARAMS,
    MODEL_NAME,
    NO_ANSWER_SENTINEL,
    RELEVANCE_THRESHOLD,
    TOP_K,
    build_context,
    chunk_distance,
    count_tokens,
    extract_text,
    get_pipeline,
    lexical_anchors,
    retrieve_for_mode,
    said_no_answer,
)
from ticket_policy import ESCALATION_TAG, routed_reply, ticket_block
from vector_store import DEFAULT_STRATEGY

# The UI's default: a ticket ID, an amount or an error code in the message
# can vouch for a chunk through BM25 even when dense distance is poor.
DEFAULT_RETRIEVAL_MODE = "hybrid_rerank"

TICKET_SYSTEM_PROMPT = (
    "You are a CloudDesk support agent writing a reply to a customer ticket.\n"
    "Use only the numbered sources for facts, steps and policy. "
    "Cite the source number in square brackets, e.g. [1].\n"
    "Begin the reply with the ticket ID, e.g. \"CD-10000: ...\".\n"
    f"If the support tier is Priority, put the tag {ESCALATION_TAG} "
    "on the first line.\n"
    "If the customer wants a refund, apply the refund policy in the sources "
    "and state the charge amount as a number.\n"
    "If the sources do not contain what the customer needs, "
    f"reply with exactly: {NO_ANSWER_SENTINEL}\n"
    "Never invent numbers, prices, limits or policies.\n"
    "Answer in under 80 words."
)

# Bump whenever TICKET_SYSTEM_PROMPT changes.
TICKET_PROMPT_VERSION = "ticket-v1.0"


def _draft(ticket, retrieval_mode, top_k, strategy, threshold):
    query = ticket["message"]

    chunks = retrieve_for_mode(
        query, top_k=top_k, strategy=strategy, where=None, mode=retrieval_mode
    )

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
        "model_name": MODEL_NAME,
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

    if not chunks:
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

    anchors = (
        lexical_anchors(query, chunks, strategy)
        if retrieval_mode != "dense"
        else []
    )
    result["lexical_anchors"] = anchors

    if best_distance is not None and best_distance > threshold:
        if not anchors:
            return refuse(f"distance_gate ({best_distance:.4f} > {threshold})")
        result["gate_bypass"] = f"lexical_anchor ({', '.join(anchors)})"

    messages = [
        {"role": "system", "content": TICKET_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Sources:\n{build_context(chunks)}\n\n{ticket_block(ticket)}"
            ),
        },
    ]
    result["prompt_messages"] = messages

    raw_answer = extract_text(get_pipeline()(messages, **GENERATION_PARAMS))
    result["raw_answer"] = raw_answer
    result["output_tokens"] = count_tokens(raw_answer)
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

    started = time.perf_counter()
    result = _draft(ticket, retrieval_mode, top_k, strategy, threshold)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)

    try:
        record = tracing.build_trace(
            result, latency_ms={"total": elapsed_ms}, source=source
        )
        record["kind"] = "ticket_reply"
        record["ticket"] = ticket
        tracing.append(record, session_id=session_id, tags=tags)
        result["trace_id"] = record["trace_id"]
        result["langfuse_trace_id"] = record.get("langfuse_trace_id")
    except Exception as error:            # drafting must survive tracing
        print(f"  [tracing] trace not recorded: {error}")

    result.pop("prompt_messages", None)
    result["ticket"] = ticket
    return result

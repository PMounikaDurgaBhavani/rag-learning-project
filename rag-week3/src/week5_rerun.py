"""Re-ask the 20 Week 5 sampled questions against the current build.

Replay (experiments/error_analysis.py replay) answers "does the recorded
prompt still produce the recorded output?" — it never touches retrieval.
A rerun answers the other question: "what does the system do with these 20
messages *today*?" Each question goes back through the live pipeline with
the retrieval mode, top_k, strategy and threshold its original trace
recorded, so a changed outcome is a change in the build, not in the request.

Rerun traces are tagged (source "week5-rerun", one Langfuse session per
rerun) and excluded from `error_analysis.py sample`, so re-asking the sample
never changes the population a future sample is drawn from.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import tracing

ROOT = Path(__file__).resolve().parent.parent
WEEK5_DIR = ROOT / "week5"
RERUN_DIR = WEEK5_DIR / "reruns"

RERUN_SOURCE = "week5-rerun"


def _outcome(refused, refusal_reason, answer):
    return {
        "refused": bool(refused),
        "refusal_reason": refusal_reason,
        "refusal_family": (refusal_reason or "").split(" (")[0] or None,
        "answer": answer,
    }


def rerun_sample(retrieval_mode=None, progress=print):
    """Re-ask every sampled question. retrieval_mode=None keeps each original."""

    from generator import RELEVANCE_THRESHOLD, TOP_K, generate_answer
    from vector_store import DEFAULT_STRATEGY

    sample = json.loads((WEEK5_DIR / "sample.json").read_text(encoding="utf-8"))
    by_id = {trace["trace_id"]: trace for trace in tracing.load_traces()}

    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    session_id = f"week5-rerun-{stamp}"

    rows = []
    for index, trace_id in enumerate(sample["trace_ids"], start=1):
        original = by_id.get(trace_id)
        if original is None:
            rows.append({"index": index, "original_trace_id": trace_id,
                         "error": "trace not found in traces/traces.jsonl"})
            continue

        request = original["request"]
        mode = retrieval_mode or request.get("retrieval_mode") or "dense"
        result = generate_answer(
            original["query"],
            top_k=request.get("top_k") or TOP_K,
            strategy=request.get("strategy") or DEFAULT_STRATEGY,
            threshold=request.get("threshold") or RELEVANCE_THRESHOLD,
            retrieval_mode=mode,
            source=RERUN_SOURCE,
            session_id=session_id,
            tags=["week5-rerun", f"original:{trace_id}"],
        )

        before = original["outcome"]
        then = _outcome(before["refused"], before["refusal_reason"], before["answer"])
        now = _outcome(result["refused"], result["refusal_reason"], result["answer"])
        now["trace_id"] = result.get("trace_id")

        changed = (
            then["refused"] != now["refused"]
            or then["refusal_family"] != now["refusal_family"]
            or (then["answer"] or "").strip() != (now["answer"] or "").strip()
        )

        rows.append({
            "index": index,
            "original_trace_id": trace_id,
            "query": original["query"],
            "retrieval_mode": mode,
            "original_app_version": original.get("app_version"),
            "original": then,
            "rerun": now,
            "changed": changed,
        })

        if progress:
            state = "answered" if not now["refused"] else f"refused:{now['refusal_family']}"
            progress(f"  {index:>2}. {trace_id}  {'CHANGED ' if changed else 'same    '} "
                     f"{state:<40} {original['query'][:50]}")

    tracing.flush()

    report = {
        "sample_seed": sample.get("seed"),
        "app_version": tracing.app_version(),
        "session_id": session_id,
        "langfuse": tracing.langfuse_client() is not None,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "retrieval_mode_override": retrieval_mode,
        "changed": sum(1 for row in rows if row.get("changed")),
        "rows": rows,
    }

    RERUN_DIR.mkdir(parents=True, exist_ok=True)
    path = RERUN_DIR / f"rerun_{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["saved_to"] = str(path.relative_to(ROOT))
    return report

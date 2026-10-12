"""Live checks of the main RAG pipeline: real retrieval, real generation,
real citations and refusals, traced to a TEMPORARY directory.

    python tests/test_rag_live.py

Needs Postgres (the index) and downloads/loads the 0.5B generator, the
embedding model and the cross-encoder, so it takes about a minute. It never
writes to traces/traces.jsonl: RAG_TRACE_DIR points at a temp folder before
tracing is imported, and Langfuse is off.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="rag_live_"))
os.environ["RAG_TRACE_DIR"] = str(TMP)
os.environ["RAG_LANGFUSE"] = "0"
os.environ.pop("RAG_TRACE_DB", None)
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{'' if condition else '  ' + str(detail)}")
    if not condition:
        failures.append(name)


import db  # noqa: E402

if not db.server_reachable(db.DATABASE_URL):
    print(f"Postgres not reachable at {db.safe_url(db.DATABASE_URL)} — nothing to test.")
    sys.exit(2)

import tracing  # noqa: E402
from generator import generate_answer, RELEVANCE_THRESHOLD  # noqa: E402

check("traces go to the temp dir, not traces/", Path(tracing.TRACE_FILE).resolve().parent == TMP.resolve(),
      tracing.TRACE_FILE)
check("the production threshold is unchanged", RELEVANCE_THRESHOLD == 0.75, RELEVANCE_THRESHOLD)

print("\nAnswerable question")
r = generate_answer("How long is a CloudDesk password reset link valid?", top_k=5, retrieval_mode="hybrid_rerank",
                    source="test")
retrieved = {c["chunk_id"] for c in r.get("retrieved", [])}
cited = [s["chunk_id"] for s in r.get("sources", [])]
check("answered, not refused", not r["refused"], r.get("refusal_reason"))
check("the answer states the fact", "30 minutes" in (r.get("answer") or ""), r.get("answer"))
check("at least one verified citation", len(cited) >= 1)
check("every citation is a chunk that was actually retrieved", set(cited) <= retrieved, (cited, sorted(retrieved)))
check("every citation comes from the right article", all(c.startswith("HC-001") for c in cited), cited)
check("citation markers in the answer match the sources", all(f"[{i + 1}]" in r["answer"] for i in range(len(cited))))
check("a trace id is returned", bool(r.get("trace_id")))

print("\nUnsupported question (topic in the corpus, fact is not)")
u = generate_answer("What is the maximum file size limit for CloudDesk uploads in megabytes?", top_k=5,
                    retrieval_mode="hybrid_rerank", source="test")
check("refused", u["refused"], u.get("answer"))
check("refused by a gate, with the reason recorded", u.get("refusal_reason") in
      ("model_reported_not_in_sources", "ungrounded_answer") or str(u.get("refusal_reason", "")).startswith("distance_gate"),
      u.get("refusal_reason"))
check("no sources cited on a refusal", not u.get("sources"))

print("\nOut-of-scope question")
o = generate_answer("What is the capital of France?", top_k=5, retrieval_mode="hybrid_rerank", source="test")
check("refused by the distance gate", o["refused"] and str(o.get("refusal_reason", "")).startswith("distance_gate"),
      o.get("refusal_reason"))

print("\nIdentifier + filter")
billing_articles = {d["metadata"]["article_id"] for d in db.load_documents()
                    if d["metadata"].get("product_area") == "Billing"}
f = generate_answer("How do I fix it?", top_k=3, retrieval_mode="hybrid_rerank", product_area="Billing", source="test")
check("the product_area filter is enforced at query time (only Billing articles retrieved)",
      f.get("retrieved") and all(c.get("article_id") in billing_articles for c in f["retrieved"]),
      ([c.get("article_id") for c in f.get("retrieved", [])], sorted(billing_articles)))
g = generate_answer("What is the troubleshooting solution when the reset link has expired?", top_k=5,
                    retrieval_mode="hybrid_rerank", article_id="HC-001", source="test")
check("the article_id filter is enforced at query time",
      all(c.get("article_id") == "HC-001" for c in g.get("retrieved", [])))

print("\nTrace record")
traces = tracing.load_traces()
by_id = {t["trace_id"]: t for t in traces}
t = by_id.get(r["trace_id"])
check("the answerable question's trace was written", t is not None)
if t:
    check("trace carries prompt version + sha, model, retrieval list and outcome",
          all(k in t for k in ("prompt", "model", "retrieval", "outcome")) and t["prompt"].get("sha")
          and t["model"].get("name"))
    check("trace retrieval matches the chunks the answer cited",
          set(cited) <= {c["chunk_id"] for c in t["retrieval"]})
    check("trace outcome agrees with the result", t["outcome"]["refused"] is False and t["outcome"]["answer"] == r["answer"])
    check("trace records the build", bool(t.get("app_version")))
check("refusals are traced too, with their reason", by_id.get(u["trace_id"], {}).get("outcome", {}).get("refused") is True)
check("nothing was appended to traces/traces.jsonl by this run",
      not (ROOT / "traces" / "traces.jsonl").exists()
      or all(line and json.loads(line)["trace_id"] not in (r["trace_id"], u["trace_id"], o["trace_id"])
             for line in (ROOT / "traces" / "traces.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()))

print()
if failures:
    print(f"{len(failures)} check(s) failed:")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print(f"All checks passed. Temp traces in {TMP}")

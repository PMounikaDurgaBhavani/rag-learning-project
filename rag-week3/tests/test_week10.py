"""Week 10 regression checks that need no model and no database.

    python tests/test_week10.py

What is covered:

  cases      the ten-case manifest is deterministic, two per Week 5 mode,
             excludes the judge's few-shot examples, and both arms read the
             same ids from it
  manager    routing (rule, triage, fallback), the task lifecycle in order,
             dispatch and return hand-offs with parent/child ids, grounded
             synthesis that cites the numbered sources, bounded conflict
             re-synthesis, and the "not found" path that calls no synthesis
  specialists the ticket specialist's policy tool (ticket_policy), the
             knowledge specialist's gate and retrieval, both with fakes for
             the model and the retriever
  failure    a real HTTP worker in a thread: an injected 500 for one ticket
             only, bounded retries, FAILED/TIMED_OUT task states, the
             degraded reply is the routed hand-off, no exception text leaks
  metrics    nearest-rank percentiles and the aggregate arithmetic
  api        the JSON routes over a real socket, bad input codes, the
             one-run-at-a-time guard, the 503 degraded contract, and the UI
             markup for the new view

Synthetic data only: nothing here writes to week10/ or traces/.
"""

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))
os.chdir(ROOT)
os.environ.setdefault("RAG_LANGFUSE", "0")
os.environ.setdefault("RAG_TRACE", "0")

failures = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{'' if condition else '  ' + str(detail)}")
    if not condition:
        failures.append(name)


def skip(name, why):
    print(f"  SKIP  {name}  ({why})")


# ===========================================================================
print("Cases — the ten-case manifest")

import week10_benchmark as wb

cases = wb.load_week6_cases()
excluded = wb.few_shot_ids()
chosen = wb.select_cases(cases, excluded)
ids = [c["id"] for c in chosen]
check("ten cases, two per Week 5 mode", len(ids) == 10 and
      all(sum(1 for c in chosen if c["mode"] == m) == 2 for m in wb.MODES), ids)
check("selection is deterministic", [c["id"] for c in wb.select_cases(cases, excluded)] == ids)
check("the judge's few-shot examples are excluded", not set(excluded) & set(ids), (excluded, ids))
check("every chosen case keeps its original reference facts",
      all(c["reference"] == next(o for o in cases if o["id"] == c["id"])["reference"] for c in chosen))
if wb.MANIFEST.exists():
    manifest = wb.read_json(wb.MANIFEST)
    check("the committed manifest equals the rule applied today", manifest["case_ids"] == ids,
          (manifest["case_ids"], ids))
    check("the manifest records the source file sha and the rule",
          manifest["source_sha"] == wb.tracing.sha256(wb.CASES_FILE.read_text(encoding="utf-8"))
          and manifest["selection_rule"] == wb.SELECTION_RULE)
else:
    skip("committed manifest", "week10/cases_10.json not written yet")
for arm in ("single", "multi"):
    path = wb.RESULTS[arm]
    if path.exists():
        data = wb.read_json(path)
        check(f"{arm} results ran exactly the manifest's cases in order", data["case_ids"] == ids)
        check(f"{arm} per-case rows carry what the aggregates need",
              all(k in r for r in data["rows"] for k in ("latency_ms", "input_tokens", "output_tokens",
                                                          "cost_usd", "model_calls", "handoffs", "pass", "judge")))
    else:
        skip(f"{arm} results", f"{path.name} not written yet")

# ===========================================================================
print("\nManager — routing, lifecycle, hand-offs, synthesis (fake model, fake retriever)")

import multi_agent as ma
from ticket_policy import ESCALATION_TAG


class FakeLLM:
    name = "fake-llm"

    def __init__(self, triage='{"ticket_specialist": false, "knowledge_specialist": true, "reason": "kb"}',
                 knowledge="A reset link is valid for 30 minutes [1]",
                 ticket="Duplicate charge, refund in full [1]",
                 synthesis="CD-1: The link is valid for 30 minutes [1]."):
        self.responses = {"manager-triage": triage, "knowledge": knowledge, "ticket": ticket,
                          "synthesis": synthesis}
        self.calls = []

    def route(self, messages):
        system = messages[0]["content"]
        if system.startswith("You are the CloudDesk support manager"):
            return "manager-triage"
        if system.startswith("You are the CloudDesk knowledge specialist"):
            return "knowledge"
        if system.startswith("You are the CloudDesk ticket specialist"):
            return "ticket"
        return "synthesis"

    def __call__(self, messages, max_new_tokens=None):
        kind = self.route(messages)
        self.calls.append({"kind": kind, "messages": messages, "max_new_tokens": max_new_tokens})
        text = self.responses[kind]
        if callable(text):
            text = text(messages, len([c for c in self.calls if c["kind"] == kind]))
        return {"text": text, "input_tokens": 100, "output_tokens": 10, "latency_ms": 0.5,
                "max_new_tokens": max_new_tokens or 160}


def chunks_for(article, n, distance=0.5):
    return [{"chunk_id": f"{article}-markdown-{i}", "article_id": article, "source_file": f"{article}.md",
             "last_updated": "2026-01-01", "section": "Section", "distance": distance, "rank": i,
             "content": "A password reset link is valid for 30 minutes."} for i in range(1, n + 1)]


def fake_retriever(distance=0.5):
    calls = []

    def retrieve(query, top_k=None, where=None):
        calls.append({"query": query, "top_k": top_k, "where": where})
        article = (where or {}).get("article_id", "HC-001")
        return chunks_for(article, top_k or 5, distance)
    retrieve.calls = calls
    return retrieve


KB_TICKET = {"ticket_id": "CD-1", "tier": "Standard", "message": "How long is the reset link valid?",
             "charge_amount": None, "days_since_charge": None, "duplicate_charge": False,
             "refund_requested": False}
REFUND_TICKET = {"ticket_id": "CD-2", "tier": "Priority", "message": "charged twice $89, refund?",
                 "charge_amount": 89.0, "days_since_charge": 3, "duplicate_charge": True,
                 "refund_requested": True}
LATE_TICKET = {"ticket_id": "CD-3", "tier": "Standard", "message": "Refund my $1,200 from 45 days ago",
               "charge_amount": 1200.0, "days_since_charge": 45, "duplicate_charge": False,
               "refund_requested": True}

llm = FakeLLM()
retriever = fake_retriever()
manager = ma.Manager(llm=llm, retriever=retriever, anchors_fn=lambda q, c: [], trace=False)
result = manager.handle(KB_TICKET, case_id="K1")

check("a knowledge question dispatches only the knowledge specialist (triage said no ticket specialist)",
      result["routing"]["dispatch"] == ["knowledge_specialist"], result["routing"])
check("routing records who decided each specialist",
      result["routing"]["decided_by"]["knowledge_specialist"].startswith("policy")
      and result["routing"]["decided_by"]["ticket_specialist"].startswith("triage"), result["routing"]["decided_by"])
tasks = result["tasks"]
check("one root task, one triage, one specialist, one synthesis",
      [t["kind"] for t in tasks] == ["manager", "triage", "specialist", "synthesis"], [t["kind"] for t in tasks])
check("child tasks name the root as parent", all(t["parent_id"] == tasks[0]["id"] for t in tasks[1:]))
spec = tasks[2]
check("the specialist task went CREATED -> ASSIGNED -> RUNNING -> COMPLETED, in order",
      [x["state"] for x in spec["transitions"]] == ["CREATED", "ASSIGNED", "RUNNING", "COMPLETED"],
      spec["transitions"])
check("every task has start/end timestamps and a latency", all(
    t["started_at"] and t["ended_at"] and t["latency_ms"] is not None for t in tasks))
hand = result["handoffs"]
check("a dispatch and a return hand-off, manager -> specialist -> manager, with task ids",
      [h["direction"] for h in hand] == ["dispatch", "return"]
      and hand[0]["from_task"] == tasks[0]["id"] and hand[0]["to_task"] == spec["id"]
      and hand[1]["from_task"] == spec["id"] and hand[1]["to_task"] == tasks[0]["id"], hand)
check("the return hand-off carries the specialist's token cost",
      hand[1]["input_tokens"] == 100 and hand[1]["output_tokens"] == 10, hand[1])
check("the knowledge specialist retrieved with the single agent's top_k and no filter",
      retriever.calls and retriever.calls[0]["top_k"] == 5 and retriever.calls[0]["where"] is None, retriever.calls)
check("synthesis used the same reply rules as the single agent",
      llm.calls[-1]["messages"][0]["content"] == __import__("ticket_reply").TICKET_SYSTEM_PROMPT)
check("the synthesis prompt contains the numbered sources and the specialist findings",
      "Sources:\n[1] (article HC-001" in llm.calls[-1]["messages"][1]["content"]
      and "[knowledge specialist]" in llm.calls[-1]["messages"][1]["content"])
check("the final reply's [1] is resolved to the chunk it cites",
      result["citations"] == [{"marker": 1, "chunk_id": "HC-001-markdown-1", "article_id": "HC-001", "section": "Section"}],
      result["citations"])
check("tokens are summed over every model call (triage + specialist + synthesis)",
      result["meter"]["model_calls"] == 3 and result["meter"]["input_tokens"] == 300
      and result["meter"]["tokens"] == 330, result["meter"])
check("cost = tokens x the Week 7 rate card",
      abs(result["meter"]["cost_usd"] - (300 * 0.15 + 30 * 0.60) / 1e6) < 1e-12)
check("the context re-send multiplier divides total prompt tokens by the ticket's own tokens",
      result["meter"]["base_context_tokens"] > 0 and
      result["meter"]["context_resend_multiplier"] == round(300 / result["meter"]["base_context_tokens"], 2))
check("the reference facts never reach either arm's prompt",
      all("REFERENCE" not in m["content"] for c in llm.calls for m in c["messages"]))
check("root status COMPLETED, not degraded", result["status"] == "COMPLETED" and not result["degraded"])

# rule floor: a charge on the account always needs the ticket specialist, whatever triage says
llm2 = FakeLLM(synthesis="[ESCALATED-PRIORITY]\nCD-2: Your duplicate $89.00 charge will be refunded in full [1].")
manager2 = ma.Manager(llm=llm2, retriever=fake_retriever(), anchors_fn=lambda q, c: [], trace=False)
r2 = manager2.handle(REFUND_TICKET, case_id="R1")
check("a charge on the account dispatches the ticket specialist by rule, before the knowledge specialist",
      r2["routing"]["dispatch"] == ["ticket_specialist", "knowledge_specialist"]
      and r2["routing"]["decided_by"]["ticket_specialist"].startswith("rule"), r2["routing"])
ticket_findings = r2["specialists"]["ticket_specialist"]
check("the ticket specialist decided eligibility in code from ticket_policy (duplicate -> eligible, $89.00)",
      ticket_findings["facts"]["eligible"] is True and "$89.00" in ticket_findings["facts"]["determination"]
      and ticket_findings["facts"]["escalation_required"] is True, ticket_findings["facts"])
check("the ticket specialist retrieved HC-008 passages only",
      all(c.startswith("HC-008") for c in ticket_findings["chunk_ids"]), ticket_findings["chunk_ids"])
check("sources for synthesis are the union of both specialists' passages, de-duplicated",
      len(r2["retrieved"]) == 8 and len({c["chunk_id"] for c in r2["retrieved"]}) == 8)
check("no re-synthesis when the reply agrees with the determination", r2["meter"]["resynthesis"] == 0)

# conflict resolution: a late charge, the first draft promises a refund, the rewrite does not
drafts = iter(["CD-3: We will refund your $1,200.00 charge.",
               "CD-3: The charge is 45 days old, outside the 30-day refund window, so it cannot be refunded."])
llm3 = FakeLLM(synthesis=lambda messages, n: next(drafts))
manager3 = ma.Manager(llm=llm3, retriever=fake_retriever(), anchors_fn=lambda q, c: [], trace=False)
r3 = manager3.handle(LATE_TICKET, case_id="R2")
check("a reply that contradicts the determination is re-synthesised once and recorded",
      r3["meter"]["resynthesis"] == 1 and r3["resynthesis"][0]["rejected"].startswith("CD-3: We will refund")
      and "cannot be refunded" in r3["answer"], (r3["resynthesis"], r3["answer"]))
check("the rewrite request names the conflict", "contradicts" in llm3.calls[-1]["messages"][-1]["content"])
check("re-synthesis is bounded: 4 model calls = triage + 2 specialists + 2 syntheses",
      r3["meter"]["model_calls"] == 5 - 0 and len(llm3.calls) == 5, len(llm3.calls))

# triage unparseable -> fallback rule recorded
llm4 = FakeLLM(triage="I think we need everyone.")
r4 = ma.Manager(llm=llm4, retriever=fake_retriever(), anchors_fn=lambda q, c: [], trace=False).handle(KB_TICKET)
check("unparseable triage falls back to a recorded rule instead of crashing",
      r4["routing"]["decided_by"]["ticket_specialist"].startswith("fallback"), r4["routing"])

# not found: the gate refuses, no synthesis call, the routed hand-off is returned
llm5 = FakeLLM()
r5 = ma.Manager(llm=llm5, retriever=fake_retriever(distance=0.9), anchors_fn=lambda q, c: [],
                trace=False).handle(dict(KB_TICKET, message="Does CloudDesk integrate with Slack?"))
check("below the evidence gate: refused, not_found, routed hand-off, no synthesis call",
      r5["refused"] and r5["not_found"] and r5["answer"].startswith("CD-1: Thanks for contacting")
      and "specialist" in r5["answer"] and [c["kind"] for c in llm5.calls] == ["manager-triage"],
      (r5["refusal_reason"], [c["kind"] for c in llm5.calls]))
check("the knowledge specialist still completed (it reported no evidence, which is an answer)",
      [t["status"] for t in r5["tasks"]] == ["COMPLETED", "COMPLETED", "COMPLETED"], [t["status"] for t in r5["tasks"]])
# knowledge specialist whose findings omit the [n] marker: the retrieval gate
# passed, so the manager still synthesises (v1.1) and records the omission
llm6 = FakeLLM(knowledge="A reset link is valid for 30 minutes.")
r6 = ma.Manager(llm=llm6, retriever=fake_retriever(), anchors_fn=lambda q, c: [], trace=False).handle(KB_TICKET)
check("uncited findings above the gate are still evidence (v1.1); the omission is recorded",
      not r6["refused"] and r6["specialists"]["knowledge_specialist"]["findings_cite_sources"] is False
      and any(c["kind"] == "synthesis" for c in llm6.calls), (r6["refused"], r6["specialists"]))
check("a Priority not-found reply carries the escalation tag",
      ESCALATION_TAG in ma.Manager(llm=FakeLLM(), retriever=fake_retriever(distance=0.9),
                                   anchors_fn=lambda q, c: [], trace=False).handle(
                                       dict(KB_TICKET, tier="Priority"))["answer"])

# lifecycle guards
t = ma.Task("x", "y", "z")
t.transition("ASSIGNED").transition("RUNNING").transition("COMPLETED")
try:
    t.transition("RUNNING")
    check("a terminal task refuses further transitions", False)
except ValueError:
    check("a terminal task refuses further transitions", True)
try:
    ma.Task("x", "y", "z").transition("DONE")
    check("unknown states are rejected", False)
except ValueError:
    check("unknown states are rejected", True)

# ===========================================================================
print("\nFailure — HTTP 500 at the specialist boundary, bounded retries, honest degradation")

from specialist_worker import SpecialistWorker, parse_fault


class FakeSpecialist:
    def __init__(self, name, result=None, delay=0.0, boom=False):
        self.name, self.result, self.delay, self.boom = name, result, delay, boom
        self.seen = []

    def run(self, payload):
        self.seen.append(payload)
        if self.delay:
            time.sleep(self.delay)
        if self.boom:
            raise RuntimeError("secret internal detail: DATABASE_URL=postgres://x:y@z")
        return self.result


kb = FakeSpecialist("knowledge_specialist", {"specialist": "knowledge_specialist", "evidence": True,
                                             "findings": "valid for 30 minutes [1]", "model_cited": [1],
                                             "chunks": chunks_for("HC-001", 2), "calls": [
                                                 {"input_tokens": 50, "output_tokens": 5, "latency_ms": 1}],
                                             "tool_calls": 1, "best_distance": 0.5, "lexical_anchors": []})
tk = FakeSpecialist("ticket_specialist", {"specialist": "ticket_specialist",
                                          "facts": ma.account_facts(REFUND_TICKET),
                                          "findings": "duplicate [1]", "model_cited": [1],
                                          "chunks": chunks_for("HC-008", 1), "calls": [], "tool_calls": 1})
worker = SpecialistWorker(specialists={"knowledge_specialist": kb, "ticket_specialist": tk},
                          fault={"specialist": "ticket_specialist", "ticket_id": "CD-2"}).start()
try:
    def post(path, body):
        req = urllib.request.Request(worker.url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    check("parse_fault reads specialist:TICKET", parse_fault("ticket_specialist:cd-2") ==
          {"specialist": "ticket_specialist", "ticket_id": "CD-2"})
    status, body = post("/specialist/ticket_specialist", {"ticket": REFUND_TICKET})
    check("the faulted specialist answers HTTP 500 for the faulted ticket, before running",
          status == 500 and body.get("fault") is True and not tk.seen, (status, body, len(tk.seen)))
    status, body = post("/specialist/ticket_specialist", {"ticket": LATE_TICKET})
    check("the same specialist answers 200 for another ticket", status == 200 and body["status"] == "ok", status)
    status, body = post("/specialist/knowledge_specialist", {"ticket": REFUND_TICKET})
    check("the other specialist answers 200 for the faulted ticket", status == 200, status)
    status, body = post("/specialist/nobody", {"ticket": REFUND_TICKET})
    check("an unknown specialist is 404", status == 404, status)

    transport = ma.HTTPTransport(worker.url, retry_policy={"backoff_s": 0.0})
    m = ma.Manager(llm=FakeLLM(), transport=transport, trace=False)
    rf = m.handle(REFUND_TICKET, case_id="F1")
    failed = next(t for t in rf["tasks"] if t["agent"] == "ticket_specialist")
    check("the manager retried exactly max_attempts times and stopped",
          failed["attempts"] == ma.RETRY_POLICY["max_attempts"]
          and [a["status"] for a in transport.log if a["name"] == "ticket_specialist"] == [500, 500],
          (failed["attempts"], transport.log))
    check("the task is FAILED with the error recorded (kind, status, attempts)",
          failed["status"] == "FAILED" and failed["error"]["kind"] == "http_error"
          and failed["error"]["status"] == 500 and failed["error"]["attempts"] == 2, failed["error"])
    check("the lifecycle shows CREATED -> ASSIGNED -> RUNNING -> FAILED",
          [x["state"] for x in failed["transitions"]] == ["CREATED", "ASSIGNED", "RUNNING", "FAILED"])
    check("the failure propagates: root FAILED, degraded, an error entry, retries counted",
          rf["status"] == "FAILED" and rf["degraded"] and len(rf["errors"]) == 1 and rf["meter"]["retries"] == 1,
          (rf["status"], rf["errors"], rf["meter"]["retries"]))
    check("the manager did not invent an answer: the reply is the routed hand-off with the tier's tag",
          rf["refused"] and rf["answer"].startswith(ESCALATION_TAG) and "passed your ticket to a specialist" in rf["answer"]
          and "89" not in rf["answer"], rf["answer"])
    check("no synthesis call was made after the failure", not any(c["kind"] == "synthesis" for c in m.llm.calls))
    check("the return hand-off records the failure", any(h["direction"] == "return" and h["status"] == "failed"
                                                         for h in rf["handoffs"]), rf["handoffs"])
    check("the other specialist was still dispatched and completed over HTTP",
          any(t["agent"] == "knowledge_specialist" and t["status"] == "COMPLETED" and t["transport"] == "http"
              for t in rf["tasks"]))

    ro = m.handle(LATE_TICKET, case_id="F2")
    check("an unrelated ticket through the same worker is unaffected", ro["status"] == "COMPLETED"
          and not ro["degraded"] and not ro["errors"], (ro["status"], ro["errors"]))

    boom = FakeSpecialist("knowledge_specialist", boom=True)
    worker.specialists["knowledge_specialist"] = boom
    status, body = post("/specialist/knowledge_specialist", {"ticket": LATE_TICKET})
    check("a specialist exception is a 500 whose body names the specialist and leaks no internals",
          status == 500 and body["error"] == "knowledge_specialist failed" and "DATABASE_URL" not in json.dumps(body),
          body)
    rb = m.handle(LATE_TICKET, case_id="F3")
    check("that failure also degrades honestly and the safe message carries no internals",
          rb["degraded"] and "DATABASE_URL" not in json.dumps(rb) and "Traceback" not in json.dumps(rb))
    worker.specialists["knowledge_specialist"] = kb

    slow = FakeSpecialist("ticket_specialist", tk.result, delay=1.5)
    worker.specialists["ticket_specialist"] = slow
    worker.fault = None
    slow_transport = ma.HTTPTransport(worker.url, timeout_s=0.5, retry_policy={"backoff_s": 0.0})
    rt = ma.Manager(llm=FakeLLM(), transport=slow_transport, trace=False).handle(REFUND_TICKET, case_id="F4")
    timed = next(t for t in rt["tasks"] if t["agent"] == "ticket_specialist")
    check("a specialist slower than the timeout is TIMED_OUT, not retried, and degrades",
          timed["status"] == "TIMED_OUT" and timed["attempts"] == 1 and rt["degraded"]
          and timed["error"]["kind"] == "timeout", (timed["status"], timed["attempts"], timed["error"]))
    time.sleep(1.6)                     # let the slow fake finish before the worker stops
finally:
    worker.stop()

# ===========================================================================
print("\nMetrics — percentiles and aggregates")

check("nearest-rank p50/p95/p99 on 1..10", (ma.percentile(list(range(1, 11)), 50), ma.percentile(list(range(1, 11)), 95),
                                            ma.percentile(list(range(1, 11)), 99)) == (5, 10, 10))
check("percentile of an empty list is None", ma.percentile([], 50) is None)
rows = [{"mode": "M1", "latency_ms": 1000.0 * i, "refused": False, "input_tokens": 100, "output_tokens": 10,
         "tokens": 110, "cost_usd": 0.0001, "model_calls": 3, "handoffs": 2, "specialist_calls": 1,
         "resynthesis": 0, "context_resend_multiplier": 4.0, "pass": i % 2 == 0, "assertions_pass": True,
         "judge": "PASS" if i % 2 == 0 else "FAIL", "citation_correct": True, "retrieved_expected_article": True,
         "errors": [], "retries": 0, "status": "COMPLETED", "not_found": False, "degraded": False}
        for i in range(1, 11)]
s = wb.summarise_arm(rows)
check("pass rate, latency and token aggregates come from the rows",
      s["passes"] == 5 and s["pass_rate_pct"] == 50.0 and s["latency_p50_ms"] == 5500.0
      and s["latency_p99_ms"] == 10000.0 and s["total_tokens"] == 1100 and s["model_calls"] == 30
      and s["handoffs"] == 20 and abs(s["total_cost_usd"] - 0.001) < 1e-9, s)
check("per-mode pass rates are reported", s["by_mode"] == {"M1": {"n": 10, "passes": 5}})

# ===========================================================================
print("\nServer — the Week 10 routes over a real socket")

import server as srv


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


port = free_port()
httpd = srv.StudioServer(("127.0.0.1", port), srv.RAGRequestHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def get(path):
    with urllib.request.urlopen(base + path, timeout=60) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post(path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


try:
    with urllib.request.urlopen(base + "/", timeout=30) as r:
        html = r.read().decode("utf-8")
    check("GET / serves the Week 10 view and the chatbot controls", r.status == 200 and
          'data-view="week10"' in html and 'id="w10Mode"' in html and 'id="w10Send"' in html
          and "/api/week10/chat" in html and 'id="w10Examples"' in html)
    check("the chat is a conversation with a mode selector, optional ticket context and a benchmark section",
          "CloudDesk Support Assistant" in html and 'id="w10Thread"' in html and 'id="w10TicketCtx"' in html
          and 'id="w10BenchTabs"' in html and "Benchmark &amp; Evaluation" in html
          and 'value="single">Single Agent' in html and 'value="multi">Multi-Agent' in html)
    check("the ticket fields and their ids survived the redesign (payload contract unchanged)",
          all(f'id="{i}"' in html for i in ("w10TicketId", "w10Tier", "w10Amount", "w10Days", "w10Dup", "w10Refund")))
    check("the page asks the backend whether the model is loaded instead of guessing", "/api/week10/status" in html)
    check("no client-side request timeout is left that could call a running request failed",
          "AbortController" not in html.split("week 10 */")[1].split("reports */")[0])
    status, body = get("/api/week10/status")
    check("GET /api/week10/status reports model state without loading anything",
          status == 200 and body.get("model_loaded") is False and "embedder_loaded" in body
          and "reranker_loaded" in body and "running" in body, (status, body))
    status, body = get("/api/week10")
    check("GET /api/week10 returns the artefacts and the examples",
          status == 200 and "examples" in body and "manifest" in body and "modes" in body, (status, list(body)[:6]))
    check("the examples include not-found questions and carry ticket fields",
          any(e["id"].startswith("NF") for e in body["examples"]) and all("ticket" in e for e in body["examples"]))
    status, body = post("/api/week10/chat", {"mode": "single", "message": ""})
    check("POST /api/week10/chat with an empty message -> 400", status == 400 and body.get("status") == "error", status)
    status, body = post("/api/week10/chat", {"mode": "triple", "message": "hi"})
    check("POST /api/week10/chat with a bad mode -> 400", status == 400, status)
    status, body = post("/api/week10/chat", {"mode": "single", "message": "hi", "inject_fault": "ticket_specialist"})
    check("a fault can only be injected into the multi path -> 400", status == 400, status)
    status, body = post("/api/week10/chat", {"mode": "single", "message": "hi",
                                             "ticket": {"charge_amount": "abc"}})
    check("a non-numeric charge amount -> 400", status == 400, (status, body))
    with srv.exclusive("test hold"):
        status, body = post("/api/week10/chat", {"mode": "multi", "message": "hi"})
        check("a chat while another run holds the model lock -> 409", status == 409 and "test hold" in body.get("error", ""))

    # The route's contract for a degraded answer, without loading a model:
    # swap the live runner for a fake in this process.
    import week10_ui
    real_run_chat = week10_ui.run_chat

    def fake_run_chat(mode, message, ticket=None, inject_fault=None):
        return {"mode": mode, "answer": "CD-1: routed to a person", "refused": True, "degraded": bool(inject_fault),
                "status": "FAILED" if inject_fault else "COMPLETED", "errors": [], "tasks": [], "handoffs": [],
                "meter": {"model_calls": 0}, "latency_ms": 1.0, "citations": []}
    week10_ui.run_chat = fake_run_chat
    try:
        status, body = post("/api/week10/chat", {"mode": "multi", "message": "hi", "inject_fault": "ticket_specialist"})
        check("a degraded answer is 503 with status 'degraded', a safe error, and the routed reply",
              status == 503 and body.get("status") == "degraded" and "specialist was unavailable" in body.get("error", "")
              and body.get("answer") == "CD-1: routed to a person", (status, body))
        status, body = post("/api/week10/chat", {"mode": "multi", "message": "hi"})
        check("a normal answer is 200 with status ok", status == 200 and body.get("status") == "ok", status)
    finally:
        week10_ui.run_chat = real_run_chat
    status, body = get("/api/reports")
    check("the Reports view lists the Week 10 README and report",
          status == 200 and {d["key"] for d in body["docs"]} >= {"week10", "week10_report"})
finally:
    httpd.shutdown()
    httpd.server_close()

# ===========================================================================
print("\nUI backend — model state and the opt-in warm-up flag")

import week10_ui as w10ui
st = w10ui.status()
check("status() reads the module caches and reports nothing loaded in a fresh process",
      st["model_loaded"] is False and st["embedder_loaded"] is False and st["reranker_loaded"] is False
      and st["model"], st)
import ticket_reply
check("status() did not load the drafter as a side effect", ticket_reply._PIPELINE is None)
sys.path.insert(0, str(ROOT))
import main as cli
parsed = cli.build_parser().parse_args(["ui", "--warm", "--no-browser", "--port", "8123"])
check("`python main.py ui --warm` is accepted and off by default",
      parsed.warm is True and cli.build_parser().parse_args(["ui"]).warm is False)
try:
    w10ui.build_ticket("hi", {"tier": "Priority", "charge_amount": "89", "days_since_charge": "3",
                              "duplicate_charge": True, "refund_requested": True})
    check("build_ticket keeps every entered ticket field", True)
    w10ui.build_ticket("hi", {"charge_amount": "89"})
    check("a charge without days is rejected, not silently dropped", False)
except ValueError:
    check("a charge without days is rejected, not silently dropped", True)

# ===========================================================================
print("\nDeliverables — present and within their limits")

for name in ("cases_10.json", "results_single.json", "results_multi.json", "comparison.json", "comparison.csv",
             "race_table.md", "handoffs.log", "failure_case.md", "failure_case.json", "verdict.md", "REPORT.md",
             "checklist.md", "README.md"):
    path = ROOT / "week10" / name
    if path.exists():
        check(f"week10/{name} exists and is not empty", path.stat().st_size > 0)
    else:
        skip(f"week10/{name}", "not generated yet")
verdict = ROOT / "week10" / "verdict.md"
if verdict.exists():
    lines = [l for l in verdict.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("verdict.md is at most 10 non-empty lines", len(lines) <= 10, len(lines))
failure_json = ROOT / "week10" / "failure_case.json"
if failure_json.exists():
    f = json.loads(failure_json.read_text(encoding="utf-8"))
    check("the stored failure case shows bounded retries and a FAILED task with its error",
          f["faulted_case"]["status"] == "FAILED" and f["faulted_case"]["failed_task"]["error"]["attempts"] == 2
          and f["faulted_case"]["refused"] and f["control_case"]["status"] == "COMPLETED", f["faulted_case"]["status"])

# ===========================================================================
print()
if failures:
    print(f"{len(failures)} check(s) failed:")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("All checks passed.")

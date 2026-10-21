"""Week 10: a manager and two specialists on the Week 6 ticket-reply task.

The single agent (src/ticket_reply.py) answers a ticket with one model call
over retrieved help-centre passages. This module answers the same ticket
with a manager that dispatches work to two specialists and writes the reply
from what they return:

    manager            triage (one model call) -> dispatch -> synthesis
                       (one model call, same rules as the single agent)
    ticket specialist  the customer's account: tier, charge, refund policy.
                       Uses ticket_policy (HC-008 as code) as its tool and
                       retrieves the HC-008 passages it needs to cite.
    knowledge specialist
                       the help centre: the same retriever, distance gate and
                       lexical-anchor bypass as the single agent, then one
                       model call that lists source-backed findings.

Same model, same decoding settings, same retrieval configuration and the
same reply rules (ticket_reply.TICKET_SYSTEM_PROMPT) as the single agent.
Neither path sees the reference facts: those belong to the judge.

Every piece of work is a Task with a recorded lifecycle
(CREATED -> ASSIGNED -> RUNNING -> COMPLETED | FAILED | TIMED_OUT | CANCELLED)
and every dispatch and return is a Handoff. Tokens are metered per model
call, so the manager's re-sent context is counted every time it is sent.

Specialists are reached through a transport. In-process is the default; the
HTTP transport talks to src/specialist_worker.py, which is the dependency
boundary the failure test breaks with an injected HTTP 500. The manager
retries 5xx answers under RETRY_POLICY and then degrades honestly: a
required specialist that stays down turns the reply into a routed hand-off
(ticket_policy.routed_reply), never into an invented answer.
"""

import json
import os
import socket
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

import tracing
from agent_runtime import RATE_CARD, cost_usd, extract_json
from ticket_policy import (
    ESCALATION_TAG,
    PRIORITY_TIER,
    REFUND_WINDOW_DAYS,
    refund_eligible,
    routed_reply,
    ticket_block,
)

# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

PROMPT_VERSION = "week10-multi-v1.1"      # v1.0: uncited findings counted as no evidence

# Bounded: a 5xx from a specialist is retried once, then the task fails.
# Timeouts are not retried: a specialist that is slow once is slow twice.
RETRY_POLICY = {"max_attempts": 2, "retry_on": (500, 502, 503, 504), "backoff_s": 0.2}

# Wall-clock budget per specialist call. The HTTP transport enforces it as
# a socket timeout; in-process calls cannot be pre-empted, so they are
# measured against it after the fact and marked TIMED_OUT when they exceed it.
SPECIALIST_TIMEOUT_S = float(os.environ.get("WEEK10_SPECIALIST_TIMEOUT_S", "600"))

# At most one re-synthesis when the written reply contradicts the ticket
# specialist's policy determination.
MAX_RESYNTHESIS = 1

POLICY_ARTICLE = "HC-008"
POLICY_TOP_K = 3

STATES = ("CREATED", "ASSIGNED", "RUNNING", "COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED")
TERMINAL = ("COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED")

TRIAGE_SYSTEM_PROMPT = (
    "You are the CloudDesk support manager. Decide which specialists a ticket needs "
    "before anyone writes the reply.\n"
    "- ticket_specialist: reads the customer's account (support tier, the charge on the "
    "account, refund eligibility). Needed when the customer asks about a charge, a "
    "refund, billing on their account, or is on the Priority tier.\n"
    "- knowledge_specialist: searches the help-centre articles for steps, limits and "
    "policies.\n"
    "Reply with only this JSON object and nothing else:\n"
    '{"ticket_specialist": true or false, "knowledge_specialist": true or false, '
    '"reason": "one short sentence"}'
)

KNOWLEDGE_SYSTEM_PROMPT = (
    "You are the CloudDesk knowledge specialist. You do not write to the customer.\n"
    "From the numbered sources, list the facts, steps and policies that answer the "
    "customer's message, one per line, each ending with the source number in square "
    "brackets, e.g. [2].\n"
    "If no source covers a part of the message, write 'Not covered: <that part>'.\n"
    "Never invent numbers, prices, limits or policies. Keep it under 90 words."
)

TICKET_SYSTEM_PROMPT_SPECIALIST = (
    "You are the CloudDesk ticket specialist. You do not write to the customer.\n"
    "You are given the account facts, the policy determination already made from those "
    "facts, and numbered policy passages. Write the findings the reply writer needs: the "
    "known facts, the determination, the required actions, and anything missing. Cite the "
    "passage number in square brackets for every policy statement. Never change the "
    "determination or invent amounts, dates or rules. Keep it under 90 words."
)

NOT_COVERED = "not covered"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# tasks and hand-offs
# ---------------------------------------------------------------------------

class Task:
    """One unit of work with a recorded lifecycle."""

    def __init__(self, agent, role, kind, parent_id=None, input_summary=None):
        self.id = new_id("task")
        self.parent_id = parent_id
        self.agent = agent
        self.role = role
        self.kind = kind
        self.status = "CREATED"
        self.transitions = [{"state": "CREATED", "at": now_iso()}]
        self.input_summary = input_summary
        self.output_summary = None
        self.error = None
        self.attempts = 0
        self.started_at = None
        self.ended_at = None
        self.latency_ms = None
        self.model_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0
        self.transport = None
        self._t0 = None

    def transition(self, state):
        if state not in STATES:
            raise ValueError(f"unknown task state {state!r}")
        if self.status in TERMINAL:
            raise ValueError(f"task {self.id} is already {self.status}")
        self.status = state
        self.transitions.append({"state": state, "at": now_iso()})
        if state == "RUNNING":
            self.started_at = now_iso()
            self._t0 = time.perf_counter()
        if state in TERMINAL:
            self.ended_at = now_iso()
            if self._t0 is not None:
                self.latency_ms = round((time.perf_counter() - self._t0) * 1000.0, 1)
        return self

    def charge(self, call):
        """Add one model call's cost to this task."""
        self.model_calls += 1
        self.input_tokens += int(call.get("input_tokens") or 0)
        self.output_tokens += int(call.get("output_tokens") or 0)

    @property
    def tokens(self):
        return self.input_tokens + self.output_tokens

    def as_dict(self):
        return {
            "id": self.id, "parent_id": self.parent_id, "agent": self.agent, "role": self.role,
            "kind": self.kind, "status": self.status, "transitions": self.transitions,
            "input": self.input_summary, "output": self.output_summary, "error": self.error,
            "attempts": self.attempts, "transport": self.transport,
            "started_at": self.started_at, "ended_at": self.ended_at,
            "latency_ms": self.latency_ms, "model_calls": self.model_calls,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "tokens": self.tokens, "tool_calls": self.tool_calls,
        }


class SpecialistError(Exception):
    """A specialist could not be used. Carries only what is safe to show."""

    def __init__(self, kind, message, status=None, attempts=0):
        super().__init__(message)
        self.kind = kind            # http_error | timeout | unavailable | bad_response
        self.status = status
        self.attempts = attempts


def summarise(value, limit=240):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------------------
# the model and the retriever, both injectable
# ---------------------------------------------------------------------------

class PipelineLLM:
    """Exactly the single agent's model: ticket_reply's pipeline and
    generator.GENERATION_PARAMS, with tokens counted by its own tokenizer."""

    def __init__(self):
        from ticket_reply import TICKET_MODEL_NAME
        self.name = TICKET_MODEL_NAME

    def count_tokens(self, text):
        from ticket_reply import get_ticket_pipeline
        return len(get_ticket_pipeline().tokenizer(text, add_special_tokens=False)["input_ids"])

    def __call__(self, messages, max_new_tokens=None):
        from generator import GENERATION_PARAMS, extract_text
        from ticket_reply import get_ticket_pipeline

        generate = get_ticket_pipeline()
        tokenizer = generate.tokenizer
        prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        input_tokens = len(tokenizer(prompt, add_special_tokens=False)["input_ids"])
        params = dict(GENERATION_PARAMS)
        if max_new_tokens:
            params["max_new_tokens"] = int(max_new_tokens)

        started = time.perf_counter()
        text = extract_text(generate(messages, **params))
        latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
        output_tokens = len(tokenizer(text, add_special_tokens=False)["input_ids"])
        return {"text": text, "input_tokens": input_tokens, "output_tokens": output_tokens,
                "latency_ms": latency_ms, "max_new_tokens": params["max_new_tokens"]}


def default_llm():
    return PipelineLLM()


def default_retriever(query, top_k=None, where=None):
    """The single agent's retrieval: hybrid RRF + cross-encoder on the markdown index."""
    from generator import TOP_K, retrieve_for_mode
    from ticket_reply import DEFAULT_RETRIEVAL_MODE
    from vector_store import DEFAULT_STRATEGY
    return retrieve_for_mode(query, top_k=top_k or TOP_K, strategy=DEFAULT_STRATEGY,
                             where=where, mode=DEFAULT_RETRIEVAL_MODE)


def default_anchors(query, chunks):
    from generator import lexical_anchors
    from vector_store import DEFAULT_STRATEGY
    return lexical_anchors(query, chunks, DEFAULT_STRATEGY)


def retrieval_config():
    from generator import GENERATION_PARAMS, RELEVANCE_THRESHOLD, TOP_K
    from ticket_reply import DEFAULT_RETRIEVAL_MODE
    from vector_store import DEFAULT_STRATEGY
    return {"retrieval_mode": DEFAULT_RETRIEVAL_MODE, "strategy": DEFAULT_STRATEGY,
            "top_k": TOP_K, "threshold": RELEVANCE_THRESHOLD,
            "model_params": dict(GENERATION_PARAMS)}


def chunk_distance(chunk):
    value = chunk.get("distance")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def portable_chunk(chunk):
    """A chunk as plain JSON, so it survives the HTTP transport unchanged."""
    out = {}
    for key in ("chunk_id", "article_id", "source_file", "last_updated", "section",
                "product_area", "content", "rank", "distance", "bm25_score", "rrf_score",
                "rerank_score", "dense_rank", "bm25_rank", "is_table"):
        if key in chunk:
            value = chunk[key]
            if hasattr(value, "item"):           # numpy scalar
                value = value.item()
            out[key] = value
    return out


def evidence_gate(query, chunks, threshold, anchors_fn):
    """The single agent's distance gate with its lexical-anchor bypass."""
    if not chunks:
        return False, None, [], "no_chunks_retrieved"
    distances = [d for d in (chunk_distance(c) for c in chunks) if d is not None]
    best = min(distances) if distances else None
    anchors = []
    try:
        anchors = list(anchors_fn(query, chunks) or [])
    except Exception:                       # the anchor index is an optimisation, not a gate
        anchors = []
    if best is not None and best > threshold and not anchors:
        return False, best, anchors, f"distance_gate ({best:.4f} > {threshold})"
    return True, best, anchors, None


def build_context(chunks):
    """Numbered sources, identical to generator.build_context."""
    return "\n\n".join(
        f"[{i + 1}] (article {c.get('article_id')}, file {c.get('source_file')}, "
        f"updated {c.get('last_updated')})\n{c.get('content')}"
        for i, c in enumerate(chunks)
    )


def cited_markers(text):
    from generator import CITATION_MARKER
    return [int(m) for m in CITATION_MARKER.findall(text or "")]


# ---------------------------------------------------------------------------
# specialists
# ---------------------------------------------------------------------------

def account_facts(ticket):
    """What the account says, decided in code from ticket_policy (HC-008)."""
    tier = ticket.get("tier")
    amount = ticket.get("charge_amount")
    days = ticket.get("days_since_charge")
    duplicate = bool(ticket.get("duplicate_charge"))
    requested = bool(ticket.get("refund_requested"))
    facts = {
        "ticket_id": ticket.get("ticket_id"), "tier": tier,
        "escalation_required": tier == PRIORITY_TIER, "escalation_tag": ESCALATION_TAG,
        "refund_requested": requested, "charge_amount": amount, "days_since_charge": days,
        "duplicate_charge": duplicate, "refund_window_days": REFUND_WINDOW_DAYS,
        "eligible": None, "determination": None, "missing": [],
    }
    if amount is None and not requested:
        facts["determination"] = "No charge on the account and no refund requested: nothing to assess."
        return facts
    if amount is None:
        facts["missing"].append("charge amount and charge date are not on the account")
        facts["determination"] = ("The customer asks for a refund but no charge is on the account; "
                                  "eligibility cannot be assessed without the charge.")
        return facts
    facts["eligible"] = refund_eligible(ticket)
    if duplicate:
        facts["determination"] = (f"Duplicate charge: refunded in full regardless of age. "
                                  f"Refund amount ${amount:,.2f}.")
    elif facts["eligible"]:
        facts["determination"] = (f"Charged {days} days ago, within the {REFUND_WINDOW_DAYS}-day window: "
                                  f"eligible for a full refund of ${amount:,.2f}.")
    else:
        facts["determination"] = (f"Charged {days} days ago, outside the {REFUND_WINDOW_DAYS}-day window: "
                                  f"NOT eligible. Do not promise or process a refund; automatic "
                                  f"renewal can be turned off in Organization Settings > Billing.")
    return facts


def facts_block(facts):
    lines = [f"Support tier: {facts['tier']}"
             + (f" (reply must carry {facts['escalation_tag']} on the first line)"
                if facts["escalation_required"] else " (no escalation tag)")]
    if facts["charge_amount"] is not None:
        charge = f"${facts['charge_amount']:,.2f}, charged {facts['days_since_charge']} days ago"
        if facts["duplicate_charge"]:
            charge += " (duplicate of an earlier charge on the same invoice)"
        lines.append(f"Charge on account: {charge}")
    lines.append(f"Refund requested: {'yes' if facts['refund_requested'] else 'no'}")
    lines.append(f"Determination: {facts['determination']}")
    if facts["missing"]:
        lines.append("Missing: " + "; ".join(facts["missing"]))
    return "\n".join(lines)


class TicketSpecialist:
    name = "ticket_specialist"
    role = "Ticket / support specialist"

    def __init__(self, llm=None, retriever=None):
        self.llm = llm or default_llm()
        self.retriever = retriever or default_retriever

    def run(self, payload):
        ticket = payload["ticket"]
        facts = account_facts(ticket)
        result = {"specialist": self.name, "facts": facts, "chunks": [], "findings": None,
                  "model_cited": [], "calls": [], "tool_calls": 0}
        if facts["charge_amount"] is None and not facts["refund_requested"]:
            # Nothing on the account to assess: say so without spending a model call.
            result["findings"] = facts_block(facts)
            return result

        from retriever import build_where
        query = f"{ticket.get('message', '')} refund eligibility window duplicate charge"
        chunks = [portable_chunk(c) for c in
                  self.retriever(query, top_k=POLICY_TOP_K, where=build_where(article_id=POLICY_ARTICLE))]
        result["tool_calls"] = 1
        result["chunks"] = chunks

        messages = [
            {"role": "system", "content": TICKET_SYSTEM_PROMPT_SPECIALIST},
            {"role": "user", "content": (
                f"Policy passages:\n{build_context(chunks) or '(none retrieved)'}\n\n"
                f"Account facts:\n{facts_block(facts)}\n\n"
                f"Customer message: {ticket.get('message', '')}\n\nFindings:")},
        ]
        call = self.llm(messages, max_new_tokens=160)
        result["calls"].append(call)
        result["findings"] = call["text"]
        result["model_cited"] = cited_markers(call["text"])
        return result


class KnowledgeSpecialist:
    name = "knowledge_specialist"
    role = "Knowledge / RAG specialist"

    def __init__(self, llm=None, retriever=None, anchors_fn=None, threshold=None, top_k=None):
        self.llm = llm or default_llm()
        self.retriever = retriever or default_retriever
        self.anchors_fn = anchors_fn or default_anchors
        config = retrieval_config() if (threshold is None or top_k is None) else {}
        self.threshold = threshold if threshold is not None else config["threshold"]
        self.top_k = top_k if top_k is not None else config["top_k"]

    def run(self, payload):
        ticket = payload["ticket"]
        query = ticket.get("message", "")
        chunks = [portable_chunk(c) for c in self.retriever(query, top_k=self.top_k, where=None)]
        passed, best, anchors, reason = evidence_gate(query, chunks, self.threshold, self.anchors_fn)
        result = {"specialist": self.name, "chunks": chunks, "best_distance": best,
                  "lexical_anchors": anchors, "evidence": passed, "gate_reason": reason,
                  "findings": None, "model_cited": [], "not_covered": [], "calls": [], "tool_calls": 1}
        if not passed:
            result["findings"] = (f"No supporting evidence: the retrieved help-centre passages do not "
                                  f"match the message ({reason}).")
            return result

        messages = [
            {"role": "system", "content": KNOWLEDGE_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Sources:\n{build_context(chunks)}\n\n"
                f"Customer message: {query}\n\nFindings:")},
        ]
        call = self.llm(messages, max_new_tokens=160)
        result["calls"].append(call)
        result["findings"] = call["text"]
        result["model_cited"] = cited_markers(call["text"])
        result["not_covered"] = [line.strip() for line in call["text"].splitlines()
                                 if line.strip().lower().startswith(NOT_COVERED)]
        # v1.0 treated findings without a [n] marker as "no evidence" and the
        # manager refused the ticket. The first benchmark run showed the 1.5B
        # model writing correct, passage-grounded findings and simply omitting
        # the marker on 3 of 7 knowledge tickets, so the gate (which had passed)
        # was being overruled by a formatting heuristic. v1.1: the retrieval
        # gate decides evidence; whether the findings cite is recorded.
        result["findings_cite_sources"] = bool(result["model_cited"])
        return result


def build_specialists(llm=None, retriever=None, anchors_fn=None, **knowledge_kwargs):
    llm = llm or default_llm()
    retriever = retriever or default_retriever
    return {
        TicketSpecialist.name: TicketSpecialist(llm=llm, retriever=retriever),
        KnowledgeSpecialist.name: KnowledgeSpecialist(llm=llm, retriever=retriever,
                                                      anchors_fn=anchors_fn, **knowledge_kwargs),
    }


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------

class InProcessTransport:
    kind = "in-process"

    def __init__(self, specialists):
        self.specialists = specialists

    def call(self, name, payload, timeout_s=None):
        if name not in self.specialists:
            raise SpecialistError("unavailable", f"no specialist named {name}", attempts=1)
        return self.specialists[name].run(payload), 1


class HTTPTransport:
    """Specialists behind src/specialist_worker.py. Bounded retries on 5xx."""

    kind = "http"

    def __init__(self, base_url, retry_policy=None, timeout_s=None):
        self.base_url = base_url.rstrip("/")
        self.retry_policy = {**RETRY_POLICY, **(retry_policy or {})}
        self.timeout_s = timeout_s or SPECIALIST_TIMEOUT_S
        self.log = []

    def call(self, name, payload, timeout_s=None):
        timeout = timeout_s or self.timeout_s
        url = f"{self.base_url}/specialist/{name}"
        body = json.dumps(payload, default=str).encode("utf-8")
        attempts = 0
        while True:
            attempts += 1
            request = urllib.request.Request(url, data=body, method="POST",
                                             headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                self.log.append({"name": name, "attempt": attempts, "status": 200})
                if data.get("status") != "ok" or "result" not in data:
                    raise SpecialistError("bad_response", f"{name} specialist returned an "
                                          f"unexpected payload", attempts=attempts)
                return data["result"], attempts
            except urllib.error.HTTPError as error:
                self.log.append({"name": name, "attempt": attempts, "status": error.code})
                retryable = error.code in self.retry_policy["retry_on"]
                if retryable and attempts < self.retry_policy["max_attempts"]:
                    time.sleep(self.retry_policy["backoff_s"])
                    continue
                raise SpecialistError("http_error",
                                      f"{name} specialist returned HTTP {error.code} "
                                      f"({attempts} attempt{'s' if attempts != 1 else ''})",
                                      status=error.code, attempts=attempts) from None
            except socket.timeout:
                self.log.append({"name": name, "attempt": attempts, "status": "timeout"})
                raise SpecialistError("timeout", f"{name} specialist did not answer within "
                                      f"{timeout:g}s", attempts=attempts) from None
            except urllib.error.URLError as error:
                self.log.append({"name": name, "attempt": attempts, "status": "unreachable"})
                if isinstance(error.reason, socket.timeout):
                    raise SpecialistError("timeout", f"{name} specialist did not answer within "
                                          f"{timeout:g}s", attempts=attempts) from None
                raise SpecialistError("unavailable", f"{name} specialist is unreachable",
                                      attempts=attempts) from None


# ---------------------------------------------------------------------------
# the manager
# ---------------------------------------------------------------------------

class Manager:
    name = "manager"
    role = "Manager / orchestrator"

    def __init__(self, llm=None, transport=None, retriever=None, anchors_fn=None,
                 timeout_s=None, max_resynthesis=MAX_RESYNTHESIS, trace=True,
                 specialist_kwargs=None):
        self.llm = llm or default_llm()
        self.transport = transport or InProcessTransport(
            build_specialists(llm=self.llm, retriever=retriever, anchors_fn=anchors_fn,
                              **(specialist_kwargs or {})))
        # None lets the transport own the budget (the HTTP transport has its
        # own); a value here overrides it for every specialist call.
        self.timeout_s = timeout_s
        self.max_resynthesis = max_resynthesis
        self.trace = trace

    # ---- bookkeeping --------------------------------------------------------

    def _handoff(self, run, direction, from_task, to_task, summary, tokens=None, status="sent"):
        record = {
            "id": new_id("handoff"), "at": now_iso(), "direction": direction,
            "from_task": from_task.id, "from_agent": from_task.agent,
            "to_task": to_task.id, "to_agent": to_task.agent,
            "summary": summarise(summary), "status": status,
            "input_tokens": (tokens or {}).get("input_tokens"),
            "output_tokens": (tokens or {}).get("output_tokens"),
            "context_chars": len(json.dumps(summary, default=str)) if summary is not None else 0,
        }
        run["handoffs"].append(record)
        return record

    def _model_call(self, run, task, messages, max_new_tokens):
        call = self.llm(messages, max_new_tokens=max_new_tokens)
        task.charge(call)
        run["calls"].append({"task": task.id, "agent": task.agent, "kind": task.kind,
                             "input_tokens": call["input_tokens"],
                             "output_tokens": call["output_tokens"],
                             "latency_ms": call["latency_ms"]})
        return call

    # ---- steps --------------------------------------------------------------

    def triage(self, run, root, ticket):
        task = Task(self.name, self.role, "triage", parent_id=root.id,
                    input_summary=summarise(ticket_block(ticket)))
        run["tasks"].append(task)
        task.transition("ASSIGNED").transition("RUNNING")
        messages = [{"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Ticket:\n{ticket_block(ticket)}\n\nJSON:"}]
        call = self._model_call(run, task, messages, 60)
        parsed = extract_json(call["text"])
        facts = account_facts(ticket)
        needs_account = facts["charge_amount"] is not None or facts["refund_requested"]

        routing = {"triage_raw": call["text"], "triage_parsed": parsed if isinstance(parsed, dict) else None,
                   "decided_by": {}, "dispatch": []}
        # The knowledge specialist is always consulted: every reply has to be
        # grounded in the help centre, and the single agent retrieves on every
        # ticket too. Triage decides the ticket specialist, with a floor: a
        # charge or a refund request on the account always needs it.
        routing["decided_by"][KnowledgeSpecialist.name] = "policy: always consulted"
        if needs_account:
            routing["decided_by"][TicketSpecialist.name] = "rule: charge or refund request on the account"
            ticket_needed = True
        elif isinstance(parsed, dict) and "ticket_specialist" in parsed:
            ticket_needed = bool(parsed.get("ticket_specialist"))
            routing["decided_by"][TicketSpecialist.name] = f"triage: {ticket_needed}"
        else:
            ticket_needed = facts["escalation_required"]
            routing["decided_by"][TicketSpecialist.name] = (
                f"fallback (triage unparseable): {ticket_needed} from the tier")
        routing["dispatch"] = ([TicketSpecialist.name] if ticket_needed else []) + [KnowledgeSpecialist.name]
        task.output_summary = summarise(routing["dispatch"])
        task.transition("COMPLETED")
        return routing

    def dispatch(self, run, root, name, role, ticket):
        task = Task(name, role, "specialist", parent_id=root.id,
                    input_summary=summarise({"ticket_id": ticket.get("ticket_id"),
                                             "message": ticket.get("message")}))
        task.transport = self.transport.kind
        run["tasks"].append(task)
        task.transition("ASSIGNED")
        self._handoff(run, "dispatch", root, task, {"ticket": ticket})
        task.transition("RUNNING")
        try:
            result, attempts = self.transport.call(name, {"ticket": ticket, "case_id": run["case_id"]},
                                                   timeout_s=self.timeout_s)
            task.attempts = attempts
        except SpecialistError as error:
            task.attempts = error.attempts
            task.error = {"kind": error.kind, "status": error.status, "message": str(error),
                          "attempts": error.attempts}
            task.transition("TIMED_OUT" if error.kind == "timeout" else "FAILED")
            run["errors"].append({"task": task.id, "agent": name, **task.error})
            self._handoff(run, "return", task, root, {"error": task.error}, status=task.status.lower())
            return task, None

        for call in result.get("calls") or []:
            task.charge(call)
            run["calls"].append({"task": task.id, "agent": name, "kind": "specialist",
                                 "input_tokens": call.get("input_tokens"),
                                 "output_tokens": call.get("output_tokens"),
                                 "latency_ms": call.get("latency_ms")})
        task.tool_calls = int(result.get("tool_calls") or 0)
        task.output_summary = summarise(result.get("findings"))
        elapsed = (time.perf_counter() - task._t0) if task._t0 else 0.0
        budget = self.timeout_s or getattr(self.transport, "timeout_s", None) or SPECIALIST_TIMEOUT_S
        if elapsed > budget:
            task.error = {"kind": "timeout", "message": f"{name} specialist took {elapsed:.0f}s, "
                          f"over the {budget:g}s budget (measured after the fact)"}
            task.transition("TIMED_OUT")
            run["errors"].append({"task": task.id, "agent": name, **task.error})
            self._handoff(run, "return", task, root, {"error": task.error}, status="timed_out")
            return task, None
        task.transition("COMPLETED")
        self._handoff(run, "return", task, root,
                      {"findings": result.get("findings"), "chunks": [c.get("chunk_id") for c in result.get("chunks", [])]},
                      tokens={"input_tokens": task.input_tokens, "output_tokens": task.output_tokens},
                      status="completed")
        return task, result

    def synthesise(self, run, root, ticket, sources, ticket_result, knowledge_result):
        from ticket_reply import TICKET_SYSTEM_PROMPT

        findings = []
        if ticket_result:
            findings.append("[ticket specialist] The policy determination below was made from the "
                            "account records and is authoritative.\n" + facts_block(ticket_result["facts"])
                            + ("\n" + ticket_result["findings"] if ticket_result.get("findings") else ""))
        if knowledge_result:
            findings.append("[knowledge specialist]\n" + (knowledge_result.get("findings") or ""))

        task = Task(self.name, self.role, "synthesis", parent_id=root.id,
                    input_summary=summarise({"sources": [c["chunk_id"] for c in sources],
                                             "findings": findings}))
        run["tasks"].append(task)
        task.transition("ASSIGNED").transition("RUNNING")
        messages = [
            {"role": "system", "content": TICKET_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Sources:\n{build_context(sources)}\n\n"
                f"Specialist findings:\n" + "\n\n".join(findings) + "\n\n"
                f"Ticket (do not repeat these fields in the reply):\n{ticket_block(ticket)}\n\n"
                f"Write the reply to the customer now, starting with {ticket.get('ticket_id')}.")},
        ]
        call = self._model_call(run, task, messages, None)
        answer = call["text"]
        resynthesis = []

        # Conflict resolution: the written reply must agree with the ticket
        # specialist's determination. One bounded re-synthesis, recorded.
        if ticket_result:
            problem = self._contradicts(ticket, ticket_result["facts"], answer)
            if problem and len(resynthesis) < self.max_resynthesis:
                resynthesis.append({"reason": problem, "rejected": answer})
                messages = messages + [
                    {"role": "assistant", "content": answer},
                    {"role": "user", "content": f"That reply contradicts the account's policy "
                                                f"determination: {problem}. Rewrite the reply so it "
                                                f"follows the determination. Reply with the new reply only."},
                ]
                call = self._model_call(run, task, messages, None)
                answer = call["text"]
        task.output_summary = summarise(answer)
        task.transition("COMPLETED")
        return answer, call, messages, resynthesis

    @staticmethod
    def _contradicts(ticket, facts, answer):
        import ticket_assertions
        case_like = {"ticket": ticket}
        reply_like = {"text": answer}
        if facts.get("eligible") is False:
            verdict = ticket_assertions.no_refund_outside_window(case_like, reply_like)
            if verdict is not None and not verdict[0]:
                return f"not eligible, but the reply {verdict[1]}"
        if facts.get("eligible") and facts.get("charge_amount") is not None:
            verdict = ticket_assertions.refund_amount_numeric(case_like, reply_like)
            if verdict is not None and not verdict[0]:
                return f"eligible for ${facts['charge_amount']:.2f}, but the reply does not state that amount"
        return None

    # ---- the whole thing ----------------------------------------------------

    def handle(self, ticket, case_id=None, source="week10-multi", session_id=None, tags=None):
        started = time.perf_counter()
        config = retrieval_config()
        run = {"case_id": case_id, "tasks": [], "handoffs": [], "calls": [], "errors": []}
        root = Task(self.name, self.role, "manager", input_summary=summarise(ticket_block(ticket)))
        run["tasks"].append(root)
        root.transition("ASSIGNED").transition("RUNNING")

        result = {
            "query": ticket.get("message"), "ticket": ticket, "case_id": case_id,
            "prompt_version": PROMPT_VERSION, "prompt_sha": None,
            "model_name": getattr(self.llm, "name", "unknown"),
            "model_params": config["model_params"], "retrieval_mode": config["retrieval_mode"],
            "strategy": config["strategy"], "top_k": config["top_k"], "threshold": config["threshold"],
            "min_coverage": None, "filter": None, "retrieved": [], "sources": [],
            "answer": None, "raw_answer": None, "output_tokens": None, "max_new_tokens": None,
            "refused": False, "refusal_reason": None, "not_found": False, "degraded": False,
            "model_cited": [], "citations": [], "routing": None, "resynthesis": [],
            "specialists": {}, "errors": run["errors"], "prompt_messages": None,
        }
        from ticket_reply import TICKET_SYSTEM_PROMPT
        result["prompt_sha"] = tracing.sha256(TICKET_SYSTEM_PROMPT)

        routing = self.triage(run, root, ticket)
        result["routing"] = routing

        specialist_results = {}
        failed = []
        for name in routing["dispatch"]:
            role = TicketSpecialist.role if name == TicketSpecialist.name else KnowledgeSpecialist.role
            task, outcome = self.dispatch(run, root, name, role, ticket)
            if outcome is None:
                failed.append(name)
            else:
                specialist_results[name] = outcome
                result["specialists"][name] = {k: v for k, v in outcome.items() if k != "chunks"}
                result["specialists"][name]["chunk_ids"] = [c.get("chunk_id") for c in outcome.get("chunks", [])]

        ticket_result = specialist_results.get(TicketSpecialist.name)
        knowledge_result = specialist_results.get(KnowledgeSpecialist.name)

        def refuse(reason, degraded=False, not_found=False):
            result["answer"] = routed_reply(ticket)
            result["refused"] = True
            result["refusal_reason"] = reason
            result["degraded"] = degraded
            result["not_found"] = not_found
            root.output_summary = summarise(result["answer"])
            root.transition("FAILED" if degraded else "COMPLETED")

        if failed:
            # A dispatched specialist was required. Nothing is invented in its
            # place: the ticket is routed to a person, and the reply says so.
            refuse("specialist_unavailable (" + "; ".join(
                e["message"] for e in run["errors"]) + ")", degraded=True)
        else:
            sources, seen = [], set()
            for outcome in (knowledge_result, ticket_result):
                if not outcome or not outcome.get("chunks"):
                    continue
                if outcome is knowledge_result and not outcome.get("evidence"):
                    continue
                for chunk in outcome["chunks"]:
                    if chunk.get("chunk_id") not in seen:
                        seen.add(chunk.get("chunk_id"))
                        sources.append(chunk)
            has_account_case = bool(ticket_result and ticket_result["facts"].get("determination")
                                    and (ticket_result["facts"].get("charge_amount") is not None
                                         or ticket_result["facts"].get("refund_requested")))
            knowledge_ok = bool(knowledge_result and knowledge_result.get("evidence"))

            if not sources or (not knowledge_ok and not has_account_case):
                reason = (knowledge_result or {}).get("gate_reason") or "no_sources"
                refuse(f"no_evidence ({reason})", not_found=True)
            else:
                answer, call, messages, resynthesis = self.synthesise(
                    run, root, ticket, sources, ticket_result, knowledge_result)
                result["answer"] = answer
                result["raw_answer"] = answer
                result["output_tokens"] = call["output_tokens"]
                result["max_new_tokens"] = call["max_new_tokens"]
                result["prompt_messages"] = messages
                result["resynthesis"] = resynthesis
                result["model_cited"] = cited_markers(answer)
                result["citations"] = [
                    {"marker": m, "chunk_id": sources[m - 1].get("chunk_id"),
                     "article_id": sources[m - 1].get("article_id"),
                     "section": sources[m - 1].get("section")}
                    for m in result["model_cited"] if 0 < m <= len(sources)]
                result["retrieved"] = [
                    {"rank": i + 1, "chunk_id": c.get("chunk_id"), "article_id": c.get("article_id"),
                     "section": c.get("section"), "distance": c.get("distance")}
                    for i, c in enumerate(sources)]
                result["sources"] = [{"marker": c["marker"], "chunk_id": c["chunk_id"]}
                                     for c in result["citations"]]
                root.output_summary = summarise(answer)
                root.transition("COMPLETED")

        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        input_tokens = sum(c.get("input_tokens") or 0 for c in run["calls"])
        output_tokens = sum(c.get("output_tokens") or 0 for c in run["calls"])
        base_context_tokens = self._count_tokens(ticket_block(ticket))
        result["meter"] = {
            "model_calls": len(run["calls"]), "input_tokens": input_tokens,
            "output_tokens": output_tokens, "tokens": input_tokens + output_tokens,
            "cost_usd": cost_usd(input_tokens, output_tokens), "rate_card": RATE_CARD,
            "handoffs": len(run["handoffs"]),
            "specialist_calls": sum(1 for t in run["tasks"] if t.kind == "specialist"),
            "tool_calls": sum(t.tool_calls for t in run["tasks"]),
            "resynthesis": len(result["resynthesis"]),
            "base_context_tokens": base_context_tokens,
            "context_resend_multiplier": (round(input_tokens / base_context_tokens, 2)
                                          if base_context_tokens else None),
            "errors": len(run["errors"]), "retries": sum(max(0, t.attempts - 1) for t in run["tasks"]),
        }
        result["latency_ms"] = elapsed_ms
        result["tasks"] = [t.as_dict() for t in run["tasks"]]
        result["handoffs"] = run["handoffs"]
        result["calls"] = run["calls"]
        result["transport"] = self.transport.kind
        result["status"] = root.status

        if self.trace:
            self._record_trace(result, elapsed_ms, source, session_id, tags)
        result.pop("prompt_messages", None)
        return result

    def _count_tokens(self, text):
        counter = getattr(self.llm, "count_tokens", None)
        if counter is not None:
            try:
                return int(counter(text))
            except Exception:
                pass
        return max(1, len(text.split()))        # a fake model: words stand in for tokens

    def _record_trace(self, result, elapsed_ms, source, session_id, tags):
        try:
            record = tracing.build_trace(result, latency_ms={"total": elapsed_ms}, source=source)
            record["kind"] = "week10_multi_agent"
            record["ticket"] = result["ticket"]
            record["tasks"] = result["tasks"]
            record["handoffs"] = result["handoffs"]
            record["meter"] = result["meter"]
            record["errors"] = result["errors"]
            tracing.append(record, session_id=session_id, tags=tags)
            result["trace_id"] = record["trace_id"]
            result["langfuse_trace_id"] = record.get("langfuse_trace_id")
        except Exception as error:                  # answering must survive tracing
            result["trace_id"] = None
            result["trace_error"] = str(error)
            print(f"  [tracing] trace not recorded: {error}")


# ---------------------------------------------------------------------------
# percentiles, shared with the benchmark and its tests
# ---------------------------------------------------------------------------

def percentile(values, pct):
    """Nearest-rank percentile. With n=10, p95 and p99 are the maximum."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(-(-pct * len(ordered) // 100)))     # ceil(pct/100 * n)
    return ordered[min(rank, len(ordered)) - 1]

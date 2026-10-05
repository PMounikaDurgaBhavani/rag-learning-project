"""Memory for the refund-chase agent: a sliding window, a rolling summary, and
one long-term fact that survives a process restart.

Short term — ThreadMemory
-------------------------
A 30-turn ticket thread does not fit the loop's budget: re-sent every lap, a
full thread costs its whole length in prompt tokens per lap. ThreadMemory keeps
the last WINDOW_TURNS turns verbatim and folds everything older into one
rolling summary, written by the same model the agent uses. The context the
agent sees is then bounded — summary + window — however long the thread grows.

The price is that a summary is lossy, and nobody chose what it loses. Every
summarisation call is metered like any other model call, and the summary text
is kept so an audit can check which details survived.

Long term — LongTermMemory
--------------------------
One JSON file, keyed by customer id. A fact is written only from a tool result
(find_ticket returning a tier), with its source ticket and time, never from
what a customer said about themselves. The file is the whole mechanism: a new
process reads it on start, which is what "survives a restart" means here.
mem0 does the same job with an LLM extracting the facts and a vector store
holding them; with exactly one fact of one known type, a keyed file is the
honest size of the problem.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agent_runtime import chat

LONG_TERM_PATH = Path(__file__).resolve().parent.parent / "week7" / "memory" / "long_term.json"

WINDOW_TURNS = 6

SUMMARY_PROMPT = (
    "You maintain the running summary of a customer support ticket thread. "
    "Merge the earlier summary and the new turns into one updated summary of at "
    "most 5 short sentences. Keep facts a support agent needs to resolve a refund. "
    "Reply with the summary only."
)

SUMMARY_MAX_NEW_TOKENS = 120


def render_turns(turns):
    return "\n".join(f"{turn['role']}: {turn['text']}" for turn in turns)


class ThreadMemory:
    """Sliding window over a ticket thread, with a rolling summary of what fell out."""

    def __init__(self, window_turns=WINDOW_TURNS, summarise=True):
        self.window_turns = window_turns
        self.summarise = summarise
        self.window = []
        self.summary = ""
        self.evicted_turns = 0
        self.meter = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0,
                      "latency_ms": 0.0}
        self.summaries = []             # every version, for the audit

    def add(self, turn):
        self.window.append(turn)
        if self.summarise and len(self.window) > self.window_turns:
            # Evict half a window at once: one summarisation call per three
            # turns rather than per turn, at the same final window size.
            cut = len(self.window) - self.window_turns + self.window_turns // 2
            evicted, self.window = self.window[:cut], self.window[cut:]
            self._fold(evicted)

    def _fold(self, evicted):
        user = (f"Earlier summary:\n{self.summary or '(none yet)'}\n\n"
                f"New turns:\n{render_turns(evicted)}\n\nUpdated summary:")
        response = chat([{"role": "system", "content": SUMMARY_PROMPT},
                         {"role": "user", "content": user}],
                        max_new_tokens=SUMMARY_MAX_NEW_TOKENS)
        self.meter["model_calls"] += 1
        self.meter["input_tokens"] += response["input_tokens"]
        self.meter["output_tokens"] += response["output_tokens"]
        self.meter["latency_ms"] += response["latency_ms"]
        self.summary = response["text"].strip()
        self.evicted_turns += len(evicted)
        self.summaries.append({"after_turn": self.evicted_turns + len(self.window),
                               "summary": self.summary})

    def as_text(self):
        """What a system downstream is allowed to know about the thread."""
        parts = []
        if self.summary:
            parts.append(f"Summary of the {self.evicted_turns} earlier turns:\n{self.summary}")
        label = "Most recent turns" if self.summary else "Thread"
        parts.append(f"{label}:\n{render_turns(self.window)}")
        return "\n\n".join(parts)


class LongTermMemory:
    """Facts about a customer that outlive the process. One JSON file."""

    def __init__(self, path=LONG_TERM_PATH):
        self.path = Path(path)
        self.facts = {}
        if self.path.exists():
            self.facts = json.loads(self.path.read_text(encoding="utf-8"))

    def recall(self, customer_id):
        return dict(self.facts.get(customer_id or "", {}))

    def remember(self, customer_id, key, value, source):
        if not customer_id or value is None:
            return
        entry = self.facts.setdefault(customer_id, {})
        entry[key] = {"value": value, "source": source,
                      "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "written_by_pid": os.getpid()}
        self._save()

    def remember_from_ticket(self, ticket):
        """The only write path the systems use: a tier a tool returned."""
        if isinstance(ticket, dict) and not ticket.get("error") and ticket.get("tier"):
            self.remember(ticket.get("customer_id"), "tier", ticket["tier"],
                          f"find_ticket({ticket.get('ticket_id')})")

    def tier_for(self, customer_id):
        return (self.recall(customer_id).get("tier") or {}).get("value")

    def _save(self):
        # Write-then-rename, so a crash mid-write cannot leave half a file for
        # the next process to read.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(self.facts, stream, indent=2)
            stream.write("\n")
        os.replace(temp, self.path)

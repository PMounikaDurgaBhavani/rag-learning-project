"""Shared runtime for the agent and the workflow: one model, one token meter,
one rate card, one budget type.

Both systems in the race import this, so neither can quietly use a different
model, a different decoding setting or a different way of counting tokens —
the race is between the two control flows.

Token accounting
----------------
Every call records its own prompt tokens and completion tokens. The agent
re-sends the whole message list each lap, so its prompt tokens grow every lap;
summing per-call prompt tokens is the only honest total. Counting the final
call alone would understate the agent by multiples.

Cost
----
The models run locally, so the real cash cost is zero and a cost column would
be meaningless. RATE_CARD prices the tokens at a published rate for a hosted
model of this size, and cost is derived from the measured tokens. Tokens are
the measurement; cost is that measurement times a documented constant.
"""

import json
import os
import re
import time

MODEL_NAME = os.environ.get("RAG_AGENT_LLM", "Qwen/Qwen2.5-1.5B-Instruct")

GENERATION_PARAMS = {"max_new_tokens": 220, "do_sample": False}

# USD per 1M tokens. Stand-in rate card (GPT-4o-mini class pricing), applied to
# locally measured token counts; see the module docstring.
RATE_CARD = {"input_per_mtok": 0.15, "output_per_mtok": 0.60}


def cost_usd(input_tokens, output_tokens):
    return (input_tokens * RATE_CARD["input_per_mtok"]
            + output_tokens * RATE_CARD["output_per_mtok"]) / 1_000_000


_MODEL = None
_TOKENIZER = None


def load_model():
    global _MODEL, _TOKENIZER

    if _MODEL is None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        device = os.environ.get("RAG_AGENT_DEVICE") or (
            "mps" if torch.backends.mps.is_available() else "cpu"
        )
        dtype = torch.float16 if device == "mps" else torch.float32
        _TOKENIZER = AutoTokenizer.from_pretrained(MODEL_NAME)
        _MODEL = AutoModelForCausalLM.from_pretrained(MODEL_NAME, dtype=dtype).to(device)
        _MODEL.eval()

    return _MODEL, _TOKENIZER


def release_model():
    global _MODEL, _TOKENIZER
    _MODEL = None
    _TOKENIZER = None


def chat(messages, tools=None, max_new_tokens=None):
    """One model call. Returns the text and exactly what it cost to produce."""

    model, tokenizer = load_model()

    prompt = tokenizer.apply_chat_template(
        messages, tools=tools, add_generation_prompt=True, tokenize=False
    )
    encoded = tokenizer(prompt, return_tensors="pt").to(model.device)
    input_tokens = int(encoded["input_ids"].shape[-1])

    started = time.perf_counter()
    generated = model.generate(
        **encoded,
        **{**GENERATION_PARAMS,
           **({"max_new_tokens": max_new_tokens} if max_new_tokens else {})},
        pad_token_id=tokenizer.eos_token_id,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)

    completion = generated[0][input_tokens:]
    output_tokens = int(completion.shape[-1])

    return {
        "text": tokenizer.decode(completion, skip_special_tokens=True).strip(),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def parse_tool_calls(text):
    """Tool calls the model asked for.

    Qwen emits <tool_call>{...}</tool_call>. A small model also emits the bare
    object often enough that refusing to read it would measure the parser
    rather than the loop, so a bare object carrying name+arguments counts too.
    """

    calls = []
    for blob in TOOL_CALL_TAG.findall(text):
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("name"):
            calls.append({"name": payload["name"],
                          "arguments": payload.get("arguments") or {}})

    if not calls:
        bare = extract_json(text)
        if isinstance(bare, dict) and bare.get("name") and "arguments" in bare:
            calls.append({"name": bare["name"], "arguments": bare.get("arguments") or {}})

    return calls


def extract_json(text):
    """The first balanced JSON object in the text, or None."""

    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(text)):
            character = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


# ---------------------------------------------------------------------------
# budgets
# ---------------------------------------------------------------------------

class Budgets:
    """All four limits, checked together before every lap.

    A budget that is defined but never checked is a comment, so exceeded()
    returns the name of the first limit that has been reached and the loop has
    exactly one place to ask.
    """

    def __init__(self, max_iterations=6, max_tokens=8000, max_cost_usd=0.01,
                 max_wall_seconds=120.0):
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.max_cost_usd = max_cost_usd
        self.max_wall_seconds = max_wall_seconds

    def as_dict(self):
        return {"max_iterations": self.max_iterations, "max_tokens": self.max_tokens,
                "max_cost_usd": self.max_cost_usd, "max_wall_seconds": self.max_wall_seconds}

    def exceeded(self, iterations, tokens, cost, elapsed_seconds):
        if iterations >= self.max_iterations:
            return "max_iterations", f"{iterations} >= {self.max_iterations}"
        if tokens >= self.max_tokens:
            return "max_tokens", f"{tokens} >= {self.max_tokens}"
        if cost >= self.max_cost_usd:
            return "max_cost_usd", f"${cost:.6f} >= ${self.max_cost_usd:.6f}"
        if elapsed_seconds >= self.max_wall_seconds:
            return "max_wall_seconds", f"{elapsed_seconds:.1f}s >= {self.max_wall_seconds:.1f}s"
        return None, None


# ---------------------------------------------------------------------------
# the output contract, shared by both systems
# ---------------------------------------------------------------------------

DECISIONS = ("refund_approved", "refund_denied", "already_refunded", "escalated")


def blank_result(ticket_id):
    return {"ticket_id": ticket_id, "decision": None, "refund_amount": None,
            "tier": None, "reply": ""}


def normalise_result(payload, ticket_id):
    """Coerce a model's JSON into the output contract. Unknown decisions become
    None rather than a guess: a wrong decision and an unparseable one are
    different failures and the race counts them separately."""

    result = blank_result(ticket_id)
    if not isinstance(payload, dict):
        return result

    decision = str(payload.get("decision") or "").strip().lower().replace(" ", "_")
    result["decision"] = decision if decision in DECISIONS else None

    amount = payload.get("refund_amount")
    if isinstance(amount, str):
        cleaned = amount.replace("$", "").replace(",", "").strip()
        try:
            amount = float(cleaned)
        except ValueError:
            amount = None
    result["refund_amount"] = round(float(amount), 2) if isinstance(amount, (int, float)) else None

    tier = str(payload.get("tier") or "").strip().lower()
    result["tier"] = tier or None
    result["reply"] = str(payload.get("reply") or "").strip()
    return result

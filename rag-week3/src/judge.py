"""LLM judge for ticket replies: one binary criterion, prompt read from a file.

The prompt lives in week6/judge_vN.txt rather than in code so that the
version that produced a number is a file that can be committed, diffed and
cited. A prompt file has a system part and a user part separated by a line
containing only USER_MARKER; the user part carries {{TICKET}},
{{REFERENCE}} and {{REPLY}} placeholders.

The judge model is deliberately not the model that drafted the reply.
Override with RAG_JUDGE_MODEL (any Hugging Face chat model) and
RAG_JUDGE_DEVICE (cpu / mps).
"""

import os
import re
from pathlib import Path

import tracing
from ticket_policy import ticket_block

JUDGE_MODEL = os.environ.get("RAG_JUDGE_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")

JUDGE_PARAMS = {
    "max_new_tokens": 80,
    "do_sample": False,
    "return_full_text": False,
}

USER_MARKER = "=== USER ==="

VERDICT = re.compile(r"VERDICT\W{0,4}(PASS|FAIL)\b", re.I)
REASON = re.compile(r"REASON\s*[:\-]\s*(.+)", re.I)


def load_prompt(path):
    text = Path(path).read_text(encoding="utf-8")
    if USER_MARKER not in text:
        raise ValueError(f"{path} has no '{USER_MARKER}' line")
    system, user = text.split(USER_MARKER, 1)
    return {
        "path": str(path),
        "system": system.strip(),
        "user": user.strip(),
        "sha": tracing.sha256(text),
    }


def render(prompt, case, reply_text):
    reference = "\n".join(f"- {fact}" for fact in case["reference"])
    user = (
        prompt["user"]
        .replace("{{TICKET}}", ticket_block(case["ticket"]))
        .replace("{{REFERENCE}}", reference)
        .replace("{{REPLY}}", reply_text)
    )
    return [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": user},
    ]


_PIPELINE = None


def _pipeline():
    global _PIPELINE

    if _PIPELINE is None:
        import torch
        from transformers import pipeline

        device = os.environ.get("RAG_JUDGE_DEVICE") or (
            "mps" if torch.backends.mps.is_available() else "cpu"
        )
        dtype = torch.float16 if device == "mps" else torch.float32
        _PIPELINE = pipeline(
            "text-generation", model=JUDGE_MODEL, device=device, dtype=dtype
        )

    return _PIPELINE


def parse(raw):
    """(verdict or None, reason). An answer naming both PASS and FAIL
    without a VERDICT line is unparseable, not a coin toss."""
    match = VERDICT.search(raw)
    verdict = match.group(1).upper() if match else None

    if verdict is None:
        bare = set(re.findall(r"\b(PASS|FAIL)\b", raw.upper()))
        verdict = bare.pop() if len(bare) == 1 else None

    reason = REASON.search(raw)
    return verdict, (reason.group(1).strip() if reason else raw.strip()[:200])


def judge_reply(prompt, case, reply_text):
    """One judge call. An unparseable answer counts as FAIL and is flagged."""
    from generator import extract_text

    messages = render(prompt, case, reply_text)
    raw = extract_text(_pipeline()(messages, **JUDGE_PARAMS))
    verdict, reason = parse(raw)

    return {
        "verdict": verdict or "FAIL",
        "parsed": verdict is not None,
        "reason": reason,
        "raw": raw,
        "messages": messages,
    }

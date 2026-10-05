"""RAGAS metrics, computed locally, for ticket replies.

The `ragas` package calls a hosted LLM (OpenAI by default) for every
verdict. This project runs offline on an 8 GB M1, so the four metrics are
re-implemented here from their RAGAS definitions, with the week 6 judge
model (SmolLM2-1.7B-Instruct) as the LLM and all-MiniLM-L6-v2 as the
embedder:

    faithfulness       supported claims / claims in the reply
                       (claim supported = inferable from the retrieved context)
    answer_relevancy   mean cosine(question, q_i) over questions q_i
                       generated back from the reply; 0 if the reply is a refusal
    context_precision  average precision of the retrieved ranking, where a chunk
                       counts as relevant if it helps arrive at the reference facts
    context_recall     reference facts attributable to the retrieved context / facts

Two deliberate differences from the library, both because a 1.7B model is
the LLM:

  - Claims are the reply's sentences (greetings and sign-offs dropped), not
    LLM-decomposed statements. A 1.7B model decomposes unreliably, and a
    sentence split is reproducible.
  - Every yes/no verdict is read from the next-token probabilities of "Yes"
    and "No" rather than parsed from generated text, so no verdict is ever
    unparseable and each comes with a probability.

Numbers from this module are "RAGAS-style, local", not comparable to scores
from the hosted library.
"""

import re

import numpy as np

YES_VARIANTS = ("Yes", "yes", " Yes", " yes", "YES")
NO_VARIANTS = ("No", "no", " No", " no", "NO")

# A reply sentence shorter than this, or matching SKIP_LINE, is not a claim.
MIN_CLAIM_WORDS = 4
SKIP_LINE = re.compile(
    r"^\s*(\[[A-Z-]+\]|hello|hi\b|dear\b|thank you for (reaching|contacting)|thanks for (reaching|contacting)|"
    r"best regards|kind regards|regards|sincerely|\[your name\]|clouddesk support)",
    re.I,
)


# ---------------------------------------------------------------------------
# model primitives
# ---------------------------------------------------------------------------

def _model():
    import judge
    pipe = judge._pipeline()
    return pipe.model, pipe.tokenizer


def _token_ids(tokenizer, variants):
    ids = set()
    for text in variants:
        encoded = tokenizer.encode(text, add_special_tokens=False)
        if encoded:
            ids.add(encoded[0])
    return sorted(ids)


def _next_token_probs(messages, prefix=""):
    """Softmax over the vocabulary for the first assistant token (after prefix)."""
    import torch

    model, tokenizer = _model()
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    ids = tokenizer(text + prefix, return_tensors="pt", add_special_tokens=False)
    ids = {key: value.to(model.device) for key, value in ids.items()}
    with torch.no_grad():
        logits = model(**ids).logits[0, -1].float()
    return torch.softmax(logits, dim=-1).cpu()


def p_yes(system, user):
    """P(Yes) / (P(Yes) + P(No)) for the first token of the answer."""
    _model_, tokenizer = _model()
    probs = _next_token_probs([{"role": "system", "content": system},
                               {"role": "user", "content": user}])
    yes = float(probs[_token_ids(tokenizer, YES_VARIANTS)].sum())
    no = float(probs[_token_ids(tokenizer, NO_VARIANTS)].sum())
    return yes / (yes + no) if yes + no > 0 else 0.5


def generate(system, user, max_new_tokens=120):
    import judge
    from generator import extract_text

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return extract_text(judge._pipeline()(messages, max_new_tokens=max_new_tokens,
                                          do_sample=False, return_full_text=False))


# ---------------------------------------------------------------------------
# verdict prompts
# ---------------------------------------------------------------------------

SUPPORT_SYSTEM = ("You check whether a statement is supported by a context. Answer with one "
                  "word: Yes if the statement can be directly inferred from the context, "
                  "No otherwise.")

USEFUL_SYSTEM = ("You check whether a retrieved passage is useful for giving a correct answer. "
                 "Answer with one word: Yes if the passage contains information needed to arrive "
                 "at the reference answer, No otherwise.")

QUESTIONS_SYSTEM = ("You read a customer support reply and write the customer question it answers. "
                    "Write 3 different phrasings of that question, one per line, nothing else.")


def supported(statement, context):
    return p_yes(SUPPORT_SYSTEM, f"CONTEXT:\n{context}\n\nSTATEMENT:\n{statement}\n\n"
                                 "Can the statement be directly inferred from the context?")


def useful(passage, question, reference):
    facts = "\n".join(f"- {fact}" for fact in reference)
    return p_yes(USEFUL_SYSTEM, f"QUESTION:\n{question}\n\nREFERENCE ANSWER:\n{facts}\n\n"
                                f"PASSAGE:\n{passage}\n\nIs the passage useful for arriving "
                                "at the reference answer?")


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def claims(reply_text):
    """The reply's sentences that assert something."""
    out = []
    for line in reply_text.splitlines():
        line = re.sub(r"^\s*(\d+\.|[-*•])\s*", "", line).strip()
        line = re.sub(r"^CD-\d+\s*:\s*", "", line)
        if not line or SKIP_LINE.match(line):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line):
            if len(sentence.split()) >= MIN_CLAIM_WORDS and not SKIP_LINE.match(sentence):
                out.append(sentence.strip())
    return out


def context_text(chunks):
    return "\n\n".join(f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, start=1))


def faithfulness(reply_text, chunks, threshold=0.5):
    """Share of claims inferable from the retrieved context.

    A claim is supported if the whole context OR any single chunk supports it.
    With all chunks concatenated, SmolLM2 scored sentences copied verbatim from
    a chunk at P(Yes) 0.34-0.64; against that one chunk, 0.64-0.76 (smoke test,
    hand-written replies). The best chunk also names the section the reply is
    actually quoting."""
    context = context_text(chunks)
    rows = []
    for claim in claims(reply_text):
        row = {"claim": claim, "p_full_context": round(supported(claim, context), 3)}
        if chunks:
            scores = [supported(claim, chunk["content"]) for chunk in chunks]
            best = int(np.argmax(scores))
            row["best_chunk"] = chunks[best]["chunk_id"]
            row["best_section"] = chunks[best].get("section")
            row["best_chunk_p"] = round(scores[best], 3)
        row["p_supported"] = max(row["p_full_context"], row.get("best_chunk_p", 0.0))
        row["supported"] = row["p_supported"] >= threshold
        rows.append(row)
    score = sum(r["supported"] for r in rows) / len(rows) if rows else None
    return score, rows


def answer_relevancy(question, reply_text, refused=False):
    if refused or not reply_text.strip():
        return 0.0, []
    from embeddings import embed_texts

    raw = generate(QUESTIONS_SYSTEM, f"REPLY:\n{reply_text}\n\nThe 3 questions:")
    generated = [re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", line).strip()
                 for line in raw.splitlines() if line.strip()][:3]
    if not generated:
        return 0.0, []
    vectors = embed_texts([question] + generated)
    q, g = vectors[0], vectors[1:]
    sims = g @ q / (np.linalg.norm(g, axis=1) * np.linalg.norm(q))
    return float(np.mean(sims)), generated


def context_precision(question, reference, chunks, threshold=0.5):
    """RAGAS context precision: sum_k(precision@k * v_k) / relevant-in-top-K."""
    verdicts = [useful(chunk["content"], question, reference) for chunk in chunks]
    relevant = [p >= threshold for p in verdicts]
    hits, total = 0, 0.0
    for k, is_relevant in enumerate(relevant, start=1):
        if is_relevant:
            hits += 1
            total += hits / k
    score = total / hits if hits else 0.0
    return score, [{"chunk_id": c["chunk_id"], "section": c.get("section"),
                    "p_useful": round(p, 3), "relevant": r}
                   for c, p, r in zip(chunks, verdicts, relevant)]


def context_recall(reference, chunks, threshold=0.5):
    context = context_text(chunks)
    rows = [{"fact": fact, "p_attributable": round(supported(fact, context), 3)} for fact in reference]
    for row in rows:
        row["attributable"] = row["p_attributable"] >= threshold
    score = sum(r["attributable"] for r in rows) / len(rows) if rows else None
    return score, rows


def score_reply(question, reference, reply_text, chunks, refused=False):
    faith, claim_rows = faithfulness(reply_text, chunks)
    relevancy, generated = answer_relevancy(question, reply_text, refused=refused)
    precision, chunk_rows = context_precision(question, reference, chunks)
    recall, fact_rows = context_recall(reference, chunks)
    return {
        "faithfulness": None if faith is None else round(faith, 3),
        "answer_relevancy": round(relevancy, 3),
        "context_precision": round(precision, 3),
        "context_recall": None if recall is None else round(recall, 3),
        "claims": claim_rows,
        "generated_questions": generated,
        "chunks": chunk_rows,
        "reference_facts": fact_rows,
    }

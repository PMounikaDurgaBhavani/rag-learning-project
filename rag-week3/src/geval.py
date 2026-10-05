"""G-Eval (Liu et al., 2023) for the RESOLUTION criterion, on a 1-10 scale.

Exists to set a 1-10 score next to the binary judge, not to replace it.

G-Eval has three parts, all implemented here:
  1. auto chain-of-thought: the LLM writes the evaluation steps once from the
     criterion text; the steps are frozen to a file so every reply is graded
     by the same steps;
  2. form filling: the prompt ends in "Score: " and the model fills in a number;
  3. probability weighting: the score is not the sampled token but
     sum_s s * P(s), read from the next-token distribution over 1..10.
     That gives a continuous score and removes ties.

SmolLM2's tokenizer writes numbers digit by digit, so "10" is "1" then "0".
P(1) is split into P(score 1) and P(score 10) with a second forward pass
that asks whether "0" follows the "1".
"""

from pathlib import Path

import ragas_local

STEPS_SYSTEM = ("You design grading rubrics. Given an evaluation criterion, write 4 to 6 short, "
                "numbered evaluation steps a grader should follow to score a reply on that "
                "criterion from 1 to 10. Output only the numbered steps.")

SCORE_SYSTEM = ("You grade a draft reply to a CloudDesk customer support ticket on a scale "
                "from 1 (does not resolve the issue at all) to 10 (fully resolves it).")


def steps(criterion, path):
    """The frozen evaluation steps, generated once from the criterion."""
    path = Path(path)
    if path.exists():
        return path.read_text(encoding="utf-8").strip(), False
    text = ragas_local.generate(STEPS_SYSTEM, f"CRITERION:\n{criterion}\n\nEVALUATION STEPS:",
                                max_new_tokens=220)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return text.strip(), True


def score(criterion, eval_steps, ticket_text, reference, reply_text):
    """(weighted score, {score: probability}) for one reply."""
    _model, tokenizer = ragas_local._model()
    facts = "\n".join(f"- {fact}" for fact in reference)
    messages = [
        {"role": "system", "content": SCORE_SYSTEM},
        {"role": "user", "content": (
            f"CRITERION:\n{criterion}\n\nEVALUATION STEPS:\n{eval_steps}\n\n"
            f"TICKET:\n{ticket_text}\n\nREFERENCE FACTS:\n{facts}\n\n"
            f"DRAFT REPLY:\n{reply_text}\n\n"
            "Follow the evaluation steps, then give only the score from 1 to 10."
        )},
    ]
    digit_id = {d: tokenizer.encode(str(d), add_special_tokens=False)[0] for d in range(10)}

    first = ragas_local._next_token_probs(messages, prefix="Score: ")
    p = {d: float(first[digit_id[d]]) for d in range(1, 10)}
    after_one = ragas_local._next_token_probs(messages, prefix="Score: 1")
    p_ten_given_one = float(after_one[digit_id[0]])
    p[10] = p[1] * p_ten_given_one
    p[1] = p[1] * (1 - p_ten_given_one)

    total = sum(p.values())
    dist = {s: v / total for s, v in p.items()} if total > 0 else {s: 0.1 for s in p}
    weighted = sum(s * v for s, v in dist.items())
    return weighted, {s: round(v, 4) for s, v in dist.items()}

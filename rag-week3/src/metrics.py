import re


WHITESPACE = re.compile(r"\s+")


def normalize(text):
    """Lowercase and collapse whitespace for robust substring matching."""

    return WHITESPACE.sub(" ", (text or "").lower()).strip()


def contains_all(text, keys):
    normalized = normalize(text)

    return all(
        normalize(key) in normalized
        for key in keys
    )


def is_gold_chunk(chunk, question):
    """A chunk is 'gold' when it is from the expected article and
    literally contains every answer key for the question."""

    if chunk["article_id"] != question["expected_article_id"]:
        return False

    return contains_all(
        chunk["content"],
        question["answer_keys"]
    )


def is_table_grounded(chunk, question):
    """A table answer is only usable if the retrieved chunk carries
    the table header row as well as the answer row. Without the
    header the columns cannot be interpreted."""

    header = question.get("table_header")

    if not header:
        return False

    if not is_gold_chunk(chunk, question):
        return False

    return normalize(header) in normalize(chunk["content"])


def hit_at_k(chunks, question, k, predicate=is_gold_chunk):
    return any(
        predicate(chunk, question)
        for chunk in chunks[:k]
    )


def article_hit_at_k(chunks, question, k):
    return any(
        chunk["article_id"] == question["expected_article_id"]
        for chunk in chunks[:k]
    )


def reciprocal_rank(chunks, question, predicate=is_gold_chunk):
    """1 / rank of the first gold chunk, or 0 if none was retrieved."""

    for index, chunk in enumerate(chunks):

        if predicate(chunk, question):
            return 1.0 / (index + 1)

    return 0.0


def percent(values):
    if not values:
        return 0.0

    return sum(1 for value in values if value) / len(values) * 100

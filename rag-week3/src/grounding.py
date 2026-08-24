"""Verify that a generated answer is actually supported by a chunk.

The 0.5B generator is not reliable enough to be trusted to cite its
own sources, so citations are not taken from the model's output.
Instead every retrieved chunk is scored against the answer and only
chunks that demonstrably contain the answer's content are cited.

Two signals are used:

coverage          fraction of the answer's content words that appear
                  in the chunk
unsupported nums  numbers in the answer that do not appear in the
                  chunk - a fabricated limit, price or duration shows
                  up here immediately
"""

import re


WORD = re.compile(r"[a-z0-9]+")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
CITATION_MARKER = re.compile(r"\[\d+\]")

# Minimum fraction of the answer's content words that must appear in a
# chunk for that chunk to be treated as the answer's source.
MIN_COVERAGE = 0.55

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "can", "cannot", "do", "does", "for", "from", "has", "have", "how",
    "i", "if", "in", "into", "is", "it", "its", "may", "must", "no",
    "not", "of", "on", "or", "should", "that", "the", "their", "then",
    "there", "these", "this", "to", "use", "used", "was", "were",
    "what", "when", "which", "will", "with", "you", "your",
}


def strip_citations(text):
    return CITATION_MARKER.sub(" ", text or "")


def content_words(text):
    return [
        word
        for word in WORD.findall((text or "").lower())
        if word not in STOPWORDS and len(word) > 1
    ]


def coverage(answer, chunk_text):
    """Fraction of the answer's content words present in the chunk."""

    words = content_words(strip_citations(answer))

    if not words:
        return 0.0

    chunk_words = set(content_words(chunk_text))

    found = sum(
        1
        for word in words
        if word in chunk_words
    )

    return found / len(words)


def unsupported_numbers(answer, chunk_text):
    """Numbers asserted by the answer that the chunk does not contain."""

    answer_numbers = NUMBER.findall(strip_citations(answer))
    chunk_numbers = set(NUMBER.findall(chunk_text or ""))

    return [
        number
        for number in answer_numbers
        if number not in chunk_numbers
    ]


def score_chunk(answer, chunk):
    return {
        "chunk_id": chunk["chunk_id"],
        "coverage": round(coverage(answer, chunk["content"]), 3),
        "unsupported_numbers": unsupported_numbers(
            answer, chunk["content"]
        ),
    }


def verify_answer(answer, chunks, min_coverage=MIN_COVERAGE):
    """Return the chunks that actually support the answer.

    A chunk supports the answer when it covers enough of the answer's
    content words and contradicts none of its numbers.
    """

    scored = [
        (chunk, score_chunk(answer, chunk))
        for chunk in chunks
    ]

    supporting = [
        (chunk, score)
        for chunk, score in scored
        if score["coverage"] >= min_coverage
        and not score["unsupported_numbers"]
    ]

    supporting.sort(
        key=lambda pair: pair[1]["coverage"],
        reverse=True
    )

    return supporting, [score for _, score in scored]

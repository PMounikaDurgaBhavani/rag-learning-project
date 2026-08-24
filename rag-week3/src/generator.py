import os
import re

from transformers import pipeline

from retriever import retrieve_chunks, build_where
from vector_store import DEFAULT_STRATEGY
from grounding import verify_answer, MIN_COVERAGE


MODEL_NAME = os.environ.get(
    "RAG_LLM",
    "Qwen/Qwen2.5-0.5B-Instruct"
)

# Lower distance = more similar.
#
# Calibrated by experiments/calibrate_threshold.py against the 8
# supported and 6 unsupported questions. 0.75 answers all 8 supported
# questions and refuses 4 of the 6 unsupported ones. The two survivors
# are the "near miss" traps (U3, U4), whose top-1 distance is actually
# lower than most supported questions - no distance threshold can
# separate them, so the grounding gates below have to catch them.
RELEVANCE_THRESHOLD = 0.75

TOP_K = 5

REFUSAL_MESSAGE = "I don't have enough information to answer that."

# A sentinel is used instead of the refusal sentence itself: putting
# the literal refusal text in the prompt primes the small model to
# emit it even when the answer is present in the sources.
NO_ANSWER_SENTINEL = "NOT_IN_SOURCES"

CITATION_MARKER = re.compile(r"\[(\d+)\]")

SYSTEM_PROMPT = (
    "You are a CloudDesk help centre assistant.\n"
    "Answer the question using the numbered sources below.\n"
    "Cite the source number you used in square brackets, e.g. [1].\n"
    "If the sources do not contain the specific fact asked for, "
    f"reply with exactly: {NO_ANSWER_SENTINEL}\n"
    "Never invent numbers, prices, limits or policies.\n"
    "Answer in under 60 words."
)

_PIPELINE = None


def get_pipeline():
    global _PIPELINE

    if _PIPELINE is None:
        _PIPELINE = pipeline(
            "text-generation",
            model=MODEL_NAME
        )

    return _PIPELINE


# ============================================================
# Prompt construction
# ============================================================

def build_context(chunks):
    """Number each chunk so the model can cite it."""

    blocks = []

    for index, chunk in enumerate(chunks):

        blocks.append(
            f"[{index + 1}] "
            f"(article {chunk['article_id']}, "
            f"file {chunk['source_file']}, "
            f"updated {chunk['last_updated']})\n"
            f"{chunk['content']}"
        )

    return "\n\n".join(blocks)


def extract_text(response):
    """Normalise the several shapes transformers can return."""

    generated = response[0]["generated_text"]

    if isinstance(generated, str):
        return generated.strip()

    if isinstance(generated, list) and generated:

        last = generated[-1]

        if isinstance(last, dict):
            return str(last.get("content", "")).strip()

        return str(last).strip()

    return str(generated).strip()


def said_no_answer(answer):
    normalized = answer.lower().replace(" ", "_")

    return (
        NO_ANSWER_SENTINEL.lower() in normalized
        or "don't have enough information" in answer.lower()
        or "do not have enough information" in answer.lower()
    )


def make_citation(chunk, score, marker):
    return {
        "marker": marker,
        "chunk_id": chunk["chunk_id"],
        "article_id": chunk["article_id"],
        "source_file": chunk["source_file"],
        "product_area": chunk["product_area"],
        "last_updated": chunk["last_updated"],
        "section": chunk.get("section"),
        "retrieval_distance": round(chunk["distance"], 4),
        "coverage": score["coverage"],
    }


# ============================================================
# Main entry point
# ============================================================

def generate_answer(
    query,
    top_k=TOP_K,
    strategy=DEFAULT_STRATEGY,
    product_area=None,
    article_id=None,
    threshold=RELEVANCE_THRESHOLD,
    min_coverage=MIN_COVERAGE,
    verbose=False
):
    """Answer a question from the indexed help centre articles.

    Three gates stand between a question and an answer:

      1. distance gate    nothing similar enough was retrieved
      2. model gate       the model reported the fact is not present
      3. grounding gate   no retrieved chunk actually supports the
                          text the model produced

    Citations are produced by gate 3, not copied from the model, so a
    citation can only ever point at a chunk that really contains the
    answer.
    """

    where = build_where(
        product_area=product_area,
        article_id=article_id
    )

    chunks = retrieve_chunks(
        query,
        top_k=top_k,
        strategy=strategy,
        where=where
    )

    result = {
        "query": query,
        "strategy": strategy,
        "top_k": top_k,
        "filter": where,
        "retrieved": [
            {
                "rank": chunk["rank"],
                "chunk_id": chunk["chunk_id"],
                "distance": round(chunk["distance"], 4),
            }
            for chunk in chunks
        ],
        "answer": None,
        "raw_answer": None,
        "sources": [],
        "refused": False,
        "refusal_reason": None,
        "model_cited": [],
        "best_distance": None,
    }

    # --------------------------------------------------------
    # Gate 1: nothing similar enough was retrieved
    # --------------------------------------------------------

    if not chunks:
        result["answer"] = REFUSAL_MESSAGE
        result["refused"] = True
        result["refusal_reason"] = "no_chunks_retrieved"
        return result

    best_distance = chunks[0]["distance"]
    result["best_distance"] = round(best_distance, 4)

    if verbose:
        print(f"  best distance: {best_distance:.4f}")

    if best_distance > threshold:
        result["answer"] = REFUSAL_MESSAGE
        result["refused"] = True
        result["refusal_reason"] = (
            f"distance_gate ({best_distance:.4f} > {threshold})"
        )
        return result

    # --------------------------------------------------------
    # Generate
    # --------------------------------------------------------

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Sources:\n{build_context(chunks)}\n\n"
                f"Question: {query}"
            ),
        },
    ]

    raw_answer = extract_text(
        get_pipeline()(
            messages,
            max_new_tokens=160,
            do_sample=False,
            return_full_text=False
        )
    )

    result["raw_answer"] = raw_answer

    result["model_cited"] = [
        int(marker)
        for marker in CITATION_MARKER.findall(raw_answer)
    ]

    # --------------------------------------------------------
    # Gate 2: the model reported the fact is not in the sources
    # --------------------------------------------------------

    if said_no_answer(raw_answer):
        result["answer"] = REFUSAL_MESSAGE
        result["refused"] = True
        result["refusal_reason"] = "model_reported_not_in_sources"
        return result

    # --------------------------------------------------------
    # Gate 3: attribute the answer to the chunks that support it
    # --------------------------------------------------------

    supporting, all_scores = verify_answer(
        raw_answer,
        chunks,
        min_coverage=min_coverage
    )

    result["grounding_scores"] = all_scores

    if not supporting:
        result["answer"] = REFUSAL_MESSAGE
        result["refused"] = True
        result["refusal_reason"] = "ungrounded_answer"
        return result

    # Renumber citations so markers match the sources actually cited.
    answer = CITATION_MARKER.sub("", raw_answer).strip()
    answer = re.sub(r"\s+([.,;:])", r"\1", answer)

    sources = [
        make_citation(chunk, score, f"[{index + 1}]")
        for index, (chunk, score) in enumerate(supporting)
    ]

    markers = " ".join(source["marker"] for source in sources)

    result["answer"] = f"{answer} {markers}".strip()
    result["sources"] = sources

    return result


def print_answer(result, show_retrieved=False):

    print("\n" + "=" * 72)
    print(f"Q: {result['query']}")
    print("=" * 72)

    if result["filter"]:
        print(f"filter: {result['filter']}")

    if result["best_distance"] is not None:
        print(f"best distance: {result['best_distance']}")

    if show_retrieved:
        print("retrieved:")
        for item in result["retrieved"]:
            print(
                f"   {item['rank']}. {item['chunk_id']} "
                f"({item['distance']})"
            )

    print(f"\nA: {result['answer']}")

    if result["refused"]:
        print(f"\nREFUSED -> {result['refusal_reason']}")
        return

    print("\nCitations:")

    for source in result["sources"]:
        print(
            f"   {source['marker']} "
            f"{source['article_id']} / {source['chunk_id']} "
            f"- {source['source_file']} "
            f"(section: {source['section']}, "
            f"updated {source['last_updated']}, "
            f"coverage {source['coverage']})"
        )


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    demo_queries = [
        "How long is a CloudDesk password reset link valid?",
        "What are the password requirements for a new CloudDesk password?",
        "Why is the Upload File button unavailable in CloudDesk?",
        "What is the maximum file size limit for CloudDesk uploads in megabytes?",
        "What is the capital of France?",
    ]

    for query in demo_queries:
        print_answer(generate_answer(query))

"""Fast checks that need no model download.

    python tests/test_pipeline.py
"""

import json
import os
import re
import sys

ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)

from loader import load_articles
from chunking import STRATEGIES, create_chunks, TABLE_LINE
from grounding import coverage, unsupported_numbers, verify_answer
from metrics import is_gold_chunk, is_table_grounded


REQUIRED_METADATA = {
    "source_file",
    "article_id",
    "product_area",
    "last_updated",
    "chunk_id",
}

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


print("Loader")

documents = load_articles()

check("loads 6 articles", len(documents) == 6, f"got {len(documents)}")

check(
    "every article has the required metadata",
    all(
        REQUIRED_METADATA - {"chunk_id"} <= set(doc["metadata"])
        for doc in documents
    )
)


print("\nChunking")

for strategy in STRATEGIES:

    chunks = create_chunks(strategy, documents, 300, 50)

    check(
        f"{strategy}: produces chunks",
        len(chunks) > 0
    )

    check(
        f"{strategy}: every chunk has the required metadata",
        all(
            REQUIRED_METADATA <= set(chunk["metadata"])
            for chunk in chunks
        )
    )

    check(
        f"{strategy}: chunk_ids are unique",
        len({chunk["metadata"]["chunk_id"] for chunk in chunks})
        == len(chunks)
    )

    check(
        f"{strategy}: no chunk is empty",
        all(chunk["content"].strip() for chunk in chunks)
    )


def stranded_table_rows(chunks):
    """Table data rows sitting in a chunk with no header separator."""

    total = 0

    for chunk in chunks:

        lines = [
            line
            for line in chunk["content"].split("\n")
            if TABLE_LINE.match(line)
        ]

        if not lines:
            continue

        def is_separator(line):
            stripped = line.replace("|", "").strip()
            return stripped and set(stripped) <= {"-", " ", ":"}

        if not any(is_separator(line) for line in lines):
            total += sum(
                1
                for line in lines
                if not is_separator(line)
            )

    return total


markdown_chunks = create_chunks("markdown", documents, 300, 50)

check(
    "markdown strategy strands no table rows",
    stranded_table_rows(markdown_chunks) == 0,
    f"stranded {stranded_table_rows(markdown_chunks)}"
)

check(
    "markdown strategy flags table chunks",
    any(chunk["metadata"].get("is_table") for chunk in markdown_chunks)
)


print("\nGrounding")

chunk = {
    "chunk_id": "HC-001-markdown-15",
    "content": "A password reset link is valid for 30 minutes.",
}

check(
    "exact answer scores full coverage",
    coverage("30 minutes", chunk["content"]) == 1.0
)

check(
    "fabricated number is detected",
    unsupported_numbers("The limit is 25 MB", chunk["content"]) == ["25"]
)

check(
    "supported number is not flagged",
    unsupported_numbers("30 minutes", chunk["content"]) == []
)

check(
    "citation markers are ignored when checking numbers",
    unsupported_numbers("30 minutes [1]", chunk["content"]) == []
)

supporting, _ = verify_answer(
    "The maximum file size is 25 megabytes",
    [dict(chunk, article_id="HC-001", distance=0.1)]
)

check(
    "an answer with a fabricated number is not attributed",
    supporting == []
)


print("\nMetrics")

question = {
    "expected_article_id": "HC-001",
    "answer_keys": ["Reset link has expired"],
    "table_header": "| Problem | Possible Cause | Solution |",
}

with_header = {
    "article_id": "HC-001",
    "content": (
        "| Problem | Possible Cause | Solution |\n"
        "|---|---|---|\n"
        "| Reset link has expired | Link is older than 30 minutes "
        "| Request a new password reset link |"
    ),
}

without_header = {
    "article_id": "HC-001",
    "content": (
        "| Reset link has expired | Link is older than 30 minutes "
        "| Request a new password reset link |"
    ),
}

check(
    "gold chunk is recognised with header",
    is_gold_chunk(with_header, question)
)

check(
    "gold chunk is recognised without header",
    is_gold_chunk(without_header, question)
)

check(
    "table grounding requires the header row",
    is_table_grounded(with_header, question)
    and not is_table_grounded(without_header, question)
)

check(
    "wrong article is never gold",
    not is_gold_chunk(
        dict(with_header, article_id="HC-002"), question
    )
)


print("\nEvaluation data")

with open("evaluation/questions.json", encoding="utf-8") as file:
    questions = json.load(file)

check("8 known-answer questions", len(questions) == 8)

table_questions = [
    question
    for question in questions
    if question["type"] == "table"
]

check(
    "at least 3 table-based questions",
    len(table_questions) >= 3,
    f"got {len(table_questions)}"
)

check(
    "every table question names its header row",
    all(question["table_header"] for question in table_questions)
)

# Every answer key must genuinely exist in its source article.
by_article = {
    doc["metadata"]["article_id"]: doc["content"]
    for doc in documents
}

missing = []

for question in questions:

    article = by_article[question["expected_article_id"]]
    normalized = re.sub(r"\s+", " ", article.lower())

    for key in question["answer_keys"]:
        if re.sub(r"\s+", " ", key.lower()) not in normalized:
            missing.append((question["id"], key))

check(
    "every answer key exists in its expected article",
    not missing,
    str(missing)
)

with open(
    "evaluation/unsupported_questions.json", encoding="utf-8"
) as file:
    unsupported = json.load(file)

check("6 unsupported questions", len(unsupported) == 6)


print()

if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)

print("All checks passed.")

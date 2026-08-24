import re

from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    CharacterTextSplitter,
)

from loader import load_articles


TABLE_LINE = re.compile(r"^\s*\|")


# ============================================================
# Strategy 1: Recursive Chunking
# ============================================================

def create_recursive_chunks(
    documents,
    chunk_size=300,
    chunk_overlap=50
):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=[
            "\n## ",
            "\n### ",
            "\n\n",
            "\n",
            " ",
            ""
        ]
    )

    chunks = []

    for document in documents:

        document_chunks = splitter.split_text(
            document["content"]
        )

        for index, chunk_text in enumerate(document_chunks):

            chunk_metadata = document["metadata"].copy()

            chunk_metadata["chunk_id"] = (
                f"{chunk_metadata['article_id']}"
                f"-recursive-{index + 1}"
            )

            chunk_metadata["strategy"] = "recursive"

            chunks.append(
                {
                    "content": chunk_text,
                    "metadata": chunk_metadata
                }
            )

    return chunks


# ============================================================
# Strategy 2: Fixed-Size Chunking
# ============================================================

def create_fixed_chunks(
    documents,
    chunk_size=300,
    chunk_overlap=50
):
    splitter = CharacterTextSplitter(
        separator="\n",
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )

    chunks = []

    for document in documents:

        document_chunks = splitter.split_text(
            document["content"]
        )

        for index, chunk_text in enumerate(document_chunks):

            chunk_metadata = document["metadata"].copy()

            chunk_metadata["chunk_id"] = (
                f"{chunk_metadata['article_id']}"
                f"-fixed-{index + 1}"
            )

            chunk_metadata["strategy"] = "fixed"

            chunks.append(
                {
                    "content": chunk_text,
                    "metadata": chunk_metadata
                }
            )

    return chunks


# ============================================================
# Strategy 3: Markdown-Aware Chunking (table-safe)
# ============================================================
#
# The first two strategies split troubleshooting tables in the
# middle, which leaves data rows in chunks that no longer carry
# the "| Problem | Cause | Solution |" header row.
#
# This strategy:
#   1. Splits the article on "## " section headings.
#   2. Separates each section into prose blocks and table blocks.
#   3. Chunks tables by row, repeating the header row in every
#      chunk so a retrieved row is always interpretable.
#   4. Prepends "Article Title - Section" to every chunk so the
#      chunk keeps its provenance even when read in isolation.
# ============================================================

def _split_into_sections(content):
    """Split markdown into a title plus (heading, body) sections."""

    title = ""
    sections = []

    current_heading = ""
    current_lines = []

    for line in content.split("\n"):

        if line.startswith("# ") and not title:
            title = line[2:].strip()
            continue

        if line.startswith("## "):

            if current_lines:
                sections.append(
                    (current_heading, "\n".join(current_lines).strip())
                )

            current_heading = line[3:].strip()
            current_lines = []

        else:
            current_lines.append(line)

    if current_lines:
        sections.append(
            (current_heading, "\n".join(current_lines).strip())
        )

    return title, [
        (heading, body)
        for heading, body in sections
        if body
    ]


def _split_body_into_blocks(body):
    """Separate a section body into prose blocks and table blocks."""

    blocks = []
    buffer = []
    mode = "prose"

    for line in body.split("\n"):

        line_mode = (
            "table"
            if TABLE_LINE.match(line)
            else "prose"
        )

        if line_mode != mode and buffer:
            blocks.append((mode, "\n".join(buffer).strip()))
            buffer = []

        mode = line_mode
        buffer.append(line)

    if buffer:
        blocks.append((mode, "\n".join(buffer).strip()))

    return [
        (block_mode, text)
        for block_mode, text in blocks
        if text
    ]


def _chunk_table(table_text, prefix, chunk_size):
    """Chunk a markdown table by row, repeating the header row."""

    lines = [
        line
        for line in table_text.split("\n")
        if line.strip()
    ]

    if len(lines) < 3:
        return [prefix + table_text]

    table_header = f"{lines[0]}\n{lines[1]}"
    rows = lines[2:]

    chunks = []
    current = []

    for row in rows:

        candidate = current + [row]

        candidate_text = (
            prefix
            + table_header
            + "\n"
            + "\n".join(candidate)
        )

        if len(candidate_text) > chunk_size and current:

            chunks.append(
                prefix
                + table_header
                + "\n"
                + "\n".join(current)
            )

            current = [row]

        else:
            current = candidate

    if current:
        chunks.append(
            prefix
            + table_header
            + "\n"
            + "\n".join(current)
        )

    return chunks


def _chunk_prose(prose_text, prefix, chunk_size, chunk_overlap):
    """Chunk prose, leaving room for the section prefix."""

    budget = max(chunk_size - len(prefix), 80)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=budget,
        chunk_overlap=min(chunk_overlap, budget // 2),
        separators=[
            "\n\n",
            "\n",
            ". ",
            " ",
            ""
        ]
    )

    return [
        prefix + part
        for part in splitter.split_text(prose_text)
    ]


def create_markdown_chunks(
    documents,
    chunk_size=300,
    chunk_overlap=50
):
    chunks = []

    for document in documents:

        title, sections = _split_into_sections(
            document["content"]
        )

        index = 0

        for heading, body in sections:

            prefix = (
                f"{title} - {heading}\n"
                if heading
                else f"{title}\n"
            )

            for block_mode, text in _split_body_into_blocks(body):

                if block_mode == "table":

                    block_chunks = _chunk_table(
                        text,
                        prefix,
                        chunk_size
                    )

                else:

                    block_chunks = _chunk_prose(
                        text,
                        prefix,
                        chunk_size,
                        chunk_overlap
                    )

                for chunk_text in block_chunks:

                    index += 1

                    chunk_metadata = document["metadata"].copy()

                    chunk_metadata["chunk_id"] = (
                        f"{chunk_metadata['article_id']}"
                        f"-markdown-{index}"
                    )

                    chunk_metadata["strategy"] = "markdown"
                    chunk_metadata["section"] = heading or title
                    chunk_metadata["is_table"] = (
                        block_mode == "table"
                    )

                    chunks.append(
                        {
                            "content": chunk_text,
                            "metadata": chunk_metadata
                        }
                    )

    return chunks


# ============================================================
# Strategy Registry
# ============================================================

STRATEGIES = {
    "recursive": create_recursive_chunks,
    "fixed": create_fixed_chunks,
    "markdown": create_markdown_chunks,
}


def create_chunks(
    strategy,
    documents,
    chunk_size=300,
    chunk_overlap=50
):
    if strategy not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy: {strategy}. "
            f"Available: {sorted(STRATEGIES)}"
        )

    return STRATEGIES[strategy](
        documents,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    documents = load_articles()

    print(f"Articles loaded: {len(documents)}")

    print("\n" + "=" * 70)
    print("CHUNKING STRATEGY COMPARISON (size=300, overlap=50)")
    print("=" * 70)

    print(
        f"{'Strategy':<14}"
        f"{'Chunks':<10}"
        f"{'Avg size':<12}"
        f"{'Max size':<12}"
        f"{'Table rows w/o header':<24}"
    )

    print("-" * 70)

    for strategy in STRATEGIES:

        chunks = create_chunks(
            strategy,
            documents,
            chunk_size=300,
            chunk_overlap=50
        )

        sizes = [
            len(chunk["content"])
            for chunk in chunks
        ]

        # Count chunks that contain table data rows but no header row.
        orphaned = 0

        for chunk in chunks:

            content = chunk["content"]

            table_lines = [
                line
                for line in content.split("\n")
                if TABLE_LINE.match(line)
            ]

            if not table_lines:
                continue

            has_separator = any(
                set(line.replace("|", "").strip()) <= {"-", " ", ":"}
                and line.replace("|", "").strip()
                for line in table_lines
            )

            data_rows = [
                line
                for line in table_lines
                if not (
                    set(line.replace("|", "").strip()) <= {"-", " ", ":"}
                    and line.replace("|", "").strip()
                )
            ]

            if data_rows and not has_separator:
                orphaned += len(data_rows)

        print(
            f"{strategy:<14}"
            f"{len(chunks):<10}"
            f"{sum(sizes) // len(sizes):<12}"
            f"{max(sizes):<12}"
            f"{orphaned:<24}"
        )

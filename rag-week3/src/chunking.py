import re

from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    CharacterTextSplitter,
)

from loader import load_articles


TABLE_LINE = re.compile(r"^\s*\|")
FENCE_LINE = re.compile(r"^\s*(```|~~~)")


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
#   1. Splits on "## " headings when the document has them, and
#      treats a document without any as one untitled section.
#   2. Separates each section into prose, table and code blocks,
#      so a file mixing all three splits on content boundaries.
#   3. Chunks tables by row, repeating the header row in every
#      chunk so a retrieved row is always interpretable.
#   4. Chunks fenced code on line boundaries, never mid-line.
#   5. Prepends "Title - Section" to every chunk so it keeps its
#      provenance when read in isolation, falling back to the
#      document metadata when the file carries no headings.
#
# No step requires any of these features to be present.
# ============================================================

def _paired_fence_lines(lines):
    """Indices of fence markers that actually open and close a block.

    A lone "```" is common in real documents — someone opened a snippet and
    never closed it. Honouring it would put every following line inside a
    code block and hide the rest of the document's headings, so an unpaired
    trailing fence is treated as ordinary text.
    """
    marks = [i for i, line in enumerate(lines) if FENCE_LINE.match(line)]
    return set(marks[: len(marks) - len(marks) % 2])


def _split_into_sections(content, fallback_title=""):
    """Split text into a title plus (heading, body) sections.

    Headings are used when the document happens to have them and are never
    required. A file with no "#" line at all comes back as a single
    untitled section, which chunks exactly like any other body of prose.
    """

    title = ""
    sections = []

    current_heading = ""
    current_lines = []
    in_fence = False

    lines = content.split("\n")
    fences = _paired_fence_lines(lines)

    for position, line in enumerate(lines):

        # A "## " inside a fenced code block is code, not a heading.
        if position in fences:
            in_fence = not in_fence
            current_lines.append(line)
            continue

        if not in_fence and line.startswith("# ") and not title:
            title = line[2:].strip()
            continue

        if not in_fence and line.startswith("## "):

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

    return title or fallback_title, [
        (heading, body)
        for heading, body in sections
        if body
    ]


def _line_mode(line, in_fence):
    """Classify one line so mixed content splits on type boundaries."""

    if in_fence:
        return "code"
    if TABLE_LINE.match(line):
        return "table"
    return "prose"


def _split_body_into_blocks(body):
    """Separate a section body into prose, table and code blocks.

    A single file may hold all three — pasted notes above an exported
    table above a config snippet — so blocks are cut wherever the kind of
    content changes rather than at a fixed layout.
    """

    blocks = []
    buffer = []
    mode = "prose"
    in_fence = False

    lines = body.split("\n")
    fences = _paired_fence_lines(lines)

    for position, line in enumerate(lines):

        if position in fences:
            # The fence itself belongs to the code block on both sides.
            if not in_fence and buffer:
                blocks.append((mode, "\n".join(buffer).strip()))
                buffer = []
            in_fence = not in_fence
            buffer.append(line)
            if not in_fence:
                blocks.append(("code", "\n".join(buffer).strip()))
                buffer = []
                mode = "prose"
            else:
                mode = "code"
            continue

        line_mode = _line_mode(line, in_fence)

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


def _chunk_code(code_text, prefix, chunk_size):
    """Chunk a fenced block on line boundaries, never mid-line."""

    budget = max(chunk_size - len(prefix), 80)

    chunks = []
    current = []
    length = 0

    for line in code_text.split("\n"):
        if current and length + len(line) + 1 > budget:
            chunks.append(prefix + "\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1

    if current:
        chunks.append(prefix + "\n".join(current))

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

        metadata = document["metadata"]

        # Every chunk needs a name to carry, and a document with no "#"
        # heading still has one in its metadata. Without this fallback an
        # unstructured upload produced chunks prefixed with a bare newline,
        # losing the provenance that makes a chunk readable on its own.
        fallback_title = (
            metadata.get("title")
            or metadata.get("article_id")
            or metadata.get("source_file")
            or ""
        )

        title, sections = _split_into_sections(
            document["content"],
            fallback_title=fallback_title
        )

        index = 0

        for heading, body in sections:

            label = " - ".join(p for p in (title, heading) if p)
            prefix = f"{label}\n" if label else ""

            for block_mode, text in _split_body_into_blocks(body):

                if block_mode == "table":

                    block_chunks = _chunk_table(
                        text,
                        prefix,
                        chunk_size
                    )

                elif block_mode == "code":

                    block_chunks = _chunk_code(
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

                    # When a document has no heading its title comes from
                    # metadata, which can repeat text the chunk already
                    # holds. Prefixing then stores the same sentence twice.
                    body_text = chunk_text[len(prefix):]
                    if prefix and body_text.lstrip().startswith(label):
                        chunk_text = body_text

                    index += 1

                    chunk_metadata = metadata.copy()

                    chunk_metadata["chunk_id"] = (
                        f"{chunk_metadata['article_id']}"
                        f"-markdown-{index}"
                    )

                    chunk_metadata["strategy"] = "markdown"
                    chunk_metadata["section"] = (
                        heading or title or fallback_title or "Document"
                    )
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

from pathlib import Path
import yaml


DATA_DIR = Path("data")


def load_articles():
    documents = []

    for file_path in sorted(DATA_DIR.glob("*.md")):
        content = file_path.read_text(encoding="utf-8")

        parts = content.split("---", 2)

        if len(parts) != 3:
            raise ValueError(
                f"Invalid frontmatter format in {file_path}"
            )

        metadata_text = parts[1]
        article_content = parts[2].strip()

        metadata = yaml.safe_load(metadata_text)

        metadata["source_file"] = file_path.name

        documents.append(
            {
                "content": article_content,
                "metadata": metadata,
            }
        )

    return documents


if __name__ == "__main__":
    documents = load_articles()

    print(f"Loaded {len(documents)} articles\n")

    for document in documents:
        print("=" * 60)
        print("Metadata:")
        print(document["metadata"])
        print("\nContent preview:")
        print(document["content"][:300])
        print()
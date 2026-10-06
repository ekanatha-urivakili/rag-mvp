from rag.ingestion.chunker import chunk_pages, count_tokens
from rag.ingestion.parsers import Page


def test_heading_paths_and_pages() -> None:
    md1 = "# Guide\n\nIntro text.\n\n## Install\n\n### Linux\n\nRun apt install foo."
    md2 = "### macOS\n\nRun brew install foo."
    chunks = chunk_pages([Page(1, md1), Page(2, md2)], size=200, overlap=20)
    paths = [c.heading_path for c in chunks]
    assert paths == ["Guide", "Guide > Install > Linux", "Guide > Install > macOS"]
    assert chunks[2].page_start == 2
    assert chunks[1].embed_text.startswith("Guide > Install > Linux\n\n")


def test_respects_token_budget() -> None:
    paragraphs = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(30))
    chunks = chunk_pages([Page(None, f"# Big\n\n{paragraphs}")], size=200, overlap=80)
    assert len(chunks) > 5
    assert all(c.token_count <= 200 + 5 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_oversized_paragraph_is_hard_split() -> None:
    chunks = chunk_pages([Page(None, "word " * 2000)], size=300, overlap=30)
    assert len(chunks) >= 7 and all(count_tokens(c.text) <= 301 for c in chunks)

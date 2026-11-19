"""FR-KB-01: indexing is repeatable, cheap when nothing changed, and never empties the index."""

from pathlib import Path

import pytest
from sqlalchemy import Engine

from lineageheal.core.errors import KnowledgeError
from lineageheal.rag.embeddings import HashingEmbedder
from lineageheal.rag.indexer import RunbookIndexer
from lineageheal.store.knowledge import IndexOutcome, KnowledgeRepository

RUNBOOK = """# {title}

**Fault class:** nulls · **Feeds:** AMFI

## Symptoms
{body}

## Fix
Read the raw file.
"""


class CountingEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        self.documents_embedded = 0

    def embed_documents(self, texts: "list[str]") -> list[list[float]]:  # type: ignore[override]
        self.documents_embedded += len(texts)
        return super().embed_documents(texts)


def write(folder: Path, slug: str, body: str = "A test fails.") -> None:
    (folder / f"{slug}.md").write_text(RUNBOOK.format(title=slug.title(), body=body))


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    runbooks = tmp_path / "runbooks"
    runbooks.mkdir()
    write(runbooks, "nulls")
    write(runbooks, "duplicates")
    return runbooks


@pytest.fixture
def embedder() -> CountingEmbedder:
    return CountingEmbedder()


@pytest.fixture
def indexer(engine: Engine, embedder: CountingEmbedder) -> RunbookIndexer:
    return RunbookIndexer(KnowledgeRepository(engine), embedder)


def test_every_runbook_is_added_with_a_chunk_per_section(
    indexer: RunbookIndexer, engine: Engine, folder: Path
) -> None:
    report = indexer.index(folder)

    assert report.outcomes == {"duplicates": IndexOutcome.ADDED, "nulls": IndexOutcome.ADDED}
    assert KnowledgeRepository(engine).counts("live") == (2, 4)


def test_running_again_skips_unchanged_runbooks_without_embedding_them(
    indexer: RunbookIndexer, embedder: CountingEmbedder, folder: Path
) -> None:
    indexer.index(folder)
    embedded = embedder.documents_embedded

    report = indexer.index(folder)

    assert set(report.outcomes.values()) == {IndexOutcome.UNCHANGED}
    assert embedder.documents_embedded == embedded


def test_an_edited_runbook_is_replaced_and_the_rest_left_alone(
    indexer: RunbookIndexer, folder: Path
) -> None:
    indexer.index(folder)
    write(folder, "nulls", body="A different symptom.")

    report = indexer.index(folder)

    assert report.outcomes == {
        "duplicates": IndexOutcome.UNCHANGED,
        "nulls": IndexOutcome.UPDATED,
    }


def test_a_deleted_runbook_leaves_the_index(
    indexer: RunbookIndexer, engine: Engine, folder: Path
) -> None:
    indexer.index(folder)
    (folder / "duplicates.md").unlink()

    report = indexer.index(folder)

    assert report.removed == ("duplicates",)
    assert KnowledgeRepository(engine).counts("live") == (1, 2)


def test_one_bad_runbook_stops_the_run_before_anything_is_removed(
    indexer: RunbookIndexer, engine: Engine, folder: Path
) -> None:
    indexer.index(folder)
    (folder / "nulls.md").write_text("not a runbook")

    with pytest.raises(KnowledgeError, match="nulls"):
        indexer.index(folder)

    assert KnowledgeRepository(engine).counts("live") == (2, 4)


def test_an_empty_folder_is_an_error_not_an_instruction_to_delete_everything(
    indexer: RunbookIndexer, engine: Engine, folder: Path, tmp_path: Path
) -> None:
    indexer.index(folder)
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(KnowledgeError, match="No runbooks"):
        indexer.index(empty)

    assert KnowledgeRepository(engine).counts("live") == (2, 4)


def test_a_new_embedding_model_re_embeds_every_runbook(engine: Engine, folder: Path) -> None:
    RunbookIndexer(KnowledgeRepository(engine), HashingEmbedder()).index(folder)

    class NewModel(HashingEmbedder):
        model_name = "another-model"

    report = RunbookIndexer(KnowledgeRepository(engine), NewModel()).index(folder)

    assert set(report.outcomes.values()) == {IndexOutcome.UPDATED}

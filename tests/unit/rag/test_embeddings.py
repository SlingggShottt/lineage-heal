"""D-06: embeddings come from a local model behind a small interface."""

import math
from pathlib import Path

import pytest

from lineageheal.core.errors import KnowledgeError
from lineageheal.core.settings import Settings
from lineageheal.rag import embeddings
from lineageheal.rag.embeddings import FastEmbedder, HashingEmbedder, build_embedder
from lineageheal.store.types import EMBEDDING_DIMENSIONS


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_the_test_embedder_is_deterministic_normalised_and_the_right_size() -> None:
    embedder = HashingEmbedder()

    [first] = embedder.embed_documents(["a blank NAV value"])
    [again] = embedder.embed_documents(["a blank NAV value"])

    assert first == again
    assert len(first) == EMBEDDING_DIMENSIONS
    assert math.isclose(cosine(first, first), 1.0)


def test_texts_that_share_words_are_closer_than_texts_that_do_not() -> None:
    embedder = HashingEmbedder()
    query = embedder.embed_query("nav is blank")
    related, unrelated = embedder.embed_documents(["the nav arrives blank", "file never arrived"])

    assert cosine(query, related) > cosine(query, unrelated)


def test_the_embedder_follows_the_settings_and_loads_nothing_yet(tmp_path: Path) -> None:
    settings = Settings(embedding_model="some/model", embedding_cache_dir=tmp_path)

    embedder = build_embedder(settings)

    assert isinstance(embedder, FastEmbedder)
    assert embedder.model_name == "some/model"


def test_a_model_with_the_wrong_vector_size_is_refused_naming_the_setting() -> None:
    with pytest.raises(KnowledgeError, match="EMBEDDING_MODEL"):
        embeddings._checked([[0.0] * 768], "big/model")


def test_a_model_that_cannot_be_loaded_is_a_project_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fastembed

    def refuse(**_: object) -> None:
        raise ValueError("Model nope is not supported")

    monkeypatch.setattr(fastembed, "TextEmbedding", refuse)

    with pytest.raises(KnowledgeError, match="Could not load the embedding model 'nope'"):
        FastEmbedder("nope", tmp_path).embed_query("anything")

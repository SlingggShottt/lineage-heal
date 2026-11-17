"""FR-LIN-04, FR-LIN-05: lineage graphs are stored by manifest version and read back unchanged."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.lineage.graph import EdgeKind, LineageEdge, LineageGraph, LineageNode, NodeKind
from lineageheal.store.database import create_db_engine
from lineageheal.store.lineage import KEPT_VERSIONS, LineageRepository
from lineageheal.store.models import LineageEdge as EdgeRow
from lineageheal.store.models import LineageNode as NodeRow

START = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)

RAW_NAV = LineageNode("raw.amfi.navall", "nav", NodeKind.RAW)
BRONZE_NAV = LineageNode("bronze.amfi_nav", "nav", NodeKind.MODEL)
BRONZE_ROWS = LineageNode("bronze.amfi_nav", "*", NodeKind.MODEL)
RAW_ROWS = LineageNode("raw.amfi.navall", "*", NodeKind.RAW)
SEED_DAY = LineageNode("seed_calendar", "day", NodeKind.SEED)


def graph(version: str, *, with_guess: bool = False) -> LineageGraph:
    nodes = [RAW_NAV, BRONZE_NAV, BRONZE_ROWS, RAW_ROWS, SEED_DAY]
    edges = [
        LineageEdge(RAW_NAV.id, BRONZE_NAV.id, EdgeKind.TRANSFORM),
        LineageEdge(RAW_NAV.id, BRONZE_ROWS.id, EdgeKind.FILTER),
    ]
    if with_guess:
        edges.append(LineageEdge(RAW_ROWS.id, BRONZE_ROWS.id, EdgeKind.DIRECT, inferred=True))
    return LineageGraph(version, tuple(sorted(nodes)), tuple(sorted(edges)))


@pytest.fixture
def repository(engine: Engine) -> LineageRepository:
    return LineageRepository(engine)


def count(engine: Engine, model: type[NodeRow] | type[EdgeRow]) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_a_saved_graph_loads_back_equal(repository: LineageRepository) -> None:
    original = graph("aaaa", with_guess=True)

    assert repository.save(original, activated_at=START) is True

    assert repository.load("aaaa") == original
    assert repository.load_current() == original


def test_saving_the_same_version_again_changes_nothing(
    repository: LineageRepository, engine: Engine
) -> None:
    repository.save(graph("aaaa"), activated_at=START)
    nodes, edges = count(engine, NodeRow), count(engine, EdgeRow)

    assert repository.save(graph("aaaa"), activated_at=START + timedelta(hours=1)) is False

    assert (count(engine, NodeRow), count(engine, EdgeRow)) == (nodes, edges)
    assert repository.versions() == ["aaaa"]


def test_a_new_version_becomes_current_and_the_old_one_is_kept(
    repository: LineageRepository,
) -> None:
    repository.save(graph("aaaa"), activated_at=START)
    repository.save(graph("bbbb", with_guess=True), activated_at=START + timedelta(hours=1))

    assert repository.versions() == ["bbbb", "aaaa"]
    assert repository.load_current() == graph("bbbb", with_guess=True)
    assert repository.load("aaaa") == graph("aaaa")


def test_only_the_newest_versions_are_kept_and_the_rest_leave_no_rows(
    repository: LineageRepository, engine: Engine
) -> None:
    assert KEPT_VERSIONS == 2
    for hours, version in enumerate(["aaaa", "bbbb", "cccc"]):
        repository.save(graph(version), activated_at=START + timedelta(hours=hours))

    assert repository.versions() == ["cccc", "bbbb"]
    assert repository.load("aaaa") is None
    graph_size = len(graph("aaaa").nodes), len(graph("aaaa").edges)
    assert (count(engine, NodeRow), count(engine, EdgeRow)) == (
        2 * graph_size[0],
        2 * graph_size[1],
    )


def test_saving_an_older_version_again_makes_it_current_without_pruning(
    repository: LineageRepository,
) -> None:
    repository.save(graph("aaaa"), activated_at=START)
    repository.save(graph("bbbb"), activated_at=START + timedelta(hours=1))

    assert repository.save(graph("aaaa"), activated_at=START + timedelta(hours=2)) is False

    assert repository.versions() == ["aaaa", "bbbb"]
    assert repository.load_current() == graph("aaaa")


def test_saving_the_current_version_again_does_not_move_it(repository: LineageRepository) -> None:
    repository.save(graph("aaaa"), activated_at=START)
    repository.save(graph("bbbb"), activated_at=START + timedelta(hours=1))

    repository.save(graph("bbbb"), activated_at=START + timedelta(hours=5))
    # If the re-save had moved "bbbb" forward, this older save would now be current instead.
    repository.save(graph("aaaa"), activated_at=START + timedelta(hours=2))

    assert repository.versions() == ["aaaa", "bbbb"]


def test_an_empty_database_has_no_graph(repository: LineageRepository) -> None:
    assert repository.versions() == []
    assert repository.load_current() is None
    assert repository.load("missing") is None


def test_a_graph_with_no_edges_is_stored(repository: LineageRepository) -> None:
    only_nodes = LineageGraph("cccc", (RAW_NAV,), ())

    repository.save(only_nodes, activated_at=START)

    assert repository.load("cccc") == only_nodes


def test_an_unmigrated_database_is_a_storage_error(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite+pysqlite:///{tmp_path / 'blank.db'}")
    repository = LineageRepository(engine)

    with pytest.raises(StorageError, match="Could not store the lineage graph"):
        repository.save(graph("aaaa"), activated_at=START)
    with pytest.raises(StorageError):
        repository.load_current()
    with pytest.raises(StorageError):
        repository.versions()
    engine.dispose()


def insert_edge(engine: Engine, **overrides: object) -> None:
    values: dict[str, object] = {
        "version": "aaaa",
        "source": RAW_NAV.id,
        "target": BRONZE_NAV.id,
        "kind": "direct",
        "inferred": False,
    } | overrides
    with Session(engine) as session, session.begin():
        session.add(EdgeRow(**values))


def test_the_database_rejects_an_unknown_edge_kind(
    repository: LineageRepository, engine: Engine
) -> None:
    repository.save(graph("aaaa"), activated_at=START)

    with pytest.raises(IntegrityError):
        insert_edge(engine, kind="magic")


def test_the_database_rejects_a_guessed_edge_between_columns(
    repository: LineageRepository, engine: Engine
) -> None:
    repository.save(graph("aaaa"), activated_at=START)

    with pytest.raises(IntegrityError):
        insert_edge(engine, kind="transform", inferred=True)


def test_the_database_rejects_an_edge_to_a_node_of_another_version(
    repository: LineageRepository, engine: Engine
) -> None:
    repository.save(graph("aaaa"), activated_at=START)
    repository.save(LineageGraph("bbbb", (RAW_NAV,), ()), activated_at=START + timedelta(hours=1))

    with pytest.raises(IntegrityError):
        insert_edge(engine, version="bbbb", target=BRONZE_NAV.id)

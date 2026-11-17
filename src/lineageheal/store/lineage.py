"""Stores built lineage graphs, so the agents and the dashboard read the graph an incident saw.

LH-039 builds the graph in memory from the dbt manifest; this module keeps it, keyed by the
manifest hash (FR-LIN-04). The current and the previous version are kept, and so is any older
version an incident was diagnosed against; the rest are deleted when a new version arrives.
"""

from datetime import UTC, datetime

from sqlalchemy import Engine, delete, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lineageheal.core.errors import StorageError
from lineageheal.lineage.graph import EdgeKind, LineageEdge, LineageGraph, LineageNode, NodeKind
from lineageheal.store.database import explain_database_error
from lineageheal.store.models import Incident, LineageVersion
from lineageheal.store.models import LineageEdge as EdgeRow
from lineageheal.store.models import LineageNode as NodeRow

# The current version and the one before it, so a change can be compared with what it replaced.
KEPT_VERSIONS = 2


class LineageRepository:
    """Saves lineage graphs and loads them back."""

    def __init__(self, engine: Engine) -> None:
        """Create a repository over a database engine."""
        self._engine = engine

    def save(self, graph: LineageGraph, *, activated_at: datetime | None = None) -> bool:
        """Store a graph and make it the current version.

        Saving is idempotent. A version that is already stored is not written again: if it is
        the current one nothing changes, and if it is an older one (a model change was reverted)
        it becomes current again.

        Args:
            graph: A graph built from a manifest.
            activated_at: When the version becomes current; defaults to now.

        Returns:
            True if the version was new, False if it was already stored.

        Raises:
            StorageError: The database could not be reached or rejected the graph.
        """
        moment = activated_at or datetime.now(UTC)
        try:
            with Session(self._engine) as session, session.begin():
                existing = session.get(LineageVersion, graph.version)
                if existing is not None:
                    if self._current_version(session) != graph.version:
                        existing.activated_at = moment
                    return False

                session.add(
                    LineageVersion(
                        version=graph.version,
                        activated_at=moment,
                        node_count=len(graph.nodes),
                        edge_count=len(graph.edges),
                    )
                )
                # The version must exist before its nodes, and the nodes before the edges that
                # point at them.
                session.flush()
                if graph.nodes:
                    session.execute(
                        insert(NodeRow),
                        [
                            {
                                "version": graph.version,
                                "node_id": node.id,
                                "table_name": node.table,
                                "column_name": node.column,
                                "kind": node.kind.value,
                            }
                            for node in graph.nodes
                        ],
                    )
                if graph.edges:
                    session.execute(
                        insert(EdgeRow),
                        [
                            {
                                "version": graph.version,
                                "source": edge.source,
                                "target": edge.target,
                                "kind": edge.kind.value,
                                "inferred": edge.inferred,
                            }
                            for edge in graph.edges
                        ],
                    )
                self._prune(session)
                return True
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not store the lineage graph: {explain_database_error(exc)}"
            ) from exc

    def load(self, version: str) -> LineageGraph | None:
        """Return a stored graph, or None if that version is not stored.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                return self._load(session, version)
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not read the lineage graph: {explain_database_error(exc)}"
            ) from exc

    def load_current(self) -> LineageGraph | None:
        """Return the most recently activated graph, or None if none is stored.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                version = self._current_version(session)
                return None if version is None else self._load(session, version)
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not read the lineage graph: {explain_database_error(exc)}"
            ) from exc

    def versions(self) -> list[str]:
        """Return the stored versions, the current one first.

        Raises:
            StorageError: The database could not be read.
        """
        try:
            with Session(self._engine) as session:
                return _versions(session)
        except SQLAlchemyError as exc:
            raise StorageError(
                f"Could not list the lineage versions: {explain_database_error(exc)}"
            ) from exc

    @staticmethod
    def _current_version(session: Session) -> str | None:
        newest = _versions(session, limit=1)
        return newest[0] if newest else None

    @staticmethod
    def _prune(session: Session) -> None:
        # A version an incident was diagnosed against stays for as long as the incident does;
        # the foreign key from the incident would refuse to delete it in any case.
        referenced = set(
            session.scalars(
                select(Incident.lineage_version).where(Incident.lineage_version.is_not(None))
            )
        )
        stale = [v for v in _versions(session, offset=KEPT_VERSIONS) if v not in referenced]
        if stale:
            # The foreign keys cascade, so a version's nodes and edges go with it.
            session.execute(delete(LineageVersion).where(LineageVersion.version.in_(stale)))

    @staticmethod
    def _load(session: Session, version: str) -> LineageGraph | None:
        if session.get(LineageVersion, version) is None:
            return None
        nodes = [
            LineageNode(row.table_name, row.column_name, NodeKind(row.kind))
            for row in session.scalars(select(NodeRow).where(NodeRow.version == version))
        ]
        edges = [
            LineageEdge(row.source, row.target, EdgeKind(row.kind), row.inferred)
            for row in session.scalars(select(EdgeRow).where(EdgeRow.version == version))
        ]
        # Both are ordered dataclasses, and the builder sorts them the same way, so a loaded
        # graph equals the one that was saved.
        return LineageGraph(version, tuple(sorted(nodes)), tuple(sorted(edges)))


def _versions(session: Session, *, offset: int = 0, limit: int | None = None) -> list[str]:
    # The version breaks a tie between two activations in the same instant, so the order never
    # depends on how the database happens to return rows.
    query = (
        select(LineageVersion.version)
        .order_by(LineageVersion.activated_at.desc(), LineageVersion.version.desc())
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))

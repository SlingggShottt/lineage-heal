"""The HTTP API behind the dashboard (FR-API-01).

It serves reads and records human decisions; heavy work (healing, benchmarks) runs in GitHub
Actions, not here, because the free host is small and sleeps when idle. This package may import
from `core`, `store`, `lineage`, `rag`, `vcs` and `agents`, but never from `orchestration`.
"""

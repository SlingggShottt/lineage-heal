"""Exception hierarchy for LineageHeal.

Every project exception inherits from `LineageHealError`, so callers such as the CLI
can catch one type to report a clean failure while letting genuine bugs surface.
Subclasses are grouped by concern and added as the packages that raise them are built.
"""


class LineageHealError(Exception):
    """Base class for all errors raised deliberately by LineageHeal."""


class ConfigurationError(LineageHealError):
    """A required setting is missing or invalid."""


class IngestionError(LineageHealError):
    """A feed could not be loaded into the raw zone."""


class SourceUnavailableError(IngestionError):
    """A source file could not be downloaded after retries."""


class StorageError(LineageHealError):
    """The raw-zone object store rejected or failed an operation."""


class LineageError(LineageHealError):
    """The lineage graph could not be built or queried."""


class AgentError(LineageHealError):
    """An agent step failed in a way the workflow cannot recover from."""

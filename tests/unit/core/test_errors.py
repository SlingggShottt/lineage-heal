import pytest

from lineageheal.core import errors


@pytest.mark.parametrize(
    "error_type",
    [
        errors.ConfigurationError,
        errors.IngestionError,
        errors.SourceUnavailableError,
        errors.StorageError,
        errors.LineageError,
        errors.AgentError,
    ],
)
def test_every_project_error_can_be_caught_as_lineageheal_error(
    error_type: type[errors.LineageHealError],
) -> None:
    with pytest.raises(errors.LineageHealError, match="boom"):
        raise error_type("boom")


def test_source_unavailable_is_an_ingestion_error() -> None:
    assert issubclass(errors.SourceUnavailableError, errors.IngestionError)

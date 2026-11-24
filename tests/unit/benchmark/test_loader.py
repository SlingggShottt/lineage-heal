"""FR-BEN-01: specifications are read from YAML and errors name the file and the field."""

from pathlib import Path

import pytest

from lineageheal.benchmark.loader import load_case, load_cases
from lineageheal.core.errors import BenchmarkError

SPECS = Path(__file__).parents[2] / "fixtures" / "benchmark"
VALID = (SPECS / "amfi-nulls-001.yaml").read_text(encoding="utf-8")


def test_the_example_specifications_load_ordered_by_id() -> None:
    cases = load_cases(SPECS)

    assert [c.id for c in cases] == sorted(c.id for c in cases)
    assert len(cases) == 11
    assert {c.split for c in cases} == {"seed", "test"}


def test_one_file_loads_with_its_fields() -> None:
    case = load_case(SPECS / "amfi-unit-change-001.yaml")

    assert case.fault_class == "unit_change"
    assert case.injection.type == "scale_values"
    assert case.expected.root_cause == "raw.amfi.navall.Net Asset Value"


def test_an_invalid_file_is_refused_naming_the_file_and_the_field(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text(VALID.replace("seed: 2", "seed: -3"), encoding="utf-8")

    with pytest.raises(BenchmarkError, match=r"bad\.yaml: seed: .*greater than or equal to 0"):
        load_case(tmp_path / "bad.yaml")


def test_a_file_that_is_not_a_mapping_or_not_yaml_is_refused(tmp_path: Path) -> None:
    (tmp_path / "list.yaml").write_text("- a\n- b\n", encoding="utf-8")
    (tmp_path / "broken.yaml").write_text("id: [unclosed", encoding="utf-8")

    with pytest.raises(BenchmarkError, match="a specification is a YAML mapping"):
        load_case(tmp_path / "list.yaml")
    with pytest.raises(BenchmarkError, match="cannot read the specification"):
        load_case(tmp_path / "broken.yaml")


def test_two_files_with_one_id_are_refused_because_results_are_stored_per_id(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.yaml").write_text(VALID, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(VALID, encoding="utf-8")

    with pytest.raises(
        BenchmarkError, match=r"b\.yaml: id 'amfi-nulls-001' is already used by a\.yaml"
    ):
        load_cases(tmp_path)


def test_a_folder_with_no_specifications_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkError, match="No fault specifications"):
        load_cases(tmp_path)

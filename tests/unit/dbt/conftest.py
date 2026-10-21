"""Shared machinery for the dbt scenario tests.

Each test stores files in a temporary raw zone, runs `dbt build` for some models against a
temporary DuckDB file, and inspects which models and tests passed. Test modules import the
`Workspace` type only for annotations, so they do not need to import this module.
"""

import csv
import gzip
import json
import os
import shutil
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml
from dbt.cli.main import dbtRunner

from lineageheal.ingestion.object_stores import LocalObjectStore
from lineageheal.ingestion.raw_zone import RawZone, StoredObject
from lineageheal.ingestion.staging import ZipStager

REPO_ROOT = Path(__file__).parents[3]
DBT_DIR = REPO_ROOT / "dbt"

# Where each dataset's files are stored unless a test says otherwise.
AMFI_DATE = date(2026, 10, 15)
BHAVCOPY_DATE = date(2024, 7, 5)
REFERENCE_DATE = date(2026, 10, 16)

NSE_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "nse"
BHAVCOPY_SAMPLE = (NSE_FIXTURES / "cm_bhavcopy_udiff_2024-07-05_sample.csv").read_bytes()
ACTION_TEMPLATE = json.loads((NSE_FIXTURES / "corporate_actions_sample.json").read_bytes())[1]

# (symbol, series, close, previous close), all in rupees
Price = tuple[str, str, str, str]


def bhavcopy(day: date, prices: list[Price]) -> bytes:
    """A bhavcopy for one day: the sample's real layout, with the given instruments.

    Each row is a copy of a real one, so every column is present and typed realistically; open,
    high and low equal the close, which keeps the intraday-range check satisfied.
    """
    reader = csv.DictReader(BHAVCOPY_SAMPLE.decode("utf-8").splitlines())
    fieldnames = list(reader.fieldnames or [])
    template = next(row for row in reader if row["TckrSymb"] == "TCS")
    rows = []
    for symbol, series, close, previous_close in prices:
        identity = zlib.crc32(f"{symbol}{series}".encode())
        rows.append(
            {
                **template,
                "TradDt": day.isoformat(),
                "BizDt": day.isoformat(),
                "FinInstrmId": str(identity),
                "ISIN": f"INE{identity:09d}",
                "TckrSymb": symbol,
                "SctySrs": series,
                "FinInstrmNm": symbol,
                "OpnPric": close,
                "HghPric": close,
                "LwPric": close,
                "ClsPric": close,
                "LastPric": close,
                "PrvsClsgPric": previous_close,
                "SttlmPric": close,
            }
        )
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def amfi_navs(day: date, navs: dict[int, str]) -> bytes:
    """An AMFI report for one day with the given NAV text by scheme code."""
    header = (
        "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
        "Net Asset Value;Date"
    )
    lines = [header, "", "Open Ended Schemes ( Equity Scheme - Large Cap Fund )", ""]
    lines += [
        f"{code};Fund {code};Direct Plan;Growth Option;INF{code:06d}01;;{nav};{day:%d-%b-%Y}"
        for code, nav in navs.items()
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


@dataclass
class BuildResult:
    success: bool
    statuses: dict[str, str]  # node name -> pass, fail, success, error or skipped
    database: Path

    def query(self, sql: str, *parameters: object) -> list[tuple[object, ...]]:
        # dbt keeps its own connection to the file open in this process, and DuckDB only
        # allows a second one with identical settings, so this cannot be read-only.
        connection = duckdb.connect(str(self.database))
        try:
            return connection.execute(sql, list(parameters)).fetchall()
        finally:
            connection.close()

    def failed(self) -> set[str]:
        return {name for name, status in self.statuses.items() if status in ("fail", "error")}


@dataclass
class Workspace:
    raw_dir: Path
    zone: RawZone
    stager: ZipStager
    # Builds the named models and their tests; `variables` are passed to dbt as --vars.
    build: Callable[..., BuildResult]

    def _arrive(self, stored: StoredObject, arrived_at: float | None) -> StoredObject:
        """Set when a stored file arrived, so tests control which version is the latest."""
        if arrived_at is not None:
            os.utime(self.raw_dir / stored.key, (arrived_at, arrived_at))
        return stored

    def store_amfi(
        self, content: bytes, *, trade_date: date = AMFI_DATE, arrived_at: float | None = None
    ) -> StoredObject:
        stored = self.zone.put_if_absent(
            source="amfi",
            dataset="navall",
            trade_date=trade_date,
            filename=f"NAVHistoryReport{trade_date:%d%m%y}.txt",
            content=content,
        )
        return self._arrive(stored, arrived_at)

    def store_nse(
        self,
        csv_content: bytes,
        *,
        trade_date: date = BHAVCOPY_DATE,
        arrived_at: float | None = None,
    ) -> StoredObject:
        """Store a bhavcopy zip in the raw zone and stage it, as the loader does."""
        name = f"BhavCopy_NSE_CM_0_0_0_{trade_date:%Y%m%d}_F_0000.csv"
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, csv_content)
        stored = self.zone.put_if_absent(
            source="nse",
            dataset="cm_bhavcopy",
            trade_date=trade_date,
            filename=f"{name}.zip",
            content=buffer.getvalue(),
        )
        self.stager.stage(stored.key)
        return self._arrive(stored, arrived_at)

    def store_prices(self, days: dict[date, list[Price]]) -> None:
        """Store a bhavcopy for each day, built from (symbol, series, close, previous close)."""
        for day, prices in days.items():
            self.store_nse(bhavcopy(day, prices), trade_date=day)

    def store_navs(self, days: dict[date, dict[int, str]]) -> None:
        """Store an AMFI report for each day, built from NAV text by scheme code."""
        for day, navs in days.items():
            self.store_amfi(amfi_navs(day, navs), trade_date=day)

    def store_actions(self, ex_date: date, *announcements: tuple[str, str, str]) -> None:
        """Store the corporate actions announced for one ex-date: (symbol, series, subject)."""
        records = [
            {
                **ACTION_TEMPLATE,
                "symbol": symbol,
                "series": series,
                "subject": subject,
                "exDate": f"{ex_date:%d-%b-%Y}",
                "recDate": f"{ex_date:%d-%b-%Y}",
            }
            for symbol, series, subject in announcements
        ]
        self.store_corporate_actions(json.dumps(records).encode("utf-8"), ex_date=ex_date)

    def store_security_master(
        self, csv_content: bytes, *, arrived_at: float | None = None
    ) -> StoredObject:
        stored = self.zone.put_if_absent(
            source="nse",
            dataset="security_master",
            trade_date=REFERENCE_DATE,
            filename="NSE_CM_security_16102026.csv.gz",
            content=gzip.compress(csv_content),
        )
        return self._arrive(stored, arrived_at)

    def store_corporate_actions(
        self,
        json_content: bytes,
        *,
        ex_date: date = REFERENCE_DATE,
        arrived_at: float | None = None,
    ) -> StoredObject:
        stored = self.zone.put_if_absent(
            source="nse",
            dataset="corporate_actions",
            trade_date=ex_date,
            filename=f"corporate_actions_{ex_date:%Y%m%d}.json",
            content=json_content,
        )
        return self._arrive(stored, arrived_at)


def write_profile(profiles_dir: Path, database: Path) -> None:
    """A dbt profile that builds into one DuckDB file. Its file name sets dbt's database name."""
    profiles_dir.mkdir(parents=True, exist_ok=True)
    profile = {
        "lineageheal": {
            "target": "ci",
            "outputs": {"ci": {"type": "duckdb", "path": str(database), "threads": 1}},
        }
    }
    (profiles_dir / "profiles.yml").write_text(yaml.safe_dump(profile))


@dataclass(frozen=True)
class ParsedProject:
    """The dbt project parsed once, and the raw-zone folder its sources were parsed against."""

    raw_dir: Path
    manifest: Any  # dbt's Manifest


@pytest.fixture(scope="session")
def parsed_project(tmp_path_factory: pytest.TempPathFactory) -> ParsedProject:
    """Parse the dbt project once for the whole test session.

    Parsing takes about as long as a small build, and it would otherwise happen in every test.
    Source locations are fixed when the project is parsed (they come from RAW_ZONE_DIR), so every
    test uses this one raw-zone folder, emptied before each test. Each test still gets its own
    database file; those must all be named test.duckdb, because dbt takes its database name
    from the file name and the parsed project already contains it.
    """
    root = tmp_path_factory.mktemp("dbt-parsed")
    raw_dir = root / "raw_zone"
    raw_dir.mkdir()
    (root / "db").mkdir()
    write_profile(root / "profiles", root / "db" / "test.duckdb")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("RAW_ZONE_DIR", str(raw_dir))
        result = dbtRunner().invoke(
            [
                "parse",
                "--project-dir", str(DBT_DIR),
                "--profiles-dir", str(root / "profiles"),
                "--target-path", str(root / "target"),
                "--log-path", str(root / "logs"),
                "--no-version-check",
                "--quiet",
            ]
        )  # fmt: skip
    assert result.success, result.exception
    return ParsedProject(raw_dir, result.result)


@pytest.fixture
def workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parsed_project: ParsedProject
) -> Workspace:
    raw_dir = parsed_project.raw_dir
    shutil.rmtree(raw_dir)  # nothing from an earlier test may remain
    raw_dir.mkdir()
    monkeypatch.setenv("RAW_ZONE_DIR", str(raw_dir))
    monkeypatch.chdir(REPO_ROOT)

    profiles_dir = tmp_path / "profiles"
    database = tmp_path / "test.duckdb"  # the name must stay test.duckdb, see parsed_project
    write_profile(profiles_dir, database)

    def build(*models: str, variables: dict[str, str] | None = None) -> BuildResult:
        result = dbtRunner(manifest=parsed_project.manifest).invoke(
            [
                "build",
                "--select", *models,
                "--vars", json.dumps(variables or {}),
                # A test that reads several models runs only when all of them are selected;
                # otherwise it would query a table that was never built.
                "--indirect-selection", "cautious",
                "--project-dir", str(DBT_DIR),
                "--profiles-dir", str(profiles_dir),
                "--target-path", str(tmp_path / "target"),
                "--log-path", str(tmp_path / "logs"),
                "--no-version-check",
                "--quiet",
            ]
        )  # fmt: skip
        statuses = {
            r.node.name: str(r.status.value)
            for r in (result.result.results if result.result else [])  # type: ignore[union-attr]
        }
        return BuildResult(result.success, statuses, database)

    store = LocalObjectStore(raw_dir)
    return Workspace(raw_dir, RawZone(store), ZipStager(store), build)

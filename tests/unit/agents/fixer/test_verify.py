"""FR-FIX-03, FR-FIX-06: a failed patch is revised from the failure; every attempt is kept."""

from datetime import UTC, datetime

from lineageheal.agents.fixer import Patch, VerificationStatus, compare, verify_incident
from lineageheal.agents.fixer.fixer import FixerResult, FixerStatus
from lineageheal.agents.fixer.sandbox import SandboxError, SandboxResult
from lineageheal.agents.fixer.verify import model_of
from lineageheal.store.incidents import StoredIncident

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
PATCH = Patch("--- a/x\n+++ b/x\n", ("dbt/models/x.sql",), 2)
GOOD = compare({"t": "fail"}, {"t": "pass"}, "m+")
BAD = compare({"t": "fail"}, {"t": "fail"}, "m+")


def incident(node: str | None = "brz_amfi__navall.nav_inr") -> StoredIncident:
    return StoredIncident(
        id=1,
        status="investigating",
        summary="s",
        severity="error",
        fingerprint="fp",
        root_node=node,
        affected={},
        lineage_version="v1",
        fault_class="schema_drift",
        confidence=0.9,
        classified_by="investigator",
        opened_at=NOW,
        updated_at=NOW,
        root_cause={"node": node} if node else None,
    )


class FakeFixer:
    def __init__(self, *results: FixerResult) -> None:
        self.results = list(results)
        self.feedback: list[str | None] = []

    def propose(self, incident: StoredIncident, feedback: str | None = None) -> FixerResult:
        self.feedback.append(feedback)
        return self.results.pop(0)


class FakeSandbox:
    def __init__(self, *verdicts: SandboxResult | SandboxError) -> None:
        self.verdicts = list(verdicts)
        self.models: list[str] = []

    def verify(self, patch: Patch, model: str) -> SandboxResult:
        self.models.append(model)
        verdict = self.verdicts.pop(0)
        if isinstance(verdict, SandboxError):
            raise verdict
        return verdict


def patched(calls: int = 1) -> FixerResult:
    return FixerResult(FixerStatus.PATCHED, patch=PATCH, llm_calls=calls, input_tokens=10)


def test_a_patch_that_passes_the_first_time_is_verified() -> None:
    fixer, sandbox = FakeFixer(patched()), FakeSandbox(GOOD)

    outcome = verify_incident(fixer, sandbox, incident())

    assert outcome.status is VerificationStatus.VERIFIED
    assert outcome.verified is not None
    assert sandbox.models == ["brz_amfi__navall"]
    assert fixer.feedback == [None]


def test_a_failed_patch_is_revised_with_the_failure_and_can_then_pass() -> None:
    fixer, sandbox = FakeFixer(patched(), patched(2)), FakeSandbox(BAD, GOOD)

    outcome = verify_incident(fixer, sandbox, incident())

    assert outcome.status is VerificationStatus.VERIFIED
    assert len(outcome.attempts) == 2
    assert fixer.feedback[0] is None
    assert "Still failing" in (fixer.feedback[1] or "")
    assert (outcome.llm_calls, outcome.input_tokens) == (3, 20)


def test_when_no_attempt_passes_all_are_kept_and_the_outcome_is_unverified() -> None:
    fixer, sandbox = FakeFixer(patched(), patched()), FakeSandbox(BAD, BAD)

    outcome = verify_incident(fixer, sandbox, incident(), max_attempts=2)

    assert outcome.status is VerificationStatus.UNVERIFIED
    assert len(outcome.attempts) == 2
    assert outcome.verified is None


def test_attempts_stop_at_the_limit() -> None:
    fixer, sandbox = FakeFixer(patched(), patched(), patched()), FakeSandbox(BAD, BAD, GOOD)

    outcome = verify_incident(fixer, sandbox, incident(), max_attempts=2)

    assert len(outcome.attempts) == 2
    assert len(sandbox.verdicts) == 1


def test_a_fixer_with_nothing_to_offer_leaves_no_attempts() -> None:
    declined = FixerResult(FixerStatus.DECLINED, "not enough information")

    outcome = verify_incident(FakeFixer(declined), FakeSandbox(), incident())

    assert outcome.status is VerificationStatus.NO_PATCH
    assert outcome.reason == "not enough information"


def test_a_fixer_that_gives_up_after_a_failed_attempt_keeps_that_attempt() -> None:
    rejected = FixerResult(FixerStatus.REJECTED, "touches a file outside dbt")

    outcome = verify_incident(FakeFixer(patched(), rejected), FakeSandbox(BAD), incident())

    assert outcome.status is VerificationStatus.UNVERIFIED
    assert len(outcome.attempts) == 1


def test_a_sandbox_that_cannot_run_is_an_error_and_is_not_retried() -> None:
    fixer, sandbox = FakeFixer(patched(), patched()), FakeSandbox(SandboxError("git failed"))

    outcome = verify_incident(fixer, sandbox, incident())

    assert outcome.status is VerificationStatus.ERROR
    assert outcome.reason == "git failed"
    assert len(fixer.results) == 1


def test_an_incident_without_a_model_is_not_sent_to_the_fixer() -> None:
    fixer = FakeFixer()

    outcome = verify_incident(fixer, FakeSandbox(), incident(None))

    assert outcome.status is VerificationStatus.NO_PATCH
    assert model_of(incident(None)) is None
    assert model_of(incident("slv_nse__prices.close_inr")) == "slv_nse__prices"

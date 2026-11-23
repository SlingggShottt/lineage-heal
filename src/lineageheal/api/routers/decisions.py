"""Approve or reject a proposed fix (FR-REV-01, FR-REV-02, FR-REV-03).

Both endpoints are behind `approver_action`: signed in, on the allowlist, and carrying the CSRF
token. That guard runs before anything else, so a stranger learns nothing about GitHub or the
incident, and nothing is ever sent to GitHub for someone who may not decide.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from lineageheal.api.dependencies import ApproverDep, DecisionServiceDep
from lineageheal.api.problems import PROBLEM_RESPONSES
from lineageheal.api.routers.incidents import IncidentId

router = APIRouter(
    tags=["decisions"],
    responses={
        401: PROBLEM_RESPONSES[401],
        403: PROBLEM_RESPONSES[403],
        404: PROBLEM_RESPONSES[404],
        409: PROBLEM_RESPONSES[409],
        503: PROBLEM_RESPONSES[503],
    },
)

MAX_REASON = 1000


class Decision(BaseModel):
    """What an approval or rejection did."""

    incident_id: int
    fix_id: int
    decision: Literal["approved", "rejected"]
    reviewer: str
    decided_at: datetime
    pull_request_number: int
    pull_request_url: str
    # The squash-merge commit; present for an approval only.
    merge_commit: str | None


class RejectRequest(BaseModel):
    """Why the fix is being rejected. A rejection without a reason is refused."""

    reason: Annotated[str, Field(min_length=1, max_length=MAX_REASON)]

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("a reason is required")
        return stripped


@router.post("/incidents/{incident_id}/approve", summary="Approve and merge the fix")
def approve(incident_id: IncidentId, session: ApproverDep, service: DecisionServiceDep) -> Decision:
    """Submit an approving review and squash-merge the pull request as the signed-in approver."""
    return Decision(**vars(service.approve(incident_id, session)))


@router.post("/incidents/{incident_id}/reject", summary="Reject the fix")
def reject(
    incident_id: IncidentId,
    body: RejectRequest,
    session: ApproverDep,
    service: DecisionServiceDep,
) -> Decision:
    """Close the pull request unmerged and record the reason."""
    return Decision(**vars(service.reject(incident_id, session, body.reason)))

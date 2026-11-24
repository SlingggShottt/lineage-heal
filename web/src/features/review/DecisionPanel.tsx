import { useSignInHref } from "@/features/session/useSignInHref";
import { useState } from "react";

import { useMe } from "@/api/queries";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import { exactTime, relativeTime } from "@/lib/format";

import { ApproveDialog } from "./ApproveDialog";
import { RejectDialog } from "./RejectDialog";

type Fix = components["schemas"]["FixDetail"];
type DecisionRecord = components["schemas"]["DecisionRecord"];

type DecisionPanelProps = {
  incidentId: number;
  incidentStatus: string;
  decision: DecisionRecord | null;
  fix: Fix;
};

function Recorded({ decision }: { decision: DecisionRecord }) {
  const approved = decision.decision === "approved";
  return (
    <div className="flex flex-col gap-1">
      <p className="font-medium">
        {approved ? "Approved" : "Rejected"} by {decision.reviewer} ·{" "}
        <time dateTime={decision.decided_at} title={exactTime(decision.decided_at)}>
          {relativeTime(decision.decided_at)}
        </time>
      </p>
      {decision.reason && (
        <blockquote className="text-muted-foreground border-l-2 pl-3">{decision.reason}</blockquote>
      )}
    </div>
  );
}

/**
 * The decision on a proposed fix: the buttons for an approver, a reason why not for everyone else,
 * and the recorded outcome once it is decided.
 *
 * Hiding the buttons is courtesy, not security: the server checks who is asking on every request.
 */
export function DecisionPanel({ incidentId, incidentStatus, decision, fix }: DecisionPanelProps) {
  const { data: me, isPending, isError } = useMe();
  const signInHref = useSignInHref();
  const [dialog, setDialog] = useState<"approve" | "reject" | null>(null);

  if (decision) {
    return (
      <div>
        <h3 className="mb-1 font-medium">Decision</h3>
        <Recorded decision={decision} />
      </div>
    );
  }
  const waiting =
    fix.kind === "dbt_patch" &&
    fix.status === "published" &&
    fix.github_number !== null &&
    incidentStatus === "fix_proposed";
  if (!waiting || fix.github_number === null || isPending) {
    return null;
  }

  let body;
  if (isError) {
    body = (
      <p className="text-muted-foreground">Sign-in status is unavailable, so review is off.</p>
    );
  } else if (!me.signed_in) {
    body = (
      <p>
        <a href={signInHref} className="text-primary underline underline-offset-4">
          Sign in with GitHub
        </a>{" "}
        to review this fix.
      </p>
    );
  } else if (!me.is_approver) {
    body = (
      <p className="text-muted-foreground">
        You are signed in as <strong>{me.login}</strong>, who cannot approve fixes. Approvers are
        listed by the team that runs this project.
      </p>
    );
  } else {
    body = (
      <>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() => {
              setDialog("approve");
            }}
          >
            Approve and merge…
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              setDialog("reject");
            }}
          >
            Reject…
          </Button>
        </div>
        <ApproveDialog
          incidentId={incidentId}
          pullRequestNumber={fix.github_number}
          login={me.login ?? ""}
          open={dialog === "approve"}
          onClose={() => {
            setDialog(null);
          }}
        />
        <RejectDialog
          incidentId={incidentId}
          pullRequestNumber={fix.github_number}
          open={dialog === "reject"}
          onClose={() => {
            setDialog(null);
          }}
        />
      </>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <h3 className="font-medium">Review</h3>
      {body}
    </div>
  );
}

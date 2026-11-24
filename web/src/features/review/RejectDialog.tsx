import { useId, useState } from "react";

import { ConfirmDialog } from "./ConfirmDialog";
import { describeFailure } from "./describeFailure";
import { useReject } from "./useDecision";

const MAX_REASON = 1000;

type RejectDialogProps = {
  incidentId: number;
  pullRequestNumber: number;
  open: boolean;
  onClose: () => void;
};

/** A rejection needs a written reason, which is posted on the pull request and kept. */
export function RejectDialog({ incidentId, pullRequestNumber, open, onClose }: RejectDialogProps) {
  const reject = useReject(incidentId);
  const [reason, setReason] = useState("");
  const fieldId = useId();
  const trimmed = reason.trim();

  const close = () => {
    reject.reset();
    setReason("");
    onClose();
  };

  return (
    <ConfirmDialog
      open={open}
      title="Reject this fix?"
      confirmLabel="Reject and close"
      destructive
      confirmDisabled={trimmed === ""}
      pending={reject.isPending}
      failure={reject.isError ? describeFailure(reject.error) : null}
      onConfirm={() => {
        reject.mutate(trimmed, { onSuccess: close });
      }}
      onClose={close}
    >
      <p>
        Pull request <strong>#{pullRequestNumber}</strong> will be closed without merging, and your
        reason will be posted on it as a comment.
      </p>
      <div className="flex flex-col gap-1">
        <label htmlFor={fieldId} className="font-medium">
          Why is this fix being rejected?
        </label>
        <textarea
          id={fieldId}
          required
          rows={4}
          maxLength={MAX_REASON}
          value={reason}
          onChange={(event) => {
            setReason(event.target.value);
          }}
          className="bg-background rounded-md border p-2"
        />
        <span className="text-muted-foreground text-xs">
          {reason.length} of {MAX_REASON} characters
        </span>
      </div>
    </ConfirmDialog>
  );
}

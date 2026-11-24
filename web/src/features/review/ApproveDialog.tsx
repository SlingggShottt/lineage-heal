import { ConfirmDialog } from "./ConfirmDialog";
import { describeFailure } from "./describeFailure";
import { useApprove } from "./useDecision";

type ApproveDialogProps = {
  incidentId: number;
  pullRequestNumber: number;
  login: string;
  open: boolean;
  onClose: () => void;
};

/** Asks the approver to confirm, saying exactly what will happen on GitHub and as whom. */
export function ApproveDialog({
  incidentId,
  pullRequestNumber,
  login,
  open,
  onClose,
}: ApproveDialogProps) {
  const approve = useApprove(incidentId);
  return (
    <ConfirmDialog
      open={open}
      title="Approve and merge this fix?"
      confirmLabel="Approve and merge"
      pending={approve.isPending}
      failure={approve.isError ? describeFailure(approve.error) : null}
      onConfirm={() => {
        approve.mutate(undefined, { onSuccess: onClose });
      }}
      onClose={() => {
        approve.reset();
        onClose();
      }}
    >
      <p>
        This submits an approving review as <strong>{login}</strong> and squash-merges pull request{" "}
        <strong>#{pullRequestNumber}</strong> using your GitHub account.
      </p>
      <p className="text-muted-foreground">A merge cannot be undone from here.</p>
    </ConfirmDialog>
  );
}

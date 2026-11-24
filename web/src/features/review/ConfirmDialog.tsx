import { useEffect, useId, useRef, type ReactNode, type SyntheticEvent } from "react";

import { Button } from "@/components/ui/button";

import type { Failure } from "./describeFailure";

type ConfirmDialogProps = {
  open: boolean;
  title: string;
  confirmLabel: string;
  /** The destructive look is for the action that cannot be undone from here. */
  destructive?: boolean;
  confirmDisabled?: boolean;
  pending: boolean;
  failure: Failure | null;
  onConfirm: () => void;
  onClose: () => void;
  children: ReactNode;
};

/**
 * A modal that asks before something irreversible happens.
 *
 * It is the browser's own `<dialog>`, so focus is kept inside it, Esc closes it and focus returns
 * to the button that opened it, with no library. While the request runs it cannot be dismissed
 * and the buttons are off, so a double click cannot send the decision twice. A failure is shown
 * inside it and the dialog stays open, so the approver can fix the cause and try again.
 */
export function ConfirmDialog({
  open,
  title,
  confirmLabel,
  destructive = false,
  confirmDisabled = false,
  pending,
  failure,
  onConfirm,
  onClose,
  children,
}: ConfirmDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) {
      return;
    }
    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    if (!pending && !confirmDisabled) {
      onConfirm();
    }
  };

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        if (pending) {
          event.preventDefault();
        }
      }}
      onClose={() => {
        if (open) {
          onClose();
        }
      }}
      className="bg-card text-card-foreground m-auto w-[min(32rem,calc(100vw-2rem))] rounded-lg border p-0 shadow-lg backdrop:bg-black/50"
    >
      <form onSubmit={submit} className="flex flex-col gap-4 p-6">
        <h2 id={titleId} className="text-lg font-semibold">
          {title}
        </h2>
        <div className="flex flex-col gap-3 text-sm">{children}</div>
        {failure && (
          <div
            role="alert"
            className="border-destructive/40 text-destructive rounded-md border p-3 text-sm"
          >
            <p className="font-medium">{failure.title}</p>
            <p>{failure.detail}</p>
            {failure.signInAgain && (
              <p className="mt-2">
                <a href="/api/auth/login" className="underline">
                  Sign in with GitHub
                </a>
              </p>
            )}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={pending} onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant={destructive ? "destructive" : "default"}
            disabled={pending || confirmDisabled}
          >
            {pending ? "Working…" : confirmLabel}
          </Button>
        </div>
      </form>
    </dialog>
  );
}

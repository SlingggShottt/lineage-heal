import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ANONYMOUS, detail, lineage, patchFix, type Detail, type Fix } from "@/test/fixtures";
import { fakeApi, renderApp } from "@/test/render";

const APPROVER = {
  signed_in: true,
  login: "ada",
  is_approver: true,
  csrf_token: "csrf-abc",
  expires_at: "2026-11-24T08:00:00Z",
};
const STRANGER = { ...APPROVER, login: "mallory", is_approver: false };

const APPROVED: NonNullable<Detail["decision"]> = {
  decision: "approved",
  reviewer: "ada",
  reason: null,
  decided_at: "2026-11-24T10:40:00Z",
};

const problem = (slug: string, status: number, title: string, text: string) => ({
  status,
  body: { type: `urn:lineageheal:problem:${slug}`, title, status, detail: text },
});

type Options = {
  me?: object;
  fix?: Fix;
  incident?: Partial<Detail>;
  approve?: Parameters<typeof fakeApi>[0][string];
  reject?: Parameters<typeof fakeApi>[0][string];
};

/** An incident that answers as the API would, before and after a decision is recorded. */
function world({ me = APPROVER, fix = patchFix, incident = {}, approve, reject }: Options = {}) {
  let after: Partial<Detail> | null = null;
  const decided = (decision: Detail["decision"], status: string) => {
    after = { status: status as Detail["status"], decision };
  };
  const api = fakeApi({
    "/me": { body: me },
    "/incidents/12": () => ({ body: detail({ ...incident, ...after }) }),
    "/incidents/12/lineage": { body: lineage },
    "/incidents/12/fix": { body: fix },
    "POST /incidents/12/approve":
      approve ??
      (() => {
        decided(APPROVED, "approved");
        return { body: { decision: "approved" } };
      }),
    "POST /incidents/12/reject":
      reject ??
      (() => {
        decided({ ...APPROVED, decision: "rejected", reason: "Wrong model." }, "rejected");
        return { body: { decision: "rejected" } };
      }),
  });
  return api;
}

const posts = (api: ReturnType<typeof fakeApi>) =>
  api.seen.filter((request) => request.method === "POST");

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-24T10:45:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("who sees the controls", () => {
  it("shows an approver the two buttons while a published patch awaits a decision", async () => {
    renderApp("/incidents/12", world().fetch);

    expect(await screen.findByRole("button", { name: "Approve and merge…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject…" })).toBeInTheDocument();
  });

  it("tells a signed-in non-approver why there are no buttons", async () => {
    renderApp("/incidents/12", world({ me: STRANGER }).fetch);

    expect(await screen.findByText(/who cannot approve fixes/)).toHaveTextContent("mallory");
    expect(screen.queryByRole("button", { name: "Approve and merge…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject…" })).not.toBeInTheDocument();
  });

  it("offers a signed-out visitor sign-in instead of the buttons", async () => {
    renderApp("/incidents/12", world({ me: ANONYMOUS.body }).fetch);

    const review = (await screen.findByRole("heading", { name: "Review" }))
      .parentElement as HTMLElement;
    expect(within(review).getByRole("link", { name: "Sign in with GitHub" })).toHaveAttribute(
      "href",
      "/api/auth/login",
    );
    expect(screen.queryByRole("button", { name: "Approve and merge…" })).not.toBeInTheDocument();
  });

  it("shows no controls for a suggestion, which is resolved by hand", async () => {
    const suggestion: Fix = {
      ...patchFix,
      kind: "suggestion",
      patch: null,
      sandbox: null,
      suggestion: { title: "Retry the download" },
    };
    renderApp("/incidents/12", world({ fix: suggestion }).fetch);

    await screen.findByText("Retry the download");
    expect(screen.queryByRole("heading", { name: "Review" })).not.toBeInTheDocument();
  });

  it("shows no controls once the incident is past waiting for a decision", async () => {
    renderApp("/incidents/12", world({ incident: { status: "unresolved" } }).fetch);

    await screen.findByRole("heading", { name: "Proposed fix" });
    await screen.findByText("Published");
    expect(screen.queryByRole("heading", { name: "Review" })).not.toBeInTheDocument();
  });

  it("shows who decided and when, in place of the buttons", async () => {
    renderApp(
      "/incidents/12",
      world({ incident: { status: "approved", decision: APPROVED } }).fetch,
    );

    const panel = (await screen.findByRole("heading", { name: "Decision" }))
      .parentElement as HTMLElement;
    expect(panel).toHaveTextContent("Approved by ada · 5 minutes ago");
    expect(screen.queryByRole("button", { name: "Approve and merge…" })).not.toBeInTheDocument();
  });

  it("shows a rejection with its reason", async () => {
    const rejected = {
      ...APPROVED,
      decision: "rejected" as const,
      reason: "The rename belongs in silver.",
    };
    renderApp(
      "/incidents/12",
      world({ incident: { status: "rejected", decision: rejected } }).fetch,
    );

    const panel = (await screen.findByRole("heading", { name: "Decision" }))
      .parentElement as HTMLElement;
    expect(panel).toHaveTextContent("Rejected by ada");
    expect(panel).toHaveTextContent("The rename belongs in silver.");
  });
});

describe("approving", () => {
  it("asks first, saying what will happen and as whom, and can be cancelled without a request", async () => {
    const user = userEvent.setup();
    const api = world();
    renderApp("/incidents/12", api.fetch);

    await user.click(await screen.findByRole("button", { name: "Approve and merge…" }));

    const dialog = screen.getByRole("dialog", { name: "Approve and merge this fix?" });
    expect(dialog).toHaveTextContent("approving review as ada");
    expect(dialog).toHaveTextContent("pull request #101");
    expect(dialog).toHaveTextContent("A merge cannot be undone from here.");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(api)).toEqual([]);
  });

  it("sends the CSRF token, then shows the recorded decision and removes the buttons", async () => {
    const user = userEvent.setup();
    const api = world();
    renderApp("/incidents/12", api.fetch);
    await user.click(await screen.findByRole("button", { name: "Approve and merge…" }));

    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Approve and merge" }),
    );

    expect(await screen.findByRole("heading", { name: "Decision" })).toBeInTheDocument();
    expect(screen.getByText(/Approved by ada/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve and merge…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const [request, ...others] = posts(api);
    expect(others).toEqual([]);
    expect(new URL(request?.url ?? "").pathname).toBe("/api/incidents/12/approve");
    expect(request?.headers.get("x-csrf-token")).toBe("csrf-abc");
    // the header badge reflects the new status after the incident is read again
    expect(screen.getByRole("heading", { level: 1 }).closest("header")).toHaveTextContent(
      "Approved",
    );
  });

  it("cannot be sent twice by a double click", async () => {
    const user = userEvent.setup();
    let release: () => void = () => undefined;
    const wait = new Promise<void>((resolve) => {
      release = resolve;
    });
    const api = world({
      approve: async () => {
        await wait;
        return { body: { decision: "approved" } };
      },
    });
    renderApp("/incidents/12", api.fetch);
    await user.click(await screen.findByRole("button", { name: "Approve and merge…" }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Approve and merge" });

    await user.dblClick(confirm);

    const working = await within(dialog).findByRole("button", { name: "Working…" });
    expect(working).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(posts(api)).toHaveLength(1);
    release();
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(posts(api)).toHaveLength(1);
  });

  it("keeps the dialog open with GitHub's own reason when the merge is refused, and lets the approver retry", async () => {
    const user = userEvent.setup();
    let attempts = 0;
    const api = world({
      approve: () => {
        attempts += 1;
        return attempts === 1
          ? problem(
              "github-refused",
              409,
              "GitHub refused",
              'GitHub would not merge this pull request: Required status check "ci" is expected.',
            )
          : { body: { decision: "approved" } };
      },
    });
    renderApp("/incidents/12", api.fetch);
    await user.click(await screen.findByRole("button", { name: "Approve and merge…" }));
    const dialog = screen.getByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "Approve and merge" }));

    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("GitHub refused");
    expect(alert).toHaveTextContent('Required status check "ci" is expected.');
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    const retry = within(dialog).getByRole("button", { name: "Approve and merge" });
    expect(retry).toBeEnabled();

    await user.click(retry);

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(attempts).toBe(2);
  });

  it("asks the approver to sign in again, with a link, when GitHub no longer accepts their token", async () => {
    const user = userEvent.setup();
    const api = world({
      approve: () =>
        problem(
          "github-token-unusable",
          401,
          "Sign in again",
          "GitHub no longer accepts your sign-in.",
        ),
    });
    renderApp("/incidents/12", api.fetch);
    await user.click(await screen.findByRole("button", { name: "Approve and merge…" }));
    const dialog = screen.getByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "Approve and merge" }));

    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Sign in again");
    expect(within(alert).getByRole("link", { name: "Sign in with GitHub" })).toHaveAttribute(
      "href",
      "/api/auth/login",
    );
  });

  it.each([
    [
      problem(
        "github-unavailable",
        503,
        "GitHub unavailable",
        "GitHub could not be reached; try again shortly.",
      ),
      "GitHub could not be reached; try again shortly.",
    ],
    [
      problem("csrf-failed", 403, "Request refused", "The CSRF token is missing or wrong."),
      "Reload the page and try again.",
    ],
    [
      problem(
        "decision-not-recorded",
        503,
        "Decision not recorded",
        "GitHub carried out the decision on pull request #101, but recording it here failed.",
      ),
      "GitHub carried out the decision on pull request #101",
    ],
  ])("explains %#: a problem the API reported", async (answer, expected) => {
    const user = userEvent.setup();
    renderApp("/incidents/12", world({ approve: () => answer }).fetch);
    await user.click(await screen.findByRole("button", { name: "Approve and merge…" }));
    const dialog = screen.getByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "Approve and merge" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(expected);
  });

  it("says the API could not be reached when the request itself fails", async () => {
    const user = userEvent.setup();
    const api = world({
      approve: () => {
        throw new TypeError("Failed to fetch");
      },
    });
    renderApp("/incidents/12", api.fetch);
    await user.click(await screen.findByRole("button", { name: "Approve and merge…" }));
    const dialog = screen.getByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "Approve and merge" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The API could not be reached",
    );
  });

  it("starts the next attempt with no old error showing", async () => {
    const user = userEvent.setup();
    const api = world({ approve: () => problem("github-refused", 409, "GitHub refused", "No.") });
    renderApp("/incidents/12", api.fetch);
    await user.click(await screen.findByRole("button", { name: "Approve and merge…" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Approve and merge" }),
    );
    await within(screen.getByRole("dialog")).findByRole("alert");

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Approve and merge…" }));

    expect(within(screen.getByRole("dialog")).queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("rejecting", () => {
  it("will not send a rejection without a written reason", async () => {
    const user = userEvent.setup();
    const api = world();
    renderApp("/incidents/12", api.fetch);
    await user.click(await screen.findByRole("button", { name: "Reject…" }));
    const dialog = screen.getByRole("dialog", { name: "Reject this fix?" });
    const confirm = within(dialog).getByRole("button", { name: "Reject and close" });
    expect(confirm).toBeDisabled();

    await user.type(within(dialog).getByLabelText("Why is this fix being rejected?"), "   ");

    expect(confirm).toBeDisabled();
    expect(posts(api)).toEqual([]);
  });

  it("explains what happens, counts the characters and sends the trimmed reason", async () => {
    const user = userEvent.setup();
    const api = world();
    renderApp("/incidents/12", api.fetch);
    await user.click(await screen.findByRole("button", { name: "Reject…" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("#101 will be closed without merging");
    expect(dialog).toHaveTextContent("posted on it as a comment");
    const field = within(dialog).getByLabelText("Why is this fix being rejected?");

    await user.type(field, "  Wrong model.  ");
    expect(dialog).toHaveTextContent("16 of 1000 characters");
    await user.click(within(dialog).getByRole("button", { name: "Reject and close" }));

    expect(await screen.findByText(/Rejected by ada/)).toBeInTheDocument();
    expect(screen.getByText("Wrong model.", { selector: "blockquote" })).toBeInTheDocument();
    const [request] = posts(api);
    expect(new URL(request?.url ?? "").pathname).toBe("/api/incidents/12/reject");
    expect(request?.headers.get("x-csrf-token")).toBe("csrf-abc");
    expect(await request?.clone().json()).toEqual({ reason: "Wrong model." });
  });

  it("forgets a cancelled reason", async () => {
    const user = userEvent.setup();
    renderApp("/incidents/12", world().fetch);
    await user.click(await screen.findByRole("button", { name: "Reject…" }));
    await user.type(screen.getByLabelText("Why is this fix being rejected?"), "half a thought");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));

    await user.click(screen.getByRole("button", { name: "Reject…" }));

    expect(screen.getByLabelText("Why is this fix being rejected?")).toHaveValue("");
  });

  it("keeps the reason and shows the problem when GitHub will not close the pull request", async () => {
    const user = userEvent.setup();
    const api = world({
      reject: () =>
        problem(
          "github-refused",
          409,
          "GitHub refused",
          "GitHub would not close this pull request: Validation Failed",
        ),
    });
    renderApp("/incidents/12", api.fetch);
    await user.click(await screen.findByRole("button", { name: "Reject…" }));
    const dialog = screen.getByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Why is this fix being rejected?"),
      "Wrong model.",
    );

    await user.click(within(dialog).getByRole("button", { name: "Reject and close" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Validation Failed");
    expect(within(dialog).getByLabelText("Why is this fix being rejected?")).toHaveValue(
      "Wrong model.",
    );
  });
});

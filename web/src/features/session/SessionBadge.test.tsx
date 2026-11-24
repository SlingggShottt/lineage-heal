import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeApi, renderApp } from "@/test/render";

function me(body: unknown, status = 200) {
  return fakeApi({ "/me": { status, body } });
}

describe("SessionBadge", () => {
  it("offers sign-in to an anonymous visitor", async () => {
    const api = me({ signed_in: false, is_approver: false });

    renderApp("/", api.fetch);

    const link = await screen.findByRole("link", { name: "Sign in with GitHub" });
    expect(link).toHaveAttribute("href", "/api/auth/login?next=%2F");
  });

  it("brings the visitor back to the page and filters they were on", async () => {
    const api = me({ signed_in: false, is_approver: false });

    renderApp("/incidents?status=fix_proposed", api.fetch);

    const link = await screen.findByRole("link", { name: "Sign in with GitHub" });
    expect(link).toHaveAttribute(
      "href",
      "/api/auth/login?next=%2Fincidents%3Fstatus%3Dfix_proposed",
    );
  });

  it("shows an approver by login", async () => {
    const api = me({
      signed_in: true,
      login: "ada",
      is_approver: true,
      csrf_token: "t",
      expires_at: "2026-11-23T18:00:00Z",
    });

    renderApp("/", api.fetch);

    expect(await screen.findByText("ada")).toBeInTheDocument();
    expect(screen.getByText("Approver")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Sign in with GitHub" })).not.toBeInTheDocument();
  });

  it("shows a signed-in non-approver as read-only", async () => {
    const api = me({ signed_in: true, login: "mallory", is_approver: false, csrf_token: "t" });

    renderApp("/", api.fetch);

    expect(await screen.findByText("Read-only")).toBeInTheDocument();
    expect(screen.queryByText("Approver")).not.toBeInTheDocument();
  });

  it("says so when the API cannot be reached, instead of pretending to be signed out", async () => {
    const api = me({ type: "x", title: "Database unavailable", status: 503 }, 503);

    renderApp("/", api.fetch);

    expect(await screen.findByRole("status")).toHaveTextContent("Sign-in status unavailable");
    expect(screen.queryByRole("link", { name: "Sign in with GitHub" })).not.toBeInTheDocument();
  });

  it("signs out with the CSRF token and goes back to offering sign-in", async () => {
    const user = userEvent.setup();
    let signedIn = true;
    const api = fakeApi({
      "/me": () => ({
        body: signedIn
          ? { signed_in: true, login: "ada", is_approver: true, csrf_token: "csrf-abc" }
          : { signed_in: false, is_approver: false },
      }),
      "POST /auth/logout": () => {
        signedIn = false;
        return { status: 204, body: null };
      },
    });
    renderApp("/", api.fetch);

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("link", { name: "Sign in with GitHub" })).toBeInTheDocument();
    expect(screen.queryByText("ada")).not.toBeInTheDocument();
    const logout = api.seen.find((request) => request.method === "POST");
    expect(new URL(logout?.url ?? "").pathname).toBe("/api/auth/logout");
    expect(logout?.headers.get("x-csrf-token")).toBe("csrf-abc");
  });

  it("says so, and stays signed in, when signing out fails", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "/me": { body: { signed_in: true, login: "ada", is_approver: false, csrf_token: "t" } },
      "POST /auth/logout": {
        status: 403,
        body: {
          type: "urn:lineageheal:problem:csrf-failed",
          title: "Request refused",
          status: 403,
        },
      },
    });
    renderApp("/", api.fetch);

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not sign out");
    expect(screen.getByText("ada")).toBeInTheDocument();
  });
});

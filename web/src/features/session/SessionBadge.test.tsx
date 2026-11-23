import { screen } from "@testing-library/react";
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
    expect(link).toHaveAttribute("href", "/api/auth/login");
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
});

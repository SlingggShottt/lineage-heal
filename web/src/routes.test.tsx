import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { fakeApi, renderApp } from "@/test/render";

const anonymous = () =>
  fakeApi({
    "/me": { body: { signed_in: false, is_approver: false } },
    "/incidents": { body: { items: [], total: 0, limit: 25, offset: 0 } },
  });

describe("the app shell and routes", () => {
  it("has a skip link, a main landmark and the three sections in the navigation", async () => {
    renderApp("/", anonymous().fetch);

    expect(await screen.findByRole("link", { name: "Skip to content" })).toHaveAttribute(
      "href",
      "#content",
    );
    expect(screen.getByRole("main")).toHaveAttribute("id", "content");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveTextContent("Overview");
    expect(nav).toHaveTextContent("Incidents");
    expect(nav).toHaveTextContent("Runbooks");
    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
  });

  it("navigates between sections and marks the current one", async () => {
    renderApp("/", anonymous().fetch);

    await userEvent.click(await screen.findByRole("link", { name: "Incidents" }));

    expect(screen.getByRole("heading", { level: 1, name: "Incidents" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Incidents" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("shows a not-found page with a way back for an unknown address", async () => {
    renderApp("/nowhere", anonymous().fetch);

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the overview" })).toHaveAttribute("href", "/");
  });
});

describe("an incident's address", () => {
  it("shows that no such incident exists, with a way back to the list", async () => {
    const api = fakeApi({
      "/me": { body: { signed_in: false, is_approver: false } },
      "/incidents/12": {
        status: 404,
        body: { type: "urn:lineageheal:problem:not-found", title: "Not found", status: 404 },
      },
    });

    renderApp("/incidents/12", api.fetch);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Incident not found" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to all incidents" })).toHaveAttribute(
      "href",
      "/incidents",
    );
  });
});

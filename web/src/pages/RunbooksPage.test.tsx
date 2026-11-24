import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { components } from "@/api/schema";
import { fakeApi, renderApp } from "@/test/render";

type Summary = components["schemas"]["RunbookSummary"];
type Runbook = components["schemas"]["Runbook"];

const ANONYMOUS = { body: { signed_in: false, is_approver: false } };

const NULLS: Summary = {
  slug: "nulls",
  title: "A required value arrives blank",
  fault_class: "nulls",
  feeds: "AMFI NAVAll, NSE bhavcopy",
};
const DRIFT: Summary = {
  slug: "schema-drift",
  title: "A publisher renames, drops or retypes a column",
  fault_class: "schema_drift",
  feeds: "NSE bhavcopy",
};

const NULLS_FULL: Runbook = {
  ...NULLS,
  markdown: "unused by the screen",
  sections: [
    { heading: "Symptoms", text: "- A `not_null` test fails for **one** row." },
    { heading: "Diagnosis", text: "1. Read the failing rows.\n2. Check the raw file." },
    { heading: "Fix", text: "Re-download the day." },
  ],
};

const PROBLEM_404 = {
  status: 404,
  body: {
    type: "urn:lineageheal:problem:not-found",
    title: "Not found",
    status: 404,
    detail: "There is no runbook 'absent'.",
  },
};

describe("the runbooks list", () => {
  it("shows each runbook with its title, fault class and feeds, linked", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/runbooks": { body: { items: [NULLS, DRIFT], total: 2, limit: 100, offset: 0 } },
    });

    renderApp("/runbooks", api.fetch);

    const link = await screen.findByRole("link", { name: NULLS.title });
    expect(link).toHaveAttribute("href", "/runbooks/nulls");
    const card = link.closest("li") as HTMLElement;
    expect(card).toHaveTextContent("Nulls");
    expect(card).toHaveTextContent("Feeds: AMFI NAVAll, NSE bhavcopy");
    expect(screen.getByRole("link", { name: DRIFT.title })).toHaveAttribute(
      "href",
      "/runbooks/schema-drift",
    );
    expect(within(screen.getByRole("main")).getByText("Schema drift")).toBeInTheDocument();
  });

  it("says so when the server has no runbooks", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/runbooks": { body: { items: [], total: 0, limit: 100, offset: 0 } },
    });

    renderApp("/runbooks", api.fetch);

    expect(await screen.findByText("There are no runbooks on this server.")).toBeInTheDocument();
  });

  it("explains a failure and retries on demand", async () => {
    let failing = true;
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/runbooks": () =>
        failing
          ? { status: 503, body: { type: "x", title: "Down", status: 503, detail: "Try later." } }
          : { body: { items: [NULLS], total: 1, limit: 100, offset: 0 } },
    });
    const user = userEvent.setup();

    renderApp("/runbooks", api.fetch);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Runbooks could not be loaded");
    expect(alert).toHaveTextContent("Try later.");
    failing = false;
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: NULLS.title })).toBeInTheDocument();
  });
});

describe("one runbook", () => {
  it("shows the sections in file order with their text formatted", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/runbooks/nulls": { body: NULLS_FULL } });

    renderApp("/runbooks/nulls", api.fetch);

    expect(await screen.findByRole("heading", { level: 1, name: NULLS.title })).toBeInTheDocument();
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Symptoms", "Diagnosis", "Fix"]);
    expect(screen.getByText("not_null").tagName).toBe("CODE");
    expect(screen.getByText("one").tagName).toBe("STRONG");
    expect(screen.getByText("Re-download the day.")).toBeInTheDocument();
  });

  it("links back to the list", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/runbooks/nulls": { body: NULLS_FULL } });

    renderApp("/runbooks/nulls", api.fetch);

    expect(await screen.findByRole("link", { name: "All runbooks" })).toHaveAttribute(
      "href",
      "/runbooks",
    );
  });

  it("says a runbook is not found, and keeps the way back", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/runbooks/absent": PROBLEM_404 });

    renderApp("/runbooks/absent", api.fetch);

    expect(await screen.findByRole("heading", { name: "Runbook not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All runbooks" })).toBeInTheDocument();
  });

  it("reports other failures with a retry instead of calling it missing", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/runbooks/nulls": { status: 503, body: { type: "x", title: "Down", status: 503 } },
    });

    renderApp("/runbooks/nulls", api.fetch);

    expect(await screen.findByRole("alert")).toHaveTextContent("The runbook could not be loaded");
    expect(screen.queryByText("Runbook not found")).not.toBeInTheDocument();
  });
});

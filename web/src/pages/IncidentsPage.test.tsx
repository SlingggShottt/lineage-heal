import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "@/api/schema";
import { fakeApi, renderApp } from "@/test/render";

type Incident = components["schemas"]["IncidentSummary"];

const ANONYMOUS = { body: { signed_in: false, is_approver: false } };

function incident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: 12,
    status: "fix_proposed",
    summary: "not_null on brz_amfi__navall.nav_inr (8742 rows)",
    severity: "error",
    fault_class: "schema_drift",
    confidence: 0.93,
    classified_by: "investigator",
    root_node: "raw.amfi.navall.Net Asset Value",
    affected_models: 4,
    affected_gold_tables: 2,
    opened_at: "2026-11-23T08:40:00Z",
    updated_at: "2026-11-23T08:50:00Z",
    ...overrides,
  };
}

const page = (items: Incident[], total = items.length) => ({
  body: { items, total, limit: 25, offset: 0 },
});

/** The API requests for /incidents that the page has made, as their query parameters. */
function listRequests(api: ReturnType<typeof fakeApi>) {
  return api.seen
    .map((request) => new URL(request.url))
    .filter((url) => url.pathname === "/api/incidents")
    .map((url) => Object.fromEntries(url.searchParams));
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-23T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the incidents list", () => {
  it("shows each incident with its status, severity, class, confidence, cause and age", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/incidents": page([
        incident(),
        incident({
          id: 11,
          summary: "ingestion on amfi/navall/2026-11-20 and 1 more signal",
          status: "suggestion_filed",
          severity: "warn",
          fault_class: "late_data",
          confidence: null,
          root_node: null,
          opened_at: "2026-11-21T10:00:00Z",
        }),
      ]),
    });

    renderApp("/incidents", api.fetch);

    const table = await screen.findByRole("table", { name: "Incidents, newest first" });
    const [, first, second] = within(table).getAllByRole("row");
    expect(first).toHaveTextContent("#12");
    expect(first).toHaveTextContent("Fix proposed");
    expect(first).toHaveTextContent("Error");
    expect(first).toHaveTextContent("Schema drift");
    expect(first).toHaveTextContent("93%");
    expect(first).toHaveTextContent("raw.amfi.navall.Net Asset Value");
    expect(first).toHaveTextContent("3 hours ago");
    expect(second).toHaveTextContent("Suggestion filed");
    expect(second).toHaveTextContent("Warning");
    expect(second).toHaveTextContent("Late data");
    expect(second).toHaveTextContent("2 days ago");
    expect(within(second as HTMLElement).getAllByText("—")).toHaveLength(2); // no confidence, no root
  });

  it("links each summary to its incident and has column headers", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()]) });

    renderApp("/incidents", api.fetch);

    const link = await screen.findByRole("link", { name: /not_null on brz_amfi__navall/ });
    expect(link).toHaveAttribute("href", "/incidents/12");
    for (const name of [
      "Status",
      "Severity",
      "Fault class",
      "Confidence",
      "Root cause",
      "Opened",
    ]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
  });

  it("asks the API for the first page with no filters", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()]) });

    renderApp("/incidents", api.fetch);

    await screen.findByRole("table");
    expect(listRequests(api)).toEqual([{ limit: "25", offset: "0" }]);
  });

  it("starts from the filters and page in the address", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()], 60) });

    renderApp("/incidents?status=fix_proposed&feed=amfi&page=2", api.fetch);

    await screen.findByRole("table");
    expect(listRequests(api)[0]).toEqual({
      status: "fix_proposed",
      feed: "amfi",
      limit: "25",
      offset: "25",
    });
    expect(screen.getByLabelText("Status")).toHaveValue("fix_proposed");
    expect(screen.getByLabelText("Feed")).toHaveValue("amfi");
  });

  it("ignores a filter value the API would refuse", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()]) });

    renderApp("/incidents?status=bogus&page=-2", api.fetch);

    await screen.findByRole("table");
    expect(listRequests(api)).toEqual([{ limit: "25", offset: "0" }]);
  });
});

describe("filtering", () => {
  it("asks again with the chosen filter, back on the first page", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()], 60) });
    renderApp("/incidents?page=2", api.fetch);
    await screen.findByRole("table");

    await user.selectOptions(screen.getByLabelText("Fault class"), "Unit change");

    await vi.waitFor(() => {
      expect(listRequests(api).at(-1)).toEqual({
        fault_class: "unit_change",
        limit: "25",
        offset: "0",
      });
    });
    expect(screen.getByLabelText("Fault class")).toHaveValue("unit_change");
  });

  it("offers all four filters with every value the API has", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()]) });
    renderApp("/incidents", api.fetch);
    await screen.findByRole("table");

    expect(screen.getByRole("search", { name: "Filter incidents" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Status")).getAllByRole("option")).toHaveLength(1 + 9);
    expect(within(screen.getByLabelText("Fault class")).getAllByRole("option")).toHaveLength(1 + 6);
    expect(within(screen.getByLabelText("Severity")).getAllByRole("option")).toHaveLength(1 + 2);
    expect(within(screen.getByLabelText("Feed")).getAllByRole("option")).toHaveLength(1 + 3);
  });

  it("clears every filter with one button that only shows while one is set", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()]) });
    renderApp("/incidents?status=detected&severity=warn", api.fetch);
    await screen.findByRole("table");

    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    await vi.waitFor(() => {
      expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
    });
    expect(screen.getByLabelText("Status")).toHaveValue("");
    expect(listRequests(api).at(-1)).toEqual({ limit: "25", offset: "0" });
  });
});

describe("pages", () => {
  it("says which rows are shown and moves to the next page", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()], 60) });
    renderApp("/incidents", api.fetch);
    await screen.findByRole("table");

    expect(screen.getByText("Showing 1–25 of 60")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Next" }));

    await vi.waitFor(() => {
      expect(listRequests(api).at(-1)).toEqual({ limit: "25", offset: "25" });
    });
  });

  it("has no Next on the last page and shows the short last range", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()], 60) });
    renderApp("/incidents?page=3", api.fetch);
    await screen.findByRole("table");

    expect(screen.getByText("Showing 51–60 of 60")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
  });

  it("shows no pager buttons when everything fits on one page", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()]) });
    renderApp("/incidents", api.fetch);
    await screen.findByRole("table");

    expect(screen.getByText("Showing 1–1 of 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
  });

  it("offers the first page when the address points past the end", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([], 3) });

    renderApp("/incidents?page=9", api.fetch);

    expect(await screen.findByText("There is nothing on this page")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go to the first page" })).toBeInTheDocument();
  });
});

describe("loading, empty and failing", () => {
  it("shows a loading state first", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([incident()]) });

    renderApp("/incidents", api.fetch);

    expect(screen.getByRole("status", { name: "Loading incidents" })).toBeInTheDocument();
    await screen.findByRole("table");
    expect(screen.queryByRole("status", { name: "Loading incidents" })).not.toBeInTheDocument();
  });

  it("says there are none yet, without offering to clear filters nobody set", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([]) });

    renderApp("/incidents", api.fetch);

    expect(await screen.findByText("No incidents yet")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear filters" })).not.toBeInTheDocument();
  });

  it("says nothing matches the filters and offers to clear them", async () => {
    const api = fakeApi({ "/me": ANONYMOUS, "/incidents": page([]) });

    renderApp("/incidents?status=approved", api.fetch);

    expect(await screen.findByText("No incidents match these filters")).toBeInTheDocument();
    // one in the filter bar and one in the empty state
    expect(screen.getAllByRole("button", { name: "Clear filters" })).toHaveLength(2);
  });

  it("explains a failure with the API's own words and tries again on request", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    let failures = 1;
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/incidents": () =>
        failures-- > 0
          ? {
              status: 503,
              body: {
                type: "urn:lineageheal:problem:database-unavailable",
                title: "Database unavailable",
                status: 503,
                detail: "The database could not be read.",
              },
            }
          : page([incident()]),
    });
    renderApp("/incidents", api.fetch);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Incidents could not be loaded");
    expect(alert).toHaveTextContent("The database could not be read.");

    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

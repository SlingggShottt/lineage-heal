import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "@/api/schema";
import { fakeApi, renderApp } from "@/test/render";

type Incident = components["schemas"]["IncidentSummary"];
type Health = components["schemas"]["FeedsHealth"];

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

const HEALTH: Health = {
  as_of: "2026-11-23",
  feeds: [
    {
      source: "amfi",
      dataset: "navall",
      status: "ok",
      latest_trade_date: "2026-11-23",
      lag_days: 0,
      last_attempt_at: "2026-11-23T09:00:00Z",
      last_attempt_outcome: "stored",
      last_error: null,
    },
    {
      source: "nse",
      dataset: "cm_bhavcopy",
      status: "late",
      latest_trade_date: "2026-11-19",
      lag_days: 4,
      last_attempt_at: "2026-11-19T09:00:00Z",
      last_attempt_outcome: "stored",
      last_error: null,
    },
    {
      source: "nse",
      dataset: "security_master",
      status: "failing",
      latest_trade_date: "2026-11-22",
      lag_days: 1,
      last_attempt_at: "2026-11-23T09:00:00Z",
      last_attempt_outcome: "failed",
      last_error: "HTTP 403 from the archive",
    },
    {
      source: "nse",
      dataset: "corporate_actions",
      status: "no_data",
      latest_trade_date: null,
      lag_days: null,
      last_attempt_at: null,
      last_attempt_outcome: null,
      last_error: null,
    },
  ],
  latest_run: {
    run_id: "run-2026-11-23",
    status: "failed",
    generated_at: "2026-11-23T09:30:00Z",
    errors: 1,
    warnings: 2,
    skipped_models: 1,
  },
};

/** The incident list answers by the `status` filter, as the real API does. */
function incidentsByStatus(byStatus: Record<string, Incident[]>) {
  return (url: URL) => {
    const items = byStatus[url.searchParams.get("status") ?? ""] ?? [];
    return { body: { items, total: items.length, limit: 5, offset: 0 } };
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-23T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the overview", () => {
  it("shows every feed with its status in words, newest day and lag", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/feeds/health": { body: HEALTH },
      "/incidents": incidentsByStatus({}),
    });

    renderApp("/", api.fetch);

    const table = await screen.findByRole("table", { name: "Feed freshness" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(5);
    expect(rows[1]).toHaveTextContent("amfi/navall");
    expect(rows[1]).toHaveTextContent("Up to date");
    expect(rows[1]).toHaveTextContent("current");
    expect(rows[2]).toHaveTextContent("Late");
    expect(rows[2]).toHaveTextContent("4 days behind");
    expect(rows[3]).toHaveTextContent("Failing");
    expect(rows[3]).toHaveTextContent("HTTP 403 from the archive");
    expect(rows[3]).toHaveTextContent("1 day behind");
    expect(rows[4]).toHaveTextContent("No data yet");
  });

  it("shows the latest run, its counts and when it ran", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/feeds/health": { body: HEALTH },
      "/incidents": incidentsByStatus({}),
    });

    renderApp("/", api.fetch);

    const card = (await screen.findByText("Latest pipeline run")).closest("div")?.parentElement;
    expect(await within(card as HTMLElement).findByText("Failed")).toBeInTheDocument();
    expect(card).toHaveTextContent("1 error, 2 warnings, 1 model skipped");
    expect(card).toHaveTextContent("2 hours ago");
    expect(card).toHaveTextContent("run-2026-11-23");
  });

  it("says so when no run has been recorded", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/feeds/health": { body: { ...HEALTH, latest_run: null } },
      "/incidents": incidentsByStatus({}),
    });

    renderApp("/", api.fetch);

    expect(await screen.findByText("No run recorded yet.")).toBeInTheDocument();
  });

  it("counts open incidents across statuses and lists the newest five, each linked", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/feeds/health": { body: HEALTH },
      "/incidents": incidentsByStatus({
        fix_proposed: [incident({ id: 1, opened_at: "2026-11-23T08:00:00Z" })],
        unresolved: [
          incident({ id: 2, status: "unresolved", opened_at: "2026-11-23T10:00:00Z" }),
          incident({ id: 3, status: "unresolved", opened_at: "2026-11-22T10:00:00Z" }),
        ],
        suggestion_filed: [
          incident({ id: 4, status: "suggestion_filed", opened_at: "2026-11-21T10:00:00Z" }),
          incident({ id: 5, status: "suggestion_filed", opened_at: "2026-11-20T10:00:00Z" }),
          incident({ id: 6, status: "suggestion_filed", opened_at: "2026-11-19T10:00:00Z" }),
        ],
      }),
    });

    renderApp("/", api.fetch);

    expect(await screen.findByText("6 open")).toBeInTheDocument();
    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    expect(within(items[0] as HTMLElement).getByText("Unresolved")).toBeInTheDocument();
    expect(items[0]).toHaveTextContent("2 hours ago"); // incident 2, the newest
    const links = within(list).getAllByRole("link");
    expect(links[0]).toHaveAttribute("href", "/incidents/2");
    expect(within(list).queryByText(/^#6/)).not.toBeInTheDocument();
  });

  it("asks only for the statuses that are still open", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/feeds/health": { body: HEALTH },
      "/incidents": incidentsByStatus({}),
    });

    renderApp("/", api.fetch);
    await screen.findByText("0 open");

    const asked = api.seen
      .map((r) => new URL(r.url))
      .filter((u) => u.pathname === "/api/incidents")
      .map((u) => u.searchParams.get("status"))
      .sort();
    expect(asked).toEqual(
      [
        "detected",
        "fix_proposed",
        "fix_unverified",
        "investigating",
        "suggestion_filed",
        "unresolved",
      ].sort(),
    );
  });

  it("says there are no open incidents when there are none", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/feeds/health": { body: HEALTH },
      "/incidents": incidentsByStatus({}),
    });

    renderApp("/", api.fetch);

    expect(await screen.findByText("No open incidents.")).toBeInTheDocument();
  });

  it("keeps the other cards when the feeds request fails, and retries on demand", async () => {
    let failing = true;
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/feeds/health": () =>
        failing
          ? {
              status: 503,
              body: {
                type: "urn:lineageheal:problem:database-unavailable",
                title: "Database unavailable",
                status: 503,
                detail: "The database could not be read.",
              },
            }
          : { body: HEALTH },
      "/incidents": incidentsByStatus({ fix_proposed: [incident()] }),
    });
    const user = userEvent.setup();

    renderApp("/", api.fetch);

    const alerts = await screen.findAllByRole("alert");
    expect(alerts[0]).toHaveTextContent("Feeds could not be loaded");
    expect(alerts[0]).toHaveTextContent("The database could not be read.");
    expect(await screen.findByText("1 open")).toBeInTheDocument();

    failing = false;
    await user.click(within(alerts[0] as HTMLElement).getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("table", { name: "Feed freshness" })).toBeInTheDocument();
  });

  it("links to the full incident list", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/feeds/health": { body: HEALTH },
      "/incidents": incidentsByStatus({}),
    });

    renderApp("/", api.fetch);

    expect(await screen.findByRole("link", { name: "All incidents" })).toHaveAttribute(
      "href",
      "/incidents",
    );
  });
});

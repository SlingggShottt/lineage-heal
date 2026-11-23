import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { components } from "@/api/schema";
import { fakeApi, renderApp } from "@/test/render";

type Detail = components["schemas"]["IncidentDetail"];
type Fix = components["schemas"]["FixDetail"];
type Lineage = components["schemas"]["LineageOut"];

const ANONYMOUS = { body: { signed_in: false, is_approver: false } };
const PROBLEM_404 = (detail: string) => ({
  status: 404,
  body: { type: "urn:lineageheal:problem:not-found", title: "Not found", status: 404, detail },
});

const DIFF = [
  "--- a/dbt/models/bronze/brz_amfi__navall.sql",
  "+++ b/dbt/models/bronze/brz_amfi__navall.sql",
  "@@ -29,7 +29,9 @@",
  '-        try_cast(trim("Net Asset Value") as decimal(18, 4)) as nav_inr,',
  '+        try_cast(coalesce(trim("NAV"), trim("Net Asset Value")) as decimal(18, 4)) as nav_inr,',
  "         trade_date::date as _trade_date,",
  "",
].join("\n");

function detail(overrides: Partial<Detail> = {}): Detail {
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
    root_cause: {
      node: "raw.amfi.navall.Net Asset Value",
      explanation: "AMFI publishes the NAV under the header NAV instead of Net Asset Value.",
      confidence: 0.93,
      fix_location: "dbt",
      fix_reason: "Bronze can read the new header while still reading the old one.",
      partial: false,
      evidence_ids: [1, 3],
      alternatives: [
        {
          node: "raw.amfi.navall.Date",
          fault_class: "nulls",
          explanation: "The date column could be blank.",
          confidence: 0.2,
        },
      ],
    },
    affected: {
      models: ["brz_amfi__navall"],
      columns: ["brz_amfi__navall.nav_inr"],
      gold_tables: ["gld_mf_nav_daily"],
    },
    signals: [
      {
        kind: "test_failed",
        severity: "error",
        name: "not_null_brz_amfi__navall_nav_inr",
        check_type: "not_null",
        model: "brz_amfi__navall",
        column: "nav_inr",
        failures: 8742,
        message: "Got 8742 results, configured to fail\nmore detail",
        detected_at: "2026-11-23T08:34:00Z",
      },
    ],
    investigation: {
      status: "succeeded",
      started_at: "2026-11-23T08:41:15Z",
      finished_at: "2026-11-23T08:44:00Z",
      llm_calls: 0,
      input_tokens: 0,
      output_tokens: 0,
      models: { seed: "seed" },
      error: null,
      trace_url: null,
      evidence: [
        {
          number: 1,
          tool: "get_test_failure",
          arguments: { test: "not_null_brz_amfi__navall_nav_inr" },
          output: { failures: 8742 },
          hypothesis: null,
          collected_at: "2026-11-23T08:41:30Z",
        },
        {
          number: 2,
          tool: "get_lineage",
          arguments: { node: "brz_amfi__navall.nav_inr" },
          output: { reached: 1 },
          hypothesis: null,
          collected_at: "2026-11-23T08:41:40Z",
        },
        {
          number: 3,
          tool: "compare_schemas",
          arguments: { source: "amfi", dataset: "navall" },
          output: { removed: ["Net Asset Value"], added: ["NAV"] },
          hypothesis: "supports raw.amfi.navall.Net Asset Value",
          collected_at: "2026-11-23T08:41:50Z",
        },
      ],
    },
    similar_incidents: [],
    related_runbooks: [
      {
        source: "runbook",
        document: "schema-drift",
        heading: "Diagnosis",
        fault_class: "schema_drift",
        score: 0.0328,
        text: "A publisher renames a column\nDiagnosis\n\n1. Compare the **header**.\n- Check `NAV`.",
      },
    ],
    fixes: [
      {
        id: 1,
        kind: "dbt_patch",
        status: "published",
        files: ["dbt/models/bronze/brz_amfi__navall.sql"],
        sandbox_passed: true,
        github_url: "https://github.com/acme/pipeline/pull/101",
        github_number: 101,
        created_at: "2026-11-23T08:47:00Z",
      },
    ],
    decision: null,
    ...overrides,
  };
}

const patchFix: Fix = {
  id: 1,
  incident_id: 12,
  kind: "dbt_patch",
  status: "published",
  files: ["dbt/models/bronze/brz_amfi__navall.sql"],
  sandbox_passed: true,
  github_url: "https://github.com/acme/pipeline/pull/101",
  github_number: 101,
  created_at: "2026-11-23T08:47:00Z",
  updated_at: "2026-11-23T08:48:00Z",
  patch: DIFF,
  branch: "heal/12",
  suggestion: null,
  sandbox: {
    passed: true,
    reason: "fixed 1 failing node(s) and broke nothing",
    selector: "brz_amfi__navall+",
    fixed: ["test.lineageheal.not_null_brz_amfi__navall_nav_inr.5b0d921a09"],
    still_failing: [],
    regressions: [],
    before: {},
    after: {},
    seconds: 41.8,
  },
};

const lineage: Lineage = {
  version: "f0fce32bb6c6cb21",
  root_node: "raw.amfi.navall.Net Asset Value",
  truncated: false,
  unreached: [],
  nodes: [
    {
      id: "gld_mf_nav_daily.nav_inr",
      table: "gld_mf_nav_daily",
      column: "nav_inr",
      kind: "model",
      role: "affected",
    },
    {
      id: "raw.amfi.navall.Net Asset Value",
      table: "raw.amfi.navall",
      column: "Net Asset Value",
      kind: "raw",
      role: "root",
    },
    {
      id: "brz_amfi__navall.nav_inr",
      table: "brz_amfi__navall",
      column: "nav_inr",
      kind: "model",
      role: "affected",
    },
  ],
  edges: [
    {
      source: "brz_amfi__navall.nav_inr",
      target: "gld_mf_nav_daily.nav_inr",
      kind: "direct",
      inferred: false,
    },
    {
      source: "raw.amfi.navall.Net Asset Value",
      target: "brz_amfi__navall.nav_inr",
      kind: "transform",
      inferred: false,
    },
  ],
};

const firstAttempt = () => detail().fixes[0] as components["schemas"]["FixSummary"];

function everything() {
  return fakeApi({
    "/me": ANONYMOUS,
    "/incidents/12": { body: detail() },
    "/incidents/12/lineage": { body: lineage },
    "/incidents/12/fix": { body: patchFix },
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-23T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the incident page", () => {
  it("shows what the incident is, with its state and confidence", async () => {
    renderApp("/incidents/12", everything().fetch);

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("not_null on brz_amfi__navall.nav_inr (8742 rows)");
    const header = heading.closest("header") as HTMLElement;
    expect(header).toHaveTextContent("Incident #12");
    expect(header).toHaveTextContent("Fix proposed");
    expect(header).toHaveTextContent("Error");
    expect(header).toHaveTextContent("Schema drift");
    expect(header).toHaveTextContent("Confidence 93%");
    expect(header).toHaveTextContent("Classified by investigator");
    expect(header).toHaveTextContent("Opened 3 hours ago");
    expect(screen.getByRole("link", { name: "← All incidents" })).toHaveAttribute(
      "href",
      "/incidents",
    );
  });

  it("explains the root cause, where the fix belongs and what supports it", async () => {
    renderApp("/incidents/12", everything().fetch);

    const card = (await screen.findByRole("heading", { name: "Root cause" })).closest(
      "div[class*='rounded']",
    ) as HTMLElement;
    expect(card).toHaveTextContent("AMFI publishes the NAV under the header NAV");
    expect(card).toHaveTextContent("raw.amfi.navall.Net Asset Value");
    expect(card).toHaveTextContent("In dbt");
    expect(card).toHaveTextContent("Bronze can read the new header");
    expect(within(card).getByRole("link", { name: "Evidence 1" })).toHaveAttribute(
      "href",
      "#evidence-1",
    );
    expect(within(card).getByRole("link", { name: "Evidence 3" })).toHaveAttribute(
      "href",
      "#evidence-3",
    );
    expect(card).toHaveTextContent("Other causes considered");
    expect(card).toHaveTextContent("The date column could be blank.");
  });

  it("lists the signals the Detector grouped", async () => {
    renderApp("/incidents/12", everything().fetch);

    const heading = await screen.findByRole("heading", { name: "Signals" });
    const card = heading.closest("div[class*='rounded']") as HTMLElement;
    expect(card).toHaveTextContent("Test failed · not_null · 8742 rows");
    expect(card).toHaveTextContent("brz_amfi__navall.nav_inr");
    expect(card).toHaveTextContent("Got 8742 results, configured to fail");
    expect(card).not.toHaveTextContent("more detail"); // only the first line
  });

  it("numbers the evidence, marks what the conclusion cites, and opens a row on request", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderApp("/incidents/12", everything().fetch);

    const items = await screen
      .findAllByRole("listitem", { name: "" })
      .then(() => document.querySelectorAll<HTMLElement>("li[id^='evidence-']"));
    expect([...items].map((item) => item.id)).toEqual(["evidence-1", "evidence-2", "evidence-3"]);
    expect(items[0]).toHaveTextContent("get_test_failure");
    expect(items[0]).toHaveTextContent("Cited");
    expect(items[1]).not.toHaveTextContent("Cited");
    expect(items[2]).toHaveTextContent("supports raw.amfi.navall.Net Asset Value");

    const details = items[2]?.querySelector("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    await user.click(within(items[2] as HTMLElement).getByText("compare_schemas"));
    expect(details.open).toBe(true);
    expect(details).toHaveTextContent('"removed": [');
    expect(details).toHaveTextContent("Net Asset Value");
  });

  it("shows the lineage as an ordered path from the root, with what each step feeds", async () => {
    renderApp("/incidents/12", everything().fetch);

    const heading = await screen.findByRole("heading", { name: "Lineage" });
    const card = heading.closest("div[class*='rounded']") as HTMLElement;
    const steps = await within(card).findAllByRole("listitem");
    const first = steps[0] as HTMLElement;
    expect(first).toHaveTextContent("raw.amfi.navall.Net Asset Value");
    expect(first).toHaveTextContent("Root cause");
    expect(first).toHaveTextContent("feeds brz_amfi__navall.nav_inr (transform)");
    expect(card).toHaveTextContent("Graph version f0fce32bb6c6cb21");
    const ids = [...card.querySelectorAll("ol > li > p > code")].map((code) => code.textContent);
    expect(ids).toEqual([
      "raw.amfi.navall.Net Asset Value",
      "brz_amfi__navall.nav_inr",
      "gld_mf_nav_daily.nav_inr",
    ]);
  });

  it("draws the lineage as a graph above the list, with the path still listed as text", async () => {
    renderApp("/incidents/12", everything().fetch);

    const heading = await screen.findByRole("heading", { name: "Lineage" });
    const card = heading.closest("div[class*='rounded']") as HTMLElement;
    expect(await within(card).findByLabelText("Lineage graph")).toBeInTheDocument();
    expect(within(card).getByText("The path as a list (3 steps)")).toBeInTheDocument();
    expect(within(card).getAllByText("Root cause").length).toBeGreaterThan(0);
  });

  it("shows the fix: the pull request, the files, the sandbox verdict and the patch", async () => {
    renderApp("/incidents/12", everything().fetch);

    const heading = await screen.findByRole("heading", { name: "Proposed fix" });
    const card = heading.closest("div[class*='rounded']") as HTMLElement;
    expect(await within(card).findByRole("link", { name: "Pull request #101" })).toHaveAttribute(
      "href",
      "https://github.com/acme/pipeline/pull/101",
    );
    expect(card).toHaveTextContent("Published");
    expect(card).toHaveTextContent("dbt/models/bronze/brz_amfi__navall.sql");
    expect(card).toHaveTextContent("Passed the sandbox");
    expect(card).toHaveTextContent("fixed 1 failing node(s) and broke nothing");
    expect(card).toHaveTextContent("not_null_brz_amfi__navall_nav_inr"); // without the id's prefix and hash
    expect(card).not.toHaveTextContent("5b0d921a09");
    // Side by side is the default: the old line on the left, its replacement on the right.
    const patch = within(card).getByRole("region", { name: "Patch, side by side" });
    expect(patch).toHaveAttribute("tabindex", "0");
    const changed = within(patch)
      .getAllByRole("row")
      .find((row) => row.textContent.includes("Net Asset Value"));
    const cells = changed
      ? within(changed)
          .getAllByRole("cell")
          .filter((cell) => cell.textContent.includes("try_cast"))
      : [];
    expect(cells[0]).toHaveTextContent('Removed: try_cast(trim("Net Asset Value")');
    expect(cells[1]).toHaveTextContent('Added: try_cast(coalesce(trim("NAV")');
    expect(card).toHaveTextContent("+1 −1");
  });

  it("switches the patch to the plain unified form and back", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderApp("/incidents/12", everything().fetch);
    const group = await screen.findByRole("group", { name: "Diff layout" });
    const side = within(group).getByRole("button", { name: "Side by side" });
    const unified = within(group).getByRole("button", { name: "Unified" });
    expect(side).toHaveAttribute("aria-pressed", "true");
    expect(unified).toHaveAttribute("aria-pressed", "false");

    await user.click(unified);

    const patch = screen.getByLabelText("Patch");
    expect(patch).toHaveTextContent('- try_cast(trim("Net Asset Value")');
    expect(patch).toHaveTextContent('+ try_cast(coalesce(trim("NAV")');
    expect(unified).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("region", { name: "Patch, side by side" })).not.toBeInTheDocument();

    await user.click(side);

    expect(screen.getByRole("region", { name: "Patch, side by side" })).toBeInTheDocument();
  });

  it("shows the runbook sections the Investigator found, as short excerpts", async () => {
    renderApp("/incidents/12", everything().fetch);

    const heading = await screen.findByRole("heading", { name: "Related knowledge" });
    const card = heading.closest("div[class*='rounded']") as HTMLElement;
    expect(card).toHaveTextContent("schema-drift · Diagnosis");
    expect(card).toHaveTextContent("1. Compare the header. • Check NAV.");
    expect(card).not.toHaveTextContent("A publisher renames a column"); // the repeated title
    expect(card).not.toHaveTextContent("**");
    expect(card).not.toHaveTextContent("Similar past incidents"); // none exist to cite
  });

  it("says one row, not one rows", async () => {
    const body = detail();
    body.signals[0] = { ...(body.signals[0] as Detail["signals"][number]), failures: 1 };
    const single = fakeApi({
      "/me": ANONYMOUS,
      "/incidents/12": { body },
      "/incidents/12/lineage": { body: lineage },
      "/incidents/12/fix": { body: patchFix },
    });

    renderApp("/incidents/12", single.fetch);

    expect(await screen.findByText("Test failed · not_null · 1 row")).toBeInTheDocument();
  });

  it("is honest about who made the diagnosis: no model calls, no trace link", async () => {
    renderApp("/incidents/12", everything().fetch);

    const heading = await screen.findByRole("heading", { name: "Investigation" });
    const card = heading.closest("div[class*='rounded']") as HTMLElement;
    expect(card).toHaveTextContent("Model calls0");
    expect(card).toHaveTextContent("seed: seed");
    expect(within(card).queryByRole("link")).not.toBeInTheDocument();
  });

  it("has a heading outline: one h1, and the cards as h2", async () => {
    renderApp("/incidents/12", everything().fetch);

    await screen.findByRole("heading", { level: 1 });
    await screen.findByRole("link", { name: "Pull request #101" });

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "Lineage",
      "Root cause",
      "Signals",
      "Proposed fix",
      "Evidence",
      "Related knowledge",
      "Investigation",
    ]);
  });
});

describe("what the page says when something is missing", () => {
  it("says an incident is not found, and asks for nothing else", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/incidents/999": PROBLEM_404("There is no incident 999."),
    });

    renderApp("/incidents/999", api.fetch);

    expect(await screen.findByRole("heading", { name: "Incident not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to all incidents" })).toHaveAttribute(
      "href",
      "/incidents",
    );
    const paths = api.seen.map((request) => new URL(request.url).pathname);
    expect(paths).toEqual(expect.arrayContaining(["/api/incidents/999"]));
    expect(paths.filter((path) => path.includes("lineage") || path.includes("/fix"))).toEqual([]);
  });

  it.each(["abc", "0", "-4"])(
    "does not ask the API about an address like /incidents/%s",
    async (id) => {
      const api = fakeApi({ "/me": ANONYMOUS });

      renderApp(`/incidents/${id}`, api.fetch);

      expect(
        await screen.findByRole("heading", { name: "Incident not found" }),
      ).toBeInTheDocument();
      expect(api.seen.map((request) => new URL(request.url).pathname)).not.toContain(
        `/api/incidents/${id}`,
      );
    },
  );

  it("says there is no diagnosis or investigation yet instead of leaving gaps", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/incidents/12": {
        body: detail({
          root_cause: null,
          investigation: null,
          signals: [],
          related_runbooks: [],
          fixes: [],
        }),
      },
      "/incidents/12/lineage": PROBLEM_404("The incident has no root cause node yet."),
      "/incidents/12/fix": PROBLEM_404("Incident 12 has no fix yet."),
    });

    renderApp("/incidents/12", api.fetch);

    expect(await screen.findByText(/No diagnosis yet/)).toBeInTheDocument();
    expect(screen.getByText("Not investigated yet.")).toBeInTheDocument();
    expect(await screen.findByText("No fix has been proposed yet.")).toBeInTheDocument();
    expect(
      await screen.findByText(/No lineage can be drawn for this incident/),
    ).toBeInTheDocument();
    expect(screen.getByText(/The incident has no root cause node yet\./)).toBeInTheDocument();
    expect(screen.getByText("No signals are attached to this incident.")).toBeInTheDocument();
    expect(screen.getByText("No evidence was recorded.")).toBeInTheDocument();
    expect(screen.getByText("Nothing similar was found.")).toBeInTheDocument();
  });

  it("warns that a best guess is only that", async () => {
    const guess = detail().root_cause;
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/incidents/12": { body: detail({ root_cause: guess && { ...guess, partial: true } }) },
      "/incidents/12/lineage": { body: lineage },
      "/incidents/12/fix": PROBLEM_404("none"),
    });

    renderApp("/incidents/12", api.fetch);

    expect(await screen.findByRole("note")).toHaveTextContent("Best guess only");
  });

  it("shows only the root, and says why, for a cause that feeds no column", async () => {
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/incidents/12": { body: detail() },
      "/incidents/12/lineage": {
        body: {
          ...lineage,
          root_node: "raw.amfi.navall.*",
          nodes: [
            {
              id: "raw.amfi.navall.*",
              table: "raw.amfi.navall",
              column: "*",
              kind: "raw",
              role: "root",
            },
          ],
          edges: [],
          unreached: ["gld_mf_nav_daily", "gld_market_daily_summary"],
        },
      },
      "/incidents/12/fix": { body: patchFix },
    });

    renderApp("/incidents/12", api.fetch);

    expect(
      await screen.findByText(/No path from the root cause to the affected tables/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Lineage graph")).not.toBeInTheDocument();
    expect(screen.queryByText(/The path as a list/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/Not reached from the root: gld_mf_nav_daily, gld_market_daily_summary/),
    ).toBeInTheDocument();
  });

  it("explains a failure to load, and tries again on request", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    let failures = 2; // the first attempt and the automatic retry
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/incidents/12": () =>
        failures-- > 0
          ? {
              status: 503,
              body: {
                type: "x",
                title: "Database unavailable",
                status: 503,
                detail: "The database could not be read.",
              },
            }
          : { body: detail() },
      "/incidents/12/lineage": { body: lineage },
      "/incidents/12/fix": { body: patchFix },
    });
    renderApp("/incidents/12", api.fetch);

    // the page retries once before giving up, after a short pause
    const alert = await screen.findByRole("alert", {}, { timeout: 4000 });
    expect(alert).toHaveTextContent("The incident could not be loaded");
    expect(alert).toHaveTextContent("The database could not be read.");

    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      "not_null on brz_amfi",
    );
  });
});

describe("fixes of other kinds", () => {
  it("shows a suggestion with its draft and the issue it was filed as", async () => {
    const suggestion: Fix = {
      ...patchFix,
      kind: "suggestion",
      status: "published",
      files: [],
      patch: null,
      sandbox: null,
      github_url: "https://github.com/acme/pipeline/issues/102",
      github_number: 102,
      suggestion: {
        title: "Download the AMFI report again and retry on HTTP 503",
        target: "The AMFI loader",
        change: "Run the download again once the portal answers.",
        draft: "uv run lineageheal ingest amfi --date 2026-11-20",
        verify: "lineageheal log shows a stored attempt.",
        risks: "AMFI may republish the day.",
      },
    };
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/incidents/12": { body: detail() },
      "/incidents/12/lineage": { body: lineage },
      "/incidents/12/fix": { body: suggestion },
    });

    renderApp("/incidents/12", api.fetch);

    const heading = await screen.findByRole("heading", { name: "Proposed fix" });
    const card = heading.closest("div[class*='rounded']") as HTMLElement;
    expect(await within(card).findByRole("link", { name: "Issue #102" })).toHaveAttribute(
      "href",
      "https://github.com/acme/pipeline/issues/102",
    );
    expect(card).toHaveTextContent("Download the AMFI report again and retry on HTTP 503");
    expect(card).toHaveTextContent("Run the download again once the portal answers.");
    expect(card).toHaveTextContent("uv run lineageheal ingest amfi --date 2026-11-20");
    expect(card).toHaveTextContent("AMFI may republish the day.");
    expect(within(card).queryByLabelText("Patch")).not.toBeInTheDocument();
    expect(within(card).queryByText("Files changed")).not.toBeInTheDocument();
  });

  it("lists every attempt and shows why the latest one failed", async () => {
    const failed: Fix = {
      ...patchFix,
      status: "unverified",
      github_url: null,
      github_number: null,
      sandbox: {
        ...(patchFix.sandbox as NonNullable<Fix["sandbox"]>),
        passed: false,
        reason: "the patch breaks 1 node(s) that passed before",
        fixed: [],
        regressions: ["test.lineageheal.assert_brz_nse__cm_bhavcopy_prices_within_day_range"],
      },
    };
    const api = fakeApi({
      "/me": ANONYMOUS,
      "/incidents/12": {
        body: detail({
          fixes: [
            {
              ...firstAttempt(),
              id: 1,
              status: "unverified",
              sandbox_passed: false,
              github_url: null,
              github_number: null,
            },
            {
              ...firstAttempt(),
              id: 2,
              status: "unverified",
              sandbox_passed: false,
              github_url: null,
              github_number: null,
            },
          ],
        }),
      },
      "/incidents/12/lineage": { body: lineage },
      "/incidents/12/fix": { body: failed },
    });

    renderApp("/incidents/12", api.fetch);

    const heading = await screen.findByRole("heading", { name: "Proposed fix" });
    const card = heading.closest("div[class*='rounded']") as HTMLElement;
    expect(await within(card).findByText("Failed the sandbox")).toBeInTheDocument();
    expect(card).toHaveTextContent("the patch breaks 1 node(s) that passed before");
    expect(card).toHaveTextContent("Newly broken");
    expect(card).toHaveTextContent("assert_brz_nse__cm_bhavcopy_prices_within_day_range");
    expect(card).toHaveTextContent("Attempt 1: failed the sandbox");
    expect(card).toHaveTextContent("Attempt 2: failed the sandbox");
    expect(within(card).queryByRole("link", { name: /Pull request/ })).not.toBeInTheDocument();
  });
});

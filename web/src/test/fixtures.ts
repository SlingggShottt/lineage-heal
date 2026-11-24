// Incident data shaped like the API's answers, shared by the page tests.

import type { components } from "@/api/schema";

export type Detail = components["schemas"]["IncidentDetail"];
export type Fix = components["schemas"]["FixDetail"];
export type Lineage = components["schemas"]["LineageOut"];

export const ANONYMOUS = { body: { signed_in: false, is_approver: false } };
export const PROBLEM_404 = (detail: string) => ({
  status: 404,
  body: { type: "urn:lineageheal:problem:not-found", title: "Not found", status: 404, detail },
});

export const DIFF = [
  "--- a/dbt/models/bronze/brz_amfi__navall.sql",
  "+++ b/dbt/models/bronze/brz_amfi__navall.sql",
  "@@ -29,7 +29,9 @@",
  '-        try_cast(trim("Net Asset Value") as decimal(18, 4)) as nav_inr,',
  '+        try_cast(coalesce(trim("NAV"), trim("Net Asset Value")) as decimal(18, 4)) as nav_inr,',
  "         trade_date::date as _trade_date,",
  "",
].join("\n");

export function detail(overrides: Partial<Detail> = {}): Detail {
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

export const patchFix: Fix = {
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

export const lineage: Lineage = {
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

export const firstAttempt = () => detail().fixes[0] as components["schemas"]["FixSummary"];

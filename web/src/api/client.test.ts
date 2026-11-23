import { describe, expect, it } from "vitest";

import { fakeApi } from "@/test/render";

import { ApiProblem, createApiClient, unwrap } from "./client";

describe("the API client", () => {
  it("calls the API under /api with same-origin credentials", async () => {
    const api = fakeApi({ "/me": { body: { signed_in: false, is_approver: false } } });

    const me = unwrap(await createApiClient(api.fetch).GET("/me"));

    expect(me.signed_in).toBe(false);
    const request = api.seen[0];
    expect(new URL(request?.url ?? "").pathname).toBe("/api/me");
    expect(request?.credentials).toBe("same-origin");
  });

  it("turns an error answer into an ApiProblem that keeps the problem details", async () => {
    const problem = {
      type: "urn:lineageheal:problem:database-unavailable",
      title: "Database unavailable",
      status: 503,
      detail: "No database is configured.",
    };
    const api = fakeApi({ "/me": { status: 503, body: problem } });

    const attempt = async () => unwrap(await createApiClient(api.fetch).GET("/me"));

    await expect(attempt()).rejects.toMatchObject({
      name: "ApiProblem",
      status: 503,
      message: "No database is configured.",
      problem,
    });
    await expect(attempt()).rejects.toBeInstanceOf(ApiProblem);
  });
});

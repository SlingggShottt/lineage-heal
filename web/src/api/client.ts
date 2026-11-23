import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

/** The API's error body: RFC 9457 problem details. */
export type Problem = components["schemas"]["Problem"];

/** An error answer from the API, keeping its problem details for the screen to show. */
export class ApiProblem extends Error {
  readonly problem: Problem | undefined;
  readonly status: number;

  constructor(status: number, problem: Problem | undefined) {
    super(problem?.detail ?? problem?.title ?? `The API answered ${String(status)}`);
    this.name = "ApiProblem";
    this.status = status;
    this.problem = problem;
  }
}

export type ApiClient = ReturnType<typeof createApiClient>;

/**
 * Build the typed API client.
 *
 * Calls go to `/api`, which the dev server and the production host proxy to the API, so the
 * session cookie travels on a same-origin request (D-60).
 *
 * @param fetchImplementation - Replaces `fetch`; tests use it to fake the API.
 */
export function createApiClient(fetchImplementation?: typeof fetch) {
  return createClient<paths>({
    // Absolute, because `Request` (used by openapi-fetch) cannot resolve a relative address
    // outside a browser, and tests run in Node.
    baseUrl: new URL("/api", globalThis.location.href).href.replace(/\/$/, ""),
    credentials: "same-origin",
    ...(fetchImplementation ? { fetch: fetchImplementation } : {}),
  });
}

/** Unwrap an openapi-fetch result, turning an error answer into an `ApiProblem`. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || result.data === undefined) {
    throw new ApiProblem(result.response.status, result.error as Problem | undefined);
  }
  return result.data;
}

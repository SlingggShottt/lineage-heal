import { ApiProblem } from "@/api/client";

/** What to tell an approver when a decision did not go through, and whether to offer sign-in. */
export type Failure = {
  title: string;
  detail: string;
  signInAgain: boolean;
};

const PROBLEM_PREFIX = "urn:lineageheal:problem:";

/**
 * Turn whatever went wrong into words an approver can act on.
 *
 * The API's problems already carry a plain title and a detail (GitHub's own reason for a refused
 * merge, for example), so most are shown as they are. Only the ones that call for something else,
 * signing in again or reloading, are reworded.
 */
export function describeFailure(error: unknown): Failure {
  if (!(error instanceof ApiProblem)) {
    return {
      title: "The API could not be reached",
      detail: "Check your connection and try again.",
      signInAgain: false,
    };
  }
  const slug = error.problem?.type.replace(PROBLEM_PREFIX, "") ?? "";
  if (slug === "github-token-unusable" || slug === "unauthenticated") {
    return {
      title: "Sign in again",
      detail: error.problem?.detail ?? "Your sign-in can no longer be used.",
      signInAgain: true,
    };
  }
  if (slug === "csrf-failed") {
    return {
      title: "Your session needs a refresh",
      detail: "Reload the page and try again.",
      signInAgain: false,
    };
  }
  return {
    title: error.problem?.title ?? "The decision did not go through",
    detail: error.problem?.detail ?? error.message,
    signInAgain: false,
  };
}

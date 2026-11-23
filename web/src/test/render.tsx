import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter } from "react-router";
import { RouterProvider } from "react-router/dom";

import { createApiClient } from "@/api/client";
import { ApiClientContext } from "@/api/context";
import { routes } from "@/routes";

type Answer = { status?: number; body: unknown };

/** A fake `fetch` that answers by path and records the requests it saw. */
export function fakeApi(answers: Record<string, Answer>) {
  const seen: Request[] = [];
  const fetchFake = (input: Request | string | URL): Promise<Response> => {
    const request = input instanceof Request ? input : new Request(input);
    seen.push(request);
    const path = new URL(request.url).pathname.replace(/^\/api/, "");
    const answer = answers[path];
    if (!answer) {
      return Promise.resolve(new Response("no such fake route", { status: 599 }));
    }
    return Promise.resolve(
      new Response(JSON.stringify(answer.body), {
        status: answer.status ?? 200,
        headers: { "content-type": "application/json" },
      }),
    );
  };
  return { fetch: fetchFake, seen };
}

/** Mount the app's routes at `path`, with queries answered by `fetchFake`. */
export function renderApp(path: string, fetchFake: typeof fetch) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <ApiClientContext value={createApiClient(fetchFake)}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ApiClientContext>,
  );
}

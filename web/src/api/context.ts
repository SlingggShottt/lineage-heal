import { createContext, useContext } from "react";

import { createApiClient, type ApiClient } from "./client";

/** The client every query uses. Tests provide one wired to a fake `fetch`. */
export const ApiClientContext = createContext<ApiClient>(createApiClient());

export function useApiClient(): ApiClient {
  return useContext(ApiClientContext);
}

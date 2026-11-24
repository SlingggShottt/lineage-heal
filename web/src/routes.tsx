import type { RouteObject } from "react-router";

import { AppShell } from "@/layout/AppShell";
import { IncidentPage } from "@/pages/IncidentPage";
import { IncidentsPage } from "@/pages/IncidentsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { OverviewPage } from "@/pages/OverviewPage";
import { RunbooksPage } from "@/pages/RunbooksPage";

/** The route table, exported on its own so tests can mount it in a memory router. */
export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "incidents", element: <IncidentsPage /> },
      { path: "incidents/:id", element: <IncidentPage /> },
      { path: "runbooks", element: <RunbooksPage /> },
      { path: "runbooks/:slug", element: <RunbooksPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

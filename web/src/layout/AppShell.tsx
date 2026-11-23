import { NavLink, Outlet } from "react-router";

import { SessionBadge } from "@/features/session/SessionBadge";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/incidents", label: "Incidents", end: false },
  { to: "/runbooks", label: "Runbooks", end: false },
] as const;

/** The frame around every page: skip link, header with navigation and session, main area. */
export function AppShell() {
  return (
    <div className="min-h-screen">
      <a
        href="#content"
        className="bg-primary text-primary-foreground sr-only rounded-md px-3 py-2 focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <header className="border-b">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-3">
          <div className="flex items-center gap-6">
            <span className="text-lg font-semibold">LineageHeal</span>
            <nav aria-label="Main" className="flex gap-1">
              {NAV.map(({ to, label, end }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    cn(
                      "rounded-md px-3 py-1.5 text-sm font-medium",
                      isActive
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )
                  }
                >
                  {label}
                </NavLink>
              ))}
            </nav>
          </div>
          <SessionBadge />
        </div>
      </header>
      <main id="content" className="mx-auto max-w-6xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}

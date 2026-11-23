import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

/** A grey placeholder shown while data loads. Hidden from screen readers. */
export function Skeleton({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      aria-hidden="true"
      className={cn("bg-muted animate-pulse rounded-md", className)}
      {...props}
    />
  );
}

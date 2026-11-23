import { useState } from "react";

import { Button } from "@/components/ui/button";

import { countChanges, parseUnifiedDiff } from "./diff";
import { SideBySideDiff } from "./SideBySideDiff";
import { UnifiedDiff } from "./UnifiedDiff";

type Mode = "side" | "unified";

/** The patch, with a switch between side by side (the default) and the plain unified form. */
export function DiffView({ diff }: { diff: string }) {
  const [mode, setMode] = useState<Mode>("side");
  const { added, removed } = countChanges(parseUnifiedDiff(diff));
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">
          Patch{" "}
          <span className="text-muted-foreground text-xs font-normal">
            +{added} −{removed}
          </span>
        </h3>
        <div role="group" aria-label="Diff layout" className="flex gap-1">
          <Button
            variant={mode === "side" ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={mode === "side"}
            onClick={() => {
              setMode("side");
            }}
          >
            Side by side
          </Button>
          <Button
            variant={mode === "unified" ? "secondary" : "ghost"}
            size="sm"
            aria-pressed={mode === "unified"}
            onClick={() => {
              setMode("unified");
            }}
          >
            Unified
          </Button>
        </div>
      </div>
      {mode === "side" ? <SideBySideDiff diff={diff} /> : <UnifiedDiff diff={diff} />}
    </div>
  );
}

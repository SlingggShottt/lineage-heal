import { cn } from "@/lib/utils";

type LineKind = "file" | "hunk" | "add" | "remove" | "context";

function kindOf(line: string): LineKind {
  if (line.startsWith("+++") || line.startsWith("---")) {
    return "file";
  }
  if (line.startsWith("@@")) {
    return "hunk";
  }
  if (line.startsWith("+")) {
    return "add";
  }
  if (line.startsWith("-")) {
    return "remove";
  }
  return "context";
}

const STYLES: Record<LineKind, string> = {
  file: "text-muted-foreground font-semibold",
  hunk: "text-primary bg-accent",
  add: "bg-success/10 text-success",
  remove: "bg-destructive/10 text-destructive",
  context: "",
};

/**
 * A patch as git prints it, with added and removed lines tinted. The + and - stay visible, so
 * the meaning does not depend on colour. The side-by-side view replaces this in LH-102.
 */
export function UnifiedDiff({ diff }: { diff: string }) {
  const lines = diff.replace(/\n$/, "").split("\n");
  return (
    <pre
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a scrollable region must be reachable by keyboard
      tabIndex={0}
      aria-label="Patch"
      className="bg-muted overflow-x-auto rounded-md py-2 text-xs"
    >
      {lines.map((line, index) => (
        // The diff never changes while shown, so position is a stable key.
        <span key={index} className={cn("block px-3 whitespace-pre", STYLES[kindOf(line)])}>
          {line === "" ? " " : line}
        </span>
      ))}
    </pre>
  );
}

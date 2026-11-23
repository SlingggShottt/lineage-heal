import { cn } from "@/lib/utils";

import { countChanges, pairRows, parseUnifiedDiff, type DiffLine } from "./diff";
import { UnifiedDiff } from "./UnifiedDiff";

const SIGNS = { add: "+", remove: "−", context: " " } as const;
const SPOKEN = { add: "Added: ", remove: "Removed: ", context: "" } as const;

function NumberCell({ value }: { value: number | null }) {
  return (
    <td className="text-muted-foreground w-12 min-w-12 px-2 text-right align-top tabular-nums select-none">
      {value}
    </td>
  );
}

function CodeCell({ line, side }: { line: DiffLine | null; side: "left" | "right" }) {
  // An empty cell opposite a longer run: striped so it reads as "nothing here", not as a gap.
  if (line === null) {
    return <td aria-hidden="true" className="bg-muted/60 w-1/2" />;
  }
  const changed = line.kind !== "context";
  return (
    <td
      className={cn(
        "w-1/2 px-2 align-top break-words whitespace-pre-wrap",
        changed && side === "left" && "bg-destructive/10 text-destructive",
        changed && side === "right" && "bg-success/10 text-success",
      )}
    >
      <span aria-hidden="true" className="inline-block w-4 select-none">
        {SIGNS[line.kind]}
      </span>
      <span className="sr-only">{SPOKEN[line.kind]}</span>
      {line.text === "" ? " " : line.text}
    </td>
  );
}

/**
 * A patch with the old file on the left and the new one on the right, one block per file and
 * hunk. Changed lines are paired so each sits opposite its replacement. The + and − signs and
 * spoken labels carry the meaning that the tint only reinforces.
 */
export function SideBySideDiff({ diff }: { diff: string }) {
  const files = parseUnifiedDiff(diff);
  if (files.length === 0) {
    // Text that is not a unified diff is shown as it is rather than as an empty view.
    return <UnifiedDiff diff={diff} />;
  }
  return (
    <div
      role="region"
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a scrollable region must be reachable by keyboard
      tabIndex={0}
      aria-label="Patch, side by side"
      className="bg-muted flex flex-col gap-4 overflow-x-auto rounded-md py-2 font-mono text-xs"
    >
      {files.map((file) => {
        const { added, removed } = countChanges([file]);
        return (
          <section key={`${file.oldPath}->${file.newPath}`} className="min-w-[44rem]">
            <h3 className="px-3 pb-1 font-sans text-xs font-semibold">
              {file.newPath || file.oldPath}{" "}
              <span className="text-muted-foreground font-normal">
                +{added} −{removed}
              </span>
            </h3>
            {file.hunks.map((hunk) => (
              <table key={hunk.header} className="w-full table-fixed border-collapse">
                <caption className="sr-only">
                  Changes in {file.newPath || file.oldPath}, {hunk.header}
                </caption>
                <colgroup>
                  <col className="w-12" />
                  <col />
                  <col className="w-12" />
                  <col />
                </colgroup>
                <thead>
                  <tr>
                    <th
                      colSpan={4}
                      scope="colgroup"
                      className="text-primary bg-accent px-3 py-0.5 text-left font-normal"
                    >
                      {hunk.header}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {pairRows(hunk).map((row, index) => (
                    // Rows never reorder while shown, so position is a stable key.
                    <tr key={index}>
                      <NumberCell value={row.left?.oldNumber ?? null} />
                      <CodeCell line={row.left} side="left" />
                      <NumberCell value={row.right?.newNumber ?? null} />
                      <CodeCell line={row.right} side="right" />
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}
          </section>
        );
      })}
    </div>
  );
}

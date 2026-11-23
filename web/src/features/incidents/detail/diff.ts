/**
 * A parser for the unified diffs the Fixer stores, and the pairing that a side-by-side view needs.
 *
 * The stored patch is plain `git diff` text, so this reads that format and nothing else: no file
 * contents are needed. Hunk headers say how many lines each side has, and the parser counts them,
 * so a removed SQL comment (`-- note`, which a diff prints as `--- note`) is never mistaken for
 * the start of a new file.
 */

export type LineKind = "context" | "add" | "remove";

export type DiffLine = {
  kind: LineKind;
  /** Line number in the old file, or null for an added line. */
  oldNumber: number | null;
  /** Line number in the new file, or null for a removed line. */
  newNumber: number | null;
  text: string;
  /** The file had no final newline after this line. */
  noNewline: boolean;
};

export type Hunk = {
  header: string;
  lines: DiffLine[];
};

export type DiffFile = {
  oldPath: string;
  newPath: string;
  hunks: Hunk[];
};

/** One row of a side-by-side view: what the old file had and what the new file has. */
export type DiffRow = {
  left: DiffLine | null;
  right: DiffLine | null;
};

const HUNK_HEADER = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

function stripPrefix(path: string): string {
  return path.replace(/^[ab]\//, "");
}

/** Read a unified diff into files, hunks and numbered lines. Text that is not a diff gives []. */
export function parseUnifiedDiff(text: string): DiffFile[] {
  const files: DiffFile[] = [];
  const lines = text.replace(/\n$/, "").split("\n");
  let file: DiffFile | null = null;
  let hunk: Hunk | null = null;
  let oldLeft = 0;
  let newLeft = 0;
  let oldNumber = 0;
  let newNumber = 0;

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";

    // A line of content starts with a space, +, - or a backslash (or is empty). Anything else,
    // such as `@@` or `diff --git`, ends the hunk even if its header promised more lines.
    const marker = line.charAt(0);
    const isContent = marker === "" || " +-\\".includes(marker);
    if (hunk !== null && (oldLeft > 0 || newLeft > 0) && isContent) {
      const body = line.slice(1);
      if (marker === "+") {
        hunk.lines.push({ kind: "add", oldNumber: null, newNumber, text: body, noNewline: false });
        newNumber += 1;
        newLeft -= 1;
      } else if (marker === "-") {
        hunk.lines.push({
          kind: "remove",
          oldNumber,
          newNumber: null,
          text: body,
          noNewline: false,
        });
        oldNumber += 1;
        oldLeft -= 1;
      } else if (marker === "\\") {
        const last = hunk.lines.at(-1);
        if (last) {
          last.noNewline = true;
        }
      } else {
        // A context line; a tool that trimmed trailing spaces leaves a blank line here.
        hunk.lines.push({ kind: "context", oldNumber, newNumber, text: body, noNewline: false });
        oldNumber += 1;
        newNumber += 1;
        oldLeft -= 1;
        newLeft -= 1;
      }
      continue;
    }

    if (line.startsWith("\\") && hunk !== null) {
      const last = hunk.lines.at(-1);
      if (last) {
        last.noNewline = true;
      }
      continue;
    }
    if (line.startsWith("--- ") && (lines[index + 1] ?? "").startsWith("+++ ")) {
      file = {
        oldPath: stripPrefix(line.slice(4).split("\t")[0] ?? ""),
        newPath: stripPrefix((lines[index + 1] ?? "").slice(4).split("\t")[0] ?? ""),
        hunks: [],
      };
      files.push(file);
      hunk = null;
      index += 1;
      continue;
    }
    const header = HUNK_HEADER.exec(line);
    if (header) {
      if (file === null) {
        file = { oldPath: "", newPath: "", hunks: [] };
        files.push(file);
      }
      hunk = { header: line, lines: [] };
      file.hunks.push(hunk);
      oldNumber = Number(header[1]);
      newNumber = Number(header[3]);
      oldLeft = header[2] === undefined ? 1 : Number(header[2]);
      newLeft = header[4] === undefined ? 1 : Number(header[4]);
    }
    // Anything else (`diff --git`, `index`) is metadata a side-by-side view does not show.
  }
  return files;
}

/**
 * Lay a hunk out in two columns. A run of removed lines followed by a run of added lines is
 * paired line for line, so a changed line sits opposite its replacement; the longer run leaves
 * blank cells on the other side. Context lines appear on both sides.
 */
export function pairRows(hunk: Hunk): DiffRow[] {
  const rows: DiffRow[] = [];
  let removed: DiffLine[] = [];
  let added: DiffLine[] = [];

  const flush = () => {
    const height = Math.max(removed.length, added.length);
    for (let row = 0; row < height; row += 1) {
      rows.push({ left: removed[row] ?? null, right: added[row] ?? null });
    }
    removed = [];
    added = [];
  };

  for (const line of hunk.lines) {
    if (line.kind === "remove") {
      if (added.length > 0) {
        flush();
      }
      removed.push(line);
    } else if (line.kind === "add") {
      added.push(line);
    } else {
      flush();
      rows.push({ left: line, right: line });
    }
  }
  flush();
  return rows;
}

/** How many lines the diff adds and removes in all. */
export function countChanges(files: DiffFile[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const line of files.flatMap((file) => file.hunks.flatMap((hunk) => hunk.lines))) {
    added += line.kind === "add" ? 1 : 0;
    removed += line.kind === "remove" ? 1 : 0;
  }
  return { added, removed };
}

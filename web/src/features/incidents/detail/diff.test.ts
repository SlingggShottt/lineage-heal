import { describe, expect, it } from "vitest";

import { countChanges, pairRows, parseUnifiedDiff } from "./diff";

const REPLACE = [
  "--- a/dbt/models/bronze/b.sql",
  "+++ b/dbt/models/bronze/b.sql",
  "@@ -10,4 +10,4 @@",
  " keep one",
  "-old line",
  "+new line",
  " keep two",
  " keep three",
  "",
].join("\n");

const texts = (lines: { text: string }[]) => lines.map((line) => line.text);

describe("parseUnifiedDiff", () => {
  it("reads the file, the hunk and numbered lines on both sides", () => {
    const [file, ...rest] = parseUnifiedDiff(REPLACE);

    expect(rest).toEqual([]);
    expect(file?.oldPath).toBe("dbt/models/bronze/b.sql");
    expect(file?.newPath).toBe("dbt/models/bronze/b.sql");
    const [hunk] = file?.hunks ?? [];
    expect(hunk?.header).toBe("@@ -10,4 +10,4 @@");
    expect(hunk?.lines.map((l) => [l.kind, l.oldNumber, l.newNumber, l.text])).toEqual([
      ["context", 10, 10, "keep one"],
      ["remove", 11, null, "old line"],
      ["add", null, 11, "new line"],
      ["context", 12, 12, "keep two"],
      ["context", 13, 13, "keep three"],
    ]);
  });

  it("reads several hunks and several files", () => {
    const diff = [
      "--- a/a.sql",
      "+++ b/a.sql",
      "@@ -1,2 +1,2 @@",
      "-x",
      "+y",
      " z",
      "@@ -20,2 +20,3 @@",
      " p",
      "+q",
      " r",
      "--- a/b.sql",
      "+++ b/b.sql",
      "@@ -5 +5 @@",
      "-m",
      "+n",
    ].join("\n");

    const files = parseUnifiedDiff(diff);

    expect(files.map((f) => f.newPath)).toEqual(["a.sql", "b.sql"]);
    expect(files[0]?.hunks).toHaveLength(2);
    expect(files[0]?.hunks[1]?.lines.map((l) => l.newNumber)).toEqual([20, 21, 22]);
    expect(files[1]?.hunks[0]?.lines.map((l) => l.kind)).toEqual(["remove", "add"]);
  });

  it("does not mistake a removed SQL comment for the start of a new file", () => {
    // Removing the line `-- note` prints `--- note`; adding `-- x` prints `+-- x`.
    const diff = [
      "--- a/a.sql",
      "+++ b/a.sql",
      "@@ -1,3 +1,3 @@",
      " select 1",
      "--- note",
      "+-- a better note",
      " from t",
    ].join("\n");

    const files = parseUnifiedDiff(diff);

    expect(files).toHaveLength(1);
    expect(texts(files[0]?.hunks[0]?.lines ?? [])).toEqual([
      "select 1",
      "-- note",
      "-- a better note",
      "from t",
    ]);
  });

  it("starts a new hunk or file at a header even when the counts promised more lines", () => {
    // The first header says 5 lines on each side, but only 2 follow.
    const diff = [
      "--- a/a.sql",
      "+++ b/a.sql",
      "@@ -1,5 +1,5 @@",
      " a",
      "-b",
      "@@ -20,1 +20,1 @@",
      "-c",
      "+d",
    ].join("\n");

    const [file] = parseUnifiedDiff(diff);

    expect(file?.hunks.map((h) => h.header)).toEqual(["@@ -1,5 +1,5 @@", "@@ -20,1 +20,1 @@"]);
    expect(file?.hunks[1]?.lines.map((l) => l.text)).toEqual(["c", "d"]);
  });

  it("marks a line that has no final newline", () => {
    const diff = [
      "--- a/a.sql",
      "+++ b/a.sql",
      "@@ -1 +1 @@",
      "-a",
      "\\ No newline at end of file",
      "+b",
    ].join("\n");

    const [first, second] = parseUnifiedDiff(diff)[0]?.hunks[0]?.lines ?? [];

    expect(first?.noNewline).toBe(true);
    expect(second?.noNewline).toBe(false);
  });

  it("treats a blank line inside a hunk as an empty context line", () => {
    const diff = ["--- a/a.sql", "+++ b/a.sql", "@@ -1,3 +1,3 @@", " a", "", "-c", "+d"].join("\n");

    const lines = parseUnifiedDiff(diff)[0]?.hunks[0]?.lines ?? [];

    expect(lines.map((l) => [l.kind, l.text])).toEqual([
      ["context", "a"],
      ["context", ""],
      ["remove", "c"],
      ["add", "d"],
    ]);
  });

  it("ignores git's metadata lines and keeps a /dev/null path as it is", () => {
    const diff = [
      "diff --git a/n.sql b/n.sql",
      "index 1..2 100644",
      "--- /dev/null",
      "+++ b/n.sql",
      "@@ -0,0 +1 @@",
      "+hi",
    ].join("\n");

    const [file] = parseUnifiedDiff(diff);

    expect(file?.oldPath).toBe("/dev/null");
    expect(file?.newPath).toBe("n.sql");
    expect(texts(file?.hunks[0]?.lines ?? [])).toEqual(["hi"]);
  });

  it.each(["", "not a diff at all", "just\nsome\ntext"])("returns nothing for %j", (text) => {
    expect(parseUnifiedDiff(text)).toEqual([]);
  });
});

describe("pairRows", () => {
  const rows = (diff: string) =>
    pairRows(parseUnifiedDiff(diff)[0]?.hunks[0] ?? { header: "", lines: [] });
  const cells = (diff: string) =>
    rows(diff).map((r) => [r.left?.text ?? null, r.right?.text ?? null]);
  const wrap = (body: string[]) => ["--- a/a", "+++ b/a", ...body].join("\n");

  it("puts a changed line opposite its replacement and context on both sides", () => {
    expect(cells(REPLACE)).toEqual([
      ["keep one", "keep one"],
      ["old line", "new line"],
      ["keep two", "keep two"],
      ["keep three", "keep three"],
    ]);
  });

  it("leaves blanks opposite the extra lines when one run is longer", () => {
    const diff = wrap(["@@ -1,3 +1,2 @@", "-a", "-b", "-c", "+x", "+y"]);

    expect(cells(diff)).toEqual([
      ["a", "x"],
      ["b", "y"],
      ["c", null],
    ]);
  });

  it("shows a pure addition with an empty left side, and a pure removal with an empty right", () => {
    expect(cells(wrap(["@@ -1 +1,2 @@", " k", "+n"]))).toEqual([
      ["k", "k"],
      [null, "n"],
    ]);
    expect(cells(wrap(["@@ -1,2 +1 @@", " k", "-g"]))).toEqual([
      ["k", "k"],
      ["g", null],
    ]);
  });

  it("starts a new pairing after each run of context", () => {
    const diff = wrap(["@@ -1,5 +1,5 @@", "-a", "+b", " m", "-c", "+d", " z"]);

    expect(cells(diff)).toEqual([
      ["a", "b"],
      ["m", "m"],
      ["c", "d"],
      ["z", "z"],
    ]);
  });

  it("keeps the numbers of both sides on each paired line", () => {
    const [, change] = rows(REPLACE);

    expect(change?.left?.oldNumber).toBe(11);
    expect(change?.right?.newNumber).toBe(11);
  });
});

describe("countChanges", () => {
  it("counts added and removed lines across files", () => {
    expect(countChanges(parseUnifiedDiff(REPLACE))).toEqual({ added: 1, removed: 1 });
    expect(countChanges([])).toEqual({ added: 0, removed: 0 });
  });
});

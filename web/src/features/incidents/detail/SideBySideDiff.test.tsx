import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SideBySideDiff } from "./SideBySideDiff";

const DIFF = [
  "--- a/dbt/models/bronze/b.sql",
  "+++ b/dbt/models/bronze/b.sql",
  "@@ -10,3 +10,4 @@",
  " keep one",
  "-old line",
  "+new line",
  "+another new line",
  " keep two",
  "@@ -40,2 +41,2 @@",
  "-gone",
  "+here",
  " tail",
  "",
].join("\n");

describe("SideBySideDiff", () => {
  it("shows one block per file with its change count, and one table per hunk", () => {
    render(<SideBySideDiff diff={DIFF} />);

    const region = screen.getByRole("region", { name: "Patch, side by side" });
    expect(
      within(region).getByRole("heading", { name: /dbt\/models\/bronze\/b\.sql/ }),
    ).toHaveTextContent("+3 −2");
    expect(within(region).getAllByRole("table")).toHaveLength(2);
    expect(within(region).getByText("@@ -10,3 +10,4 @@")).toBeInTheDocument();
  });

  it("pairs a changed line with its replacement, with line numbers for each side", () => {
    render(<SideBySideDiff diff={DIFF} />);

    const [first] = screen.getAllByRole("table");
    const rows = within(first as HTMLElement)
      .getAllByRole("row")
      .slice(1); // after the hunk header
    const cells = (row: HTMLElement) =>
      // by tag, because an empty cell is hidden from the accessibility tree on purpose
      [...row.querySelectorAll("td")].map((c) => c.textContent.replace(/^[+− ]/, ""));

    expect(cells(rows[0] as HTMLElement)).toEqual(["10", "keep one", "10", "keep one"]);
    expect(cells(rows[1] as HTMLElement)).toEqual([
      "11",
      "Removed: old line",
      "11",
      "Added: new line",
    ]);
    // the extra added line has nothing opposite it, and no old line number
    expect(cells(rows[2] as HTMLElement)).toEqual(["", "", "12", "Added: another new line"]);
    expect(cells(rows[3] as HTMLElement)).toEqual(["12", "keep two", "13", "keep two"]);
  });

  it("marks the empty cells opposite an unpaired line as decoration, not content", () => {
    render(<SideBySideDiff diff={DIFF} />);

    const [first] = screen.getAllByRole("table");
    const unpaired = within(first as HTMLElement).getAllByRole("row")[3] as HTMLElement;

    expect(unpaired.querySelector("td[aria-hidden='true']")).not.toBeNull();
  });

  it("says in words what was removed and added, not only by tint", () => {
    render(<SideBySideDiff diff={DIFF} />);

    expect(screen.getAllByText("Removed:", { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Added:", { exact: false }).length).toBeGreaterThan(0);
  });

  it("can be reached and scrolled from the keyboard", () => {
    render(<SideBySideDiff diff={DIFF} />);

    expect(screen.getByRole("region", { name: "Patch, side by side" })).toHaveAttribute(
      "tabindex",
      "0",
    );
  });

  it("shows text that is not a unified diff as it is, instead of an empty view", () => {
    render(<SideBySideDiff diff={"just some text\nnot a patch"} />);

    expect(screen.getByLabelText("Patch")).toHaveTextContent("just some text");
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { components } from "@/api/schema";

import { LineageGraph } from "./LineageGraph";

type Lineage = components["schemas"]["LineageOut"];

const lineage: Lineage = {
  version: "v1",
  root_node: "raw.amfi.navall.Net Asset Value",
  truncated: false,
  unreached: [],
  nodes: [
    {
      id: "raw.amfi.navall.Net Asset Value",
      table: "raw.amfi.navall",
      column: "Net Asset Value",
      kind: "raw",
      role: "root",
    },
    {
      id: "brz_amfi__navall.nav_inr",
      table: "brz_amfi__navall",
      column: "nav_inr",
      kind: "model",
      role: "path",
    },
    {
      id: "gld_mf_nav_daily.nav_inr",
      table: "gld_mf_nav_daily",
      column: "nav_inr",
      kind: "model",
      role: "affected",
    },
  ],
  edges: [
    {
      source: "raw.amfi.navall.Net Asset Value",
      target: "brz_amfi__navall.nav_inr",
      kind: "transform",
      inferred: false,
    },
    {
      source: "brz_amfi__navall.nav_inr",
      target: "gld_mf_nav_daily.nav_inr",
      kind: "direct",
      inferred: true,
    },
  ],
};

describe("LineageGraph", () => {
  it("draws every node with its table, column and role written out", () => {
    render(<LineageGraph lineage={lineage} />);

    const nodes = [...document.querySelectorAll<HTMLElement>(".react-flow__node")];
    expect(nodes).toHaveLength(3);
    const text = nodes.map((n) => n.textContent);
    expect(text).toContain("raw.amfi.navallRoot causeNet Asset Value");
    expect(text).toContain("brz_amfi__navallOn the pathnav_inr");
    expect(text).toContain("gld_mf_nav_dailyAffectednav_inr");
  });

  it("gives each node its full id as a tooltip", () => {
    render(<LineageGraph lineage={lineage} />);

    expect(screen.getByTitle("raw.amfi.navall.Net Asset Value")).toBeInTheDocument();
  });

  it("explains the legend in words, with the meaning of a dashed arrow", () => {
    render(<LineageGraph lineage={lineage} />);

    const caption = document.querySelector("figcaption") as HTMLElement;
    expect(caption).toHaveTextContent("Left to right, from the root cause to what it broke.");
    expect(caption).toHaveTextContent("Root cause");
    expect(caption).toHaveTextContent("On the path");
    expect(caption).toHaveTextContent("Affected");
    expect(caption).toHaveTextContent("Dashed arrows are inferred.");
  });

  it("offers zoom and fit buttons, so a mouse wheel is not needed", () => {
    render(<LineageGraph lineage={lineage} />);

    expect(screen.getByRole("button", { name: /zoom in/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /zoom out/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /fit view/i })).toBeInTheDocument();
  });
});

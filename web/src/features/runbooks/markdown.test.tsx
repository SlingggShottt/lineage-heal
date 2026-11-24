import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { blocks } from "./blocks";
import { Markdown } from "./markdown";

describe("blocks", () => {
  it("groups lines into lists and paragraphs, and keeps list types apart", () => {
    expect(blocks("Intro line\ncontinues\n\n- a\n- b\n1. one\n2. two\nTail")).toEqual([
      { kind: "paragraph", text: "Intro line continues" },
      { kind: "list", ordered: false, items: ["a", "b"] },
      { kind: "list", ordered: true, items: ["one", "two"] },
      { kind: "paragraph", text: "Tail" },
    ]);
  });

  it("returns nothing for blank text", () => {
    expect(blocks("\n  \n")).toEqual([]);
  });
});

describe("Markdown", () => {
  it("renders bullets, numbered steps, bold and inline code as real elements", () => {
    render(
      <Markdown text={"- **Blank in the raw file:** check `nav_inr`\n\n1. First\n2. Second"} />,
    );

    const [bullets, steps] = screen.getAllByRole("list");
    expect(bullets?.tagName).toBe("UL");
    expect(steps?.tagName).toBe("OL");
    expect(within(steps as HTMLElement).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("Blank in the raw file:").tagName).toBe("STRONG");
    expect(screen.getByText("nav_inr").tagName).toBe("CODE");
  });

  it("never turns text into markup", () => {
    const { container } = render(
      <Markdown text={'- <img src=x onerror="alert(1)"> and <script>x()</script>'} />,
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container).toHaveTextContent('<img src=x onerror="alert(1)">');
  });

  it("leaves unmatched markers as they are", () => {
    render(<Markdown text="A lone ** and a lone ` stay text" />);

    expect(screen.getByText("A lone ** and a lone ` stay text")).toBeInTheDocument();
  });
});

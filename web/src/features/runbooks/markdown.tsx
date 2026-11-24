import type { ReactNode } from "react";

import { blocks, type Block } from "./blocks";

/**
 * Renders the few Markdown forms the runbook template uses (docs/style_guide.md 8.2): paragraphs,
 * bulleted and numbered lists, bold and inline code. It builds React elements and never sets
 * HTML, so nothing in a runbook can inject markup, and it is small enough to read in full.
 * Anything else is shown as plain text rather than guessed at.
 */
export function Markdown({ text }: { text: string }) {
  return <>{blocks(text).map(renderBlock)}</>;
}

function renderBlock(block: Block, index: number) {
  if (block.kind === "paragraph") {
    return (
      <p key={index} className="mb-2">
        {inline(block.text)}
      </p>
    );
  }
  const Tag = block.ordered ? "ol" : "ul";
  return (
    <Tag
      key={index}
      className={`mb-2 ml-5 flex flex-col gap-1 ${block.ordered ? "list-decimal" : "list-disc"}`}
    >
      {block.items.map((item, i) => (
        <li key={i}>{inline(item)}</li>
      ))}
    </Tag>
  );
}

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`)/g;

/** Bold and inline code; the markers are removed and the rest is text. */
function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} className="bg-muted rounded px-1 py-0.5 font-mono text-[0.85em]">
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}

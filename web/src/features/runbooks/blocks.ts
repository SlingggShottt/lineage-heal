/** Splits runbook text into paragraphs and lists, for `Markdown` to render. */

export type Block =
  { kind: "paragraph"; text: string } | { kind: "list"; ordered: boolean; items: string[] };

const BULLET = /^[-*] (.*)$/;
const NUMBERED = /^\d+\. (.*)$/;

/** Group lines into paragraphs and lists; a blank line or a change of list type ends a block. */
export function blocks(text: string): Block[] {
  const result: Block[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (line === "") {
      result.push({ kind: "paragraph", text: "" }); // a separator, dropped below
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = NUMBERED.exec(line);
    const item = bullet ?? numbered;
    const last = result[result.length - 1];
    if (item) {
      const ordered = numbered !== null && bullet === null;
      if (last?.kind === "list" && last.ordered === ordered) {
        last.items.push(item[1] ?? "");
      } else {
        result.push({ kind: "list", ordered, items: [item[1] ?? ""] });
      }
    } else if (last?.kind === "paragraph" && last.text !== "") {
      last.text += ` ${line}`;
    } else {
      result.push({ kind: "paragraph", text: line });
    }
  }
  return result.filter((block) => block.kind === "list" || block.text !== "");
}

// Small text helpers for the incident page, kept apart from the components so a component file
// exports only components.

const MAX_CHARACTERS = 4000;

/** JSON for reading, cut at a size a page can hold; the cut is said, not hidden. */
export function prettyJson(value: unknown): string {
  const text = JSON.stringify(value, null, 2);
  return text.length > MAX_CHARACTERS
    ? `${text.slice(0, MAX_CHARACTERS)}\n… shortened (${String(text.length)} characters in all)`
    : text;
}

/** A dbt unique id without its project prefix and trailing hash: `not_null_x` not `test.p.not_null_x.1a2b`. */
export function shortNode(uniqueId: string): string {
  const parts = uniqueId.split(".");
  const name = parts.length > 2 ? parts.slice(2) : parts;
  if (name.length > 1 && /^[0-9a-f]{10}$/.test(name[name.length - 1] ?? "")) {
    name.pop();
  }
  return name.join(".");
}

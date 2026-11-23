import { Fact } from "./Section";

const FIELDS: readonly [key: string, label: string][] = [
  ["target", "Target"],
  ["change", "Change"],
  ["verify", "How to check"],
  ["risks", "Risks"],
];

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** A drafted change for a cause outside dbt: advice for a person, never a pull request. */
export function SuggestionView({ suggestion }: { suggestion: Record<string, unknown> }) {
  const draft = text(suggestion.draft);
  return (
    <div className="flex flex-col gap-3">
      {text(suggestion.title) && <p className="text-base font-medium">{text(suggestion.title)}</p>}
      <dl className="flex flex-col gap-2">
        {FIELDS.map(([key, label]) => {
          const value = text(suggestion[key]);
          return (
            value && (
              <Fact key={key} term={label}>
                {value}
              </Fact>
            )
          );
        })}
      </dl>
      {draft && (
        <div>
          <h3 className="text-muted-foreground mb-1 text-xs font-medium">Draft</h3>
          <pre className="bg-muted overflow-x-auto rounded-md p-2 text-xs">{draft}</pre>
        </div>
      )}
    </div>
  );
}

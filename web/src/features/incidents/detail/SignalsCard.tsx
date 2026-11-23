import type { components } from "@/api/schema";

import { Section } from "./Section";

type Signal = components["schemas"]["SignalOut"];

const KIND_LABELS: Record<string, string> = {
  test_failed: "Test failed",
  test_warning: "Test warning",
  test_error: "Test error",
  model_error: "Model error",
  run_error: "Run error",
  ingestion_error: "Download failed",
};

/** The failures the Detector grouped into this incident. */
export function SignalsCard({ signals }: { signals: Signal[] }) {
  return (
    <Section title="Signals" description="What failed in the run, grouped by shared cause.">
      {signals.length === 0 ? (
        <p className="text-muted-foreground">No signals are attached to this incident.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {signals.map((signal) => (
            <li key={`${signal.kind}-${signal.name}`} className="border-l-2 pl-3">
              <p className="font-medium">
                {KIND_LABELS[signal.kind] ?? signal.kind} · {signal.check_type}
                {signal.failures
                  ? ` · ${String(signal.failures)} ${signal.failures === 1 ? "row" : "rows"}`
                  : ""}
              </p>
              <p>
                <code className="text-xs break-all">
                  {[signal.model, signal.column].filter(Boolean).join(".") || signal.name}
                </code>
              </p>
              {signal.message && (
                <p className="text-muted-foreground">{signal.message.split("\n")[0]}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

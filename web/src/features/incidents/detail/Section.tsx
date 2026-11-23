import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type SectionProps = {
  title: string;
  description?: string;
  children: ReactNode;
};

/** A titled card; the title is a heading so the page has an outline a reader can jump around. */
export function Section({ title, description, children }: SectionProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">{children}</CardContent>
    </Card>
  );
}

/** A term and its value, as a definition list row. */
export function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
      <dt className="text-muted-foreground w-36 shrink-0">{term}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type PlaceholderProps = {
  title: string;
  description: string;
};

/** A page whose screen is built by a later task; says what will appear here. */
export function Placeholder({ title, description }: PlaceholderProps) {
  return (
    <>
      <h1 className="mb-6 text-2xl font-semibold">{title}</h1>
      <Card>
        <CardHeader>
          <CardTitle>Coming soon</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent className="text-muted-foreground text-sm">
          The scaffold is in place; this screen is built in a later task.
        </CardContent>
      </Card>
    </>
  );
}

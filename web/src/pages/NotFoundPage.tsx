import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <>
      <h1 className="mb-2 text-2xl font-semibold">Page not found</h1>
      <p className="text-muted-foreground">
        There is nothing at this address.{" "}
        <Link to="/" className="text-primary underline">
          Back to the overview
        </Link>
      </p>
    </>
  );
}

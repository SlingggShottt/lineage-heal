import { Button } from "@/components/ui/button";

type PagerProps = {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
};

/** "Showing 26–50 of 60" with Previous and Next. The buttons only appear when there is more. */
export function Pager({ page, pageSize, total, onPage }: PagerProps) {
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <nav aria-label="Pages" className="mt-4 flex items-center justify-between gap-4">
      <p className="text-muted-foreground text-sm" aria-live="polite">
        {total === 0 || first > total
          ? `${String(total)} in total`
          : `Showing ${String(first)}–${String(last)} of ${String(total)}`}
      </p>
      {pages > 1 && (
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => {
              onPage(page - 1);
            }}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= pages}
            onClick={() => {
              onPage(page + 1);
            }}
          >
            Next
          </Button>
        </div>
      )}
    </nav>
  );
}

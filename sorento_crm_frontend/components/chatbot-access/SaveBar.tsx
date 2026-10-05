import { Button } from '@/components/ui/button';

/** The sticky Save bar: the number of unsaved changes, Discard and Save (n). */
export default function SaveBar({
  count,
  saving,
  onDiscard,
  onSave,
}: {
  count: number;
  saving?: boolean;
  onDiscard: () => void;
  onSave: () => void;
}) {
  return (
    <div className="sticky bottom-0 z-10 -mx-4 border-t bg-background/95 px-4 py-3 lg:-mx-6 lg:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-sm text-muted-foreground">
          {count === 0 ? 'No unsaved changes' : `${count} unsaved change${count > 1 ? 's' : ''}`}
        </span>
        <div className="flex gap-2">
          <Button variant="outline" disabled={count === 0 || saving} onClick={onDiscard}>
            Discard
          </Button>
          <Button disabled={count === 0 || saving} onClick={onSave}>
            {count === 0 ? 'Save' : `Save (${count})`}
          </Button>
        </div>
      </div>
    </div>
  );
}

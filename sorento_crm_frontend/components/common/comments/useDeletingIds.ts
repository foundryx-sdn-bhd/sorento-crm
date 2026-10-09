import { useCallback, useState } from 'react';

/**
 * Which comments have a delete countdown running. The countdown lives in each row's Delete
 * control, so that control reports into this set and the thread dims the row and hides its
 * Reply and Edit while it runs.
 */
export function useDeletingIds() {
  const [ids, setIds] = useState<ReadonlySet<string>>(new Set());
  const report = useCallback((id: string, active: boolean) => {
    setIds((prev) => {
      if (prev.has(id) === active) return prev;
      const next = new Set(prev);
      if (active) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);
  return { isDeleting: (c: { id: string }) => ids.has(c.id), report };
}

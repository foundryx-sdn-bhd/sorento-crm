'use client';

import { Button } from '@/components/ui/button';

export type BoardScope = 'saved' | 'all';

/**
 * Saved | All (owner hand test, 1 Oct 2026): Saved is what Confirm will send, All is every line,
 * so a line does not vanish when it is saved. Built the
 * same way as the board's own Grid | List switch (`FulfilmentBoardPanel`, the "Board view"
 * group): two `Button size="sm"` in a bordered inline group, the pressed one `variant="primary"`
 * (the system's filled blue), the other `ghost`. No custom colour classes.
 */
export function BoardScopeToggle({
  value,
  onChange,
  savedCount,
  allCount,
}: {
  value: BoardScope;
  onChange: (next: BoardScope) => void;
  savedCount: number;
  allCount: number;
}) {
  return (
    <div
      className="inline-flex rounded-md border border-input"
      role="group"
      aria-label="Saved or all"
      data-testid="board-scope-toggle"
    >
      <Button
        type="button"
        size="sm"
        variant={value === 'saved' ? 'primary' : 'ghost'}
        className="rounded-e-none"
        aria-pressed={value === 'saved'}
        onClick={() => onChange('saved')}
      >
        {`Saved (${savedCount})`}
      </Button>
      <Button
        type="button"
        size="sm"
        variant={value === 'all' ? 'primary' : 'ghost'}
        className="rounded-s-none border-s border-input"
        aria-pressed={value === 'all'}
        onClick={() => onChange('all')}
      >
        {`All (${allCount})`}
      </Button>
    </div>
  );
}

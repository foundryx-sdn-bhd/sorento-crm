'use client';

import { Button } from '@/components/ui/button';
import { LandingCardShell } from '@/app/(auth)/portal/components/LandingCardShell';
import { doneByText } from '@/components/stock-asks/AskEditCells';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import type { StockAsk } from '@/lib/stock-asks';
import { askAnswerText, askProductText } from '@/lib/stock-asks-todo';
import { codeWithName } from '@/lib/product-display';

/**
 * One ask on the landing card shell: who asked, what they asked, what the system answered, when.
 * The whole card opens the conversation; the one button in the top-right slot (Done, or Reopen on
 * a done card) never does. No badge, no age, no note: the opened card holds the rest.
 */
export function AskCard({
  ask,
  pending,
  showAgent = false,
  onOpen,
  onDone,
  onReopen,
}: {
  ask: StockAsk;
  pending: boolean;
  /** The CRM's All agents view: the agent code sits on the date line. */
  showAgent?: boolean;
  onOpen: (ask: StockAsk) => void;
  /** Absent on a surface that only ever shows done cards (the done history). */
  onDone?: (askId: string) => void;
  onReopen: (askId: string) => void;
}) {
  const isDone = ask.state === 'done';
  const primary = ask.customer_name || ask.contact_name || '-';
  const secondary = ask.customer_name ? ask.contact_name : null;
  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();

  return (
    <LandingCardShell
      tintClass={isDone ? 'bg-success/5 border-success/40' : 'bg-primary/5 border-primary/30'}
      role="button"
      tabIndex={0}
      onClick={() => onOpen(ask)}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen(ask);
        }
      }}
    >
      {/* The slot: a press on the button (mouse or key) stays on the button. */}
      <div className="absolute top-2 right-2" onClick={stop} onKeyDown={stop}>
        {isDone ? (
          <Button size="sm" variant="outline" disabled={pending} onClick={() => onReopen(ask.id)}>
            Reopen
          </Button>
        ) : (
          <Button size="sm" variant="primary" disabled={pending} onClick={() => onDone?.(ask.id)}>
            Done
          </Button>
        )}
      </div>
      <div className={isDone ? 'opacity-70' : undefined}>
        <p className="pr-24 text-base break-words">
          <span className="font-semibold">{primary}</span>
          {secondary ? <span className="text-sm text-muted-foreground"> {secondary}</span> : null}
        </p>
        <div className="mt-1 space-y-1">
          <p className="text-sm text-foreground/85 break-words" title={codeWithName(ask.product_code, ask.product_name)}>
            Asked: {askProductText(ask)}
          </p>
          <p className="text-sm text-foreground/85 break-words">
            Answered: {askAnswerText(ask)}
          </p>
          <p className="text-xs text-muted-foreground">
            {formatDateTimeInMalaysia(ask.created_at)}
            {showAgent && ask.agent_code ? (
              <>
                {' · '}
                <span className="font-medium">{ask.agent_code}</span>
              </>
            ) : null}
          </p>
          {isDone ? <p className="text-xs text-success">{doneByText(ask)}</p> : null}
        </div>
      </div>
    </LandingCardShell>
  );
}

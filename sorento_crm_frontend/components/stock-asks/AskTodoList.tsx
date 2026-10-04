'use client';

import { useMemo } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { MessageSquareText } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { AskCard } from '@/components/stock-asks/AskCard';
import { AskTodoGrid } from '@/components/stock-asks/AskTodoGrid';
import { NOOP_ON_UPDATE, fadeExitTransition, useReducedMotion } from '@/lib/motion';
import type { StockAsk } from '@/lib/stock-asks';
import { DEFAULT_ASK_SORT, bucketTodo, type AskTodoPayload } from '@/lib/stock-asks-todo';
import type { LandingSort } from '@/app/(auth)/portal/lib/landing-fields';
import type { ListBoardViewMode } from '@/hooks/useListBoardViewPreference';

export interface AskTodoListProps {
  payload: AskTodoPayload | null;
  loading: boolean;
  error?: string | null;
  /** `board` = the landing cards, `list` = the DataGrid. */
  view: ListBoardViewMode;
  /** Orders the rows inside each section; the mount owns where it is remembered. */
  sort?: LandingSort;
  onSortChange?: (sort: LandingSort) => void;
  /** Opens the conversation for one ask (the whole card / row). */
  onOpen: (ask: StockAsk) => void;
  onDone: (askId: string) => void;
  onReopen: (askId: string) => void;
  /** The CRM manager view: an Agent column in the list. */
  showAgent?: boolean;
  /** The CRM only: a Done by column in the list. */
  showDoneBy?: boolean;
  /** A filter or search narrowed the rows: an empty result reads "No asks match". */
  filtered?: boolean;
  /** The ask whose Done / Reopen PATCH is in flight: its button is disabled meanwhile. */
  pendingAskId?: string | null;
  /** `sales.customer_asks.view::todo` on the CRM, null on the portal. */
  listingKey?: string | null;
}

/**
 * The salesperson's to-do of their customers' asks, shared by the portal Customer asks tab and
 * Sales > Customer asks in the CRM. Presentational: the mount fetches, owns the toolbar, the
 * sort persistence and the opened card. Cards or the DataGrid, in two groups: Open, Done today.
 */
export function AskTodoList({
  payload,
  loading,
  error,
  view,
  sort = DEFAULT_ASK_SORT,
  onSortChange,
  onOpen,
  onDone,
  onReopen,
  showAgent = false,
  showDoneBy = false,
  filtered = false,
  pendingAskId = null,
  listingKey = null,
}: AskTodoListProps) {
  const reduced = useReducedMotion();
  const bucketed = useMemo(() => (payload ? bucketTodo(payload, sort) : null), [payload, sort]);

  if (error) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
        <h2 className="text-sm font-semibold text-destructive">The asks could not be loaded</h2>
        <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">{error}</p>
      </div>
    );
  }
  if (loading && !bucketed) {
    return (
      <div className="space-y-2" role="status" aria-label="Loading">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (!payload || !bucketed) return null;

  const { counts, sections, done } = bucketed;
  const exit = { opacity: 0, transition: fadeExitTransition(reduced) };
  const empty =
    counts.open === 0 ? (
      <div className="space-y-2 rounded-lg border px-6 py-8 text-center">
        <MessageSquareText className="mx-auto size-8 text-muted-foreground" />
        {filtered ? (
          <p className="font-medium">No asks match</p>
        ) : (
          <>
            <p className="font-medium">Nothing waiting</p>
            <p className="text-sm text-muted-foreground">Asks your customers make on WhatsApp land here.</p>
          </>
        )}
      </div>
    ) : null;
  const truncated = payload.truncated ? (
    <p className="text-sm text-muted-foreground">Showing the oldest 500 open asks</p>
  ) : null;

  if (view === 'list') {
    return (
      <div className="space-y-4">
        {empty}
        {truncated}
        {counts.open > 0 || done.length > 0 ? (
          <AskTodoGrid
            payload={payload}
            sort={sort}
            showAgent={showAgent}
            showDoneBy={showDoneBy}
            listingKey={listingKey}
            onOpen={onOpen}
            onDone={onDone}
            onReopen={onReopen}
            pendingAskId={pendingAskId}
            onSortChange={onSortChange}
          />
        ) : null}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {empty}

      {sections.map((section) => (
        <section key={section.key} aria-labelledby={`ask-section-${section.key}`} className="space-y-2">
          <h2 id={`ask-section-${section.key}`} className="text-sm font-semibold">
            {section.label}
          </h2>
          <ul className="space-y-2.5">
            <AnimatePresence initial={false}>
              {section.days.flatMap((day) =>
                day.asks.map((ask) => (
                  <motion.li key={ask.id} exit={exit} onUpdate={NOOP_ON_UPDATE}>
                    <AskCard
                      ask={ask}
                      pending={pendingAskId === ask.id}
                      showAgent={showAgent}
                      onOpen={onOpen}
                      onDone={onDone}
                      onReopen={onReopen}
                    />
                  </motion.li>
                )),
              )}
            </AnimatePresence>
          </ul>
        </section>
      ))}

      {truncated}

      {done.length > 0 ? (
        <section aria-labelledby="ask-section-done" className="space-y-2">
          <h2 id="ask-section-done" className="text-sm font-semibold">
            Done today
          </h2>
          <ul className="space-y-2.5">
            {done.map((ask) => (
              <li key={ask.id}>
                <AskCard
                  ask={ask}
                  pending={pendingAskId === ask.id}
                  showAgent={showAgent}
                  onOpen={onOpen}
                  onDone={onDone}
                  onReopen={onReopen}
                />
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

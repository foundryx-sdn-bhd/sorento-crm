'use client';

import * as React from 'react';
import Link from 'next/link';
import { AlertTriangle, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { NumberInput } from '@/components/ui/number-input';
import { Textarea } from '@/components/ui/textarea';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { formatDateInMalaysia } from '@/lib/helpers';
import type {
  BorrowCandidate,
  SupplyComponent,
  SupplyLine,
  SupplySpoRef,
} from '../../_shared/types/fulfilmentPlanning.types';
import {
  fromMinor,
  lineBalance,
  lineBlockers,
  quantityError,
  toMinor,
  type DraftBorrow,
  type DraftLine,
} from '../../_shared/lib/supplyComposition';
import { poolShareLimitsFromLine } from '../../_shared/lib/poolShare';
import { BoardLadderOptionsTable, debtMonthLabel } from './BoardLadderOptionsTable';
import { BorrowAddDialog } from './BorrowAddDialog';
import { ClassificationProofPopover } from './ClassificationProofPopover';

/**
 * One line's composition (journey steps 1 and 2).
 *
 * The read view and the edit view are the same layout: a confirmed line shows the frozen
 * quantity where the input was, in the same order, under the same headings. Nothing moves,
 * appears or disappears when a decision lands, because the read view is what taught the
 * reader where things are.
 *
 * Every section renders whatever the answer is (AC-G02). No eligible stock, no incoming by
 * the delivery date, no later incoming, no borrow candidate, no reorder level and no
 * classification each state that in place; a section that disappears reads as a screen
 * that has not finished loading.
 */
export function SupplyLineCard({
  line,
  draft,
  frozen,
  onChange,
  salesOrderHref,
}: {
  line: SupplyLine;
  draft: DraftLine;
  /** The composition is a decision that stands: it is shown, not edited. */
  frozen: boolean;
  onChange: (next: DraftLine) => void;
  /** Where the fulfilment location is stated, for a line whose sales order does not. */
  salesOrderHref?: string | null;
}) {
  const [adding, setAdding] = React.useState(false);

  // A line the sheet cannot plan is named and blocked rather than guessed at: no reconciled
  // AutoCount line to promise against, or a sales order that states no fulfilment location
  // (so no free stock, no pool and no incoming to read). The way out is the sales order.
  if (line.unplannable_reason) {
    return (
      <BlockedLineCard line={line} reason={line.unplannable_reason} salesOrderHref={salesOrderHref} />
    );
  }
  if (line.fulfilment_location_missing) {
    return (
      <BlockedLineCard
        line={line}
        reason="No fulfilment location on the sales order line, so nothing can be composed for it."
        salesOrderHref={salesOrderHref}
      />
    );
  }

  const frozenComponents = line.frozen?.components ?? [];
  const balance = lineBalance(draft);
  // B2 (fix round 5): the card's OWN blocker text agrees with the sheet's foot-of-page list
  // (`SupplyCompositionSection`), which reads the same line's `pool_allowances` - without
  // this the card refused the engine's own "BRW 62 + Buy 73" while the foot said nothing.
  const blockers = frozen ? [] : lineBlockers(draft, poolShareLimitsFromLine(line));
  const uom = line.uom ? ` ${line.uom}` : '';
  const openQty = frozen ? (line.frozen?.open_qty ?? line.open_qty) : line.open_qty;

  const frozenOf = (kind: SupplyComponent['kind']) =>
    frozenComponents.filter((component) => component.kind === kind);

  const setBorrow = (borrow: DraftBorrow[]) => onChange({ ...draft, borrow });
  // One or several borrow rows appended in ONE change: a merged bin's split arrives as
  // several (BORROW-LOCATION-ONLY R2), and appending them one by one off the same `draft`
  // would keep only the last.
  const addBorrows = (parts: { candidate: BorrowCandidate; qty: string }[], reason: string) =>
    setBorrow([
      ...draft.borrow,
      ...parts.map(({ candidate, qty }, index) => ({
        key: `borrow-${line.project_line_id}-${candidate.warehouse_code}-${draft.borrow.length + index}`,
        source: candidate.source,
        warehouse_code: candidate.warehouse_code,
        warehouse_id: candidate.warehouse_id,
        donor_project_ref: candidate.donor_project_ref,
        donor_project_id: candidate.donor_project_id,
        qty,
        reason,
        donor_impact: candidate.donor_impact,
        // Ladder v2 group borrow (section E.4): the donor SO line this
        // candidate names, so the confirmation checks its live commitment
        // and raises the order-back under the right donor (mirrors
        // BoardAmendDialog's own Borrow-add flow, `BoardAmendDialog.tsx`).
        donor_core_line_id: candidate.donor_core_line_id,
        donor_so_number: candidate.donor_so_number,
        donor_line_no: candidate.donor_line_no,
        donor_agent_code: candidate.donor_agent_code,
        same_agent: candidate.same_agent,
        donor_required_date: candidate.donor_required_date,
      })),
    ]);

  return (
    <article className="rounded-lg border border-border">
      <header className="flex flex-col gap-1 border-b border-border px-3 py-2.5 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">
            {`Line ${line.line_no}${line.item_code ? ` · ${line.item_code}` : ''}`}
          </div>
          <div className="truncate text-sm text-muted-foreground" title={line.description ?? ''}>
            {line.description || 'No description'}
          </div>
        </div>
        <div className="shrink-0 text-sm tabular-nums sm:text-end">
          <span className="font-medium">{`${openQty}${uom}`}</span>
          <span className="text-muted-foreground"> open</span>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-3 border-b border-border px-3 py-2.5 sm:grid-cols-3">
        <Fact label="Delivery date">
          {line.required_date ? (
            <span className="tabular-nums">{formatDateInMalaysia(line.required_date)}</span>
          ) : (
            <Muted>No date</Muted>
          )}
        </Fact>
        {/* The line's own location, off the core sales-order line. Never asked for here and
            never defaulted: a guessed warehouse is a silent wrong answer inside a promise to
            a customer. */}
        <Fact label="Fulfil from">
          {line.fulfilment_location ? (
            <span>{line.fulfilment_location}</span>
          ) : (
            <span className="text-destructive">Not on the sales order line</span>
          )}
        </Fact>
        {/* Own-location Reserve is always eligible regardless of this fact (PLAN 3.3a); it
            names what the SHARED POOL below is offered against. */}
        <Fact label="Hot-selling">
          <div className="flex flex-wrap items-center gap-1">
            <HotSellingPills line={line} />
            <ClassificationProofPopover
              productId={line.product_id}
              testId={line.project_line_id}
            />
          </div>
        </Fact>
      </div>

      {/* Reserve evidence: the pool and what hot-selling (if any) offers of it. Rendered on
          every line, so "no reorder level is set" is stated rather than left blank. */}
      <div className="border-b border-border px-3 py-2.5 text-sm">
        <div className="text-2xs uppercase tracking-wide text-muted-foreground">
          Reserve pool
        </div>
        {line.pool_location ? (
          <p className="break-words">
            {line.pool_location}
            {line.pool_reorder_level && line.pool_reorder_level !== '0'
              ? `, reorder level ${line.pool_reorder_level}`
              : ', no reorder level set'}
            {line.is_dealer_hot_selling
              ? '. Dealer hot-selling: the pool is not offered.'
              : line.is_project_hot_selling
                ? '. Project hot-selling: the pool is offered while it stays available.'
                : '.'}
          </p>
        ) : (
          <Muted>No pool is configured for this location.</Muted>
        )}
      </div>

      {line.is_discontinued && (
        <div className="flex items-start gap-2 border-b border-border bg-destructive/5 px-3 py-2.5 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
          <span>This product is discontinued.</span>
        </div>
      )}

      {/* WHAT THE LADDER OFFERED, above the composition it proposes (R36, AC-S3-14).
          The same table the board's decision panel and the trail popover render, from the
          same five answers - `/supply` carries them per line exactly as the board payload
          carries them per contribution. It reads BEFORE the components, because it is what
          the planner reads before deciding whether to amend them. Display only: taking a
          different option is done in the inputs below. */}
      {(line.options?.length ?? 0) > 0 && (
        <div className="border-b border-border px-3 py-2.5">
          <div className="mb-1 text-2xs uppercase tracking-wide text-muted-foreground">
            Options
          </div>
          <BoardLadderOptionsTable
            options={line.options ?? []}
            contributionKey={line.project_line_id}
          />
        </div>
      )}

      {/* Components, in the order the engine proposes them (plan 3.2). */}
      <div className="divide-y divide-border">
        <Row label="Incoming by the delivery date">
          {frozen ? (
            <FrozenComponents components={frozenOf('timely_spo')} uom={uom} />
          ) : line.timely_spo.length === 0 ? (
            <Muted>No incoming arrives by the delivery date.</Muted>
          ) : (
            <div className="space-y-1">
              <div className="text-sm tabular-nums">{`${draft.timely_spo_qty}${uom}`}</div>
              {line.components
                .filter((component) => component.kind === 'timely_spo')
                .map((component, index) => (
                  <Reason key={`timely-${index}`}>{component.reason}</Reason>
                ))}
              <SpoList refs={line.timely_spo} />
            </div>
          )}
        </Row>

        <Row label="Reserve">
          {frozen ? (
            <FrozenComponents components={frozenOf('reserve')} uom={uom} />
          ) : draft.reserve.length === 0 ? (
            <Muted>
              {`No eligible free stock to reserve${
                line.fulfilment_location ? ` at ${line.fulfilment_location}` : ''
              }.`}
            </Muted>
          ) : (
            <div className="space-y-2">
              {draft.reserve.map((row, index) => (
                <div key={row.key} className="space-y-1">
                  <div className="flex items-center gap-2">
                    <NumberInput
                      min="0"
                      step="any"
                      value={row.qty}
                      error={quantityError(row.qty)}
                      aria-label={`Reserve at ${row.location ?? 'the fulfilment location'} on line ${line.line_no}`}
                      onChange={(event) => {
                        const next = [...draft.reserve];
                        next[index] = { ...row, qty: event.target.value };
                        onChange({ ...draft, reserve: next });
                      }}
                      className="h-8 w-28 tabular-nums"
                    />
                    <span className="text-sm text-muted-foreground">
                      {row.location ?? 'Location not set'}
                    </span>
                  </div>
                  <Reason>{row.reason}</Reason>
                </div>
              ))}
            </div>
          )}
        </Row>

        <Row label="Borrow">
          {frozen ? (
            <FrozenComponents components={frozenOf('borrow')} uom={uom} />
          ) : (
            <div className="space-y-3">
              {draft.borrow.length === 0 ? (
                <Muted>Nothing is borrowed on this line.</Muted>
              ) : (
                draft.borrow.map((row, index) => (
                  <div key={row.key} className="space-y-1.5">
                    <div className="flex items-center gap-2">
                      <NumberInput
                        min="0"
                        step="any"
                        value={row.qty}
                        error={quantityError(row.qty)}
                        aria-label={`Borrow from ${row.donor_project_ref ?? row.warehouse_code} on line ${line.line_no}`}
                        onChange={(event) => {
                          const next = [...draft.borrow];
                          next[index] = { ...row, qty: event.target.value };
                          setBorrow(next);
                        }}
                        className="h-8 w-28 tabular-nums"
                      />
                      <span className="min-w-0 truncate text-sm text-muted-foreground">
                        {row.donor_project_ref
                          ? `${row.donor_project_ref} at ${row.warehouse_code}`
                          : row.warehouse_code}
                      </span>
                      <Button
                        type="button"
                        mode="icon"
                        variant="dim"
                        aria-label={`Remove the borrow from ${row.donor_project_ref ?? row.warehouse_code} on line ${line.line_no}`}
                        onClick={() =>
                          setBorrow(draft.borrow.filter((entry) => entry.key !== row.key))
                        }
                      >
                        <Trash2 />
                      </Button>
                    </div>
                    {row.supply_document ? (
                      <BorrowDocumentFacts row={row} uom={uom} />
                    ) : (
                      <p className="text-sm text-muted-foreground break-words">
                        {`${row.donor_impact.free_before} free before, ${row.donor_impact.free_after_full_borrow} after taking all of it, ${row.donor_impact.committed_qty} committed.`}
                      </p>
                    )}
                    <div className="space-y-1">
                      <label
                        className="block text-2xs uppercase tracking-wide text-muted-foreground"
                        htmlFor={`borrow-reason-${line.project_line_id}-${row.key}`}
                      >
                        Reason <span className="text-destructive">*</span>
                      </label>
                      <Textarea
                        id={`borrow-reason-${line.project_line_id}-${row.key}`}
                        value={row.reason}
                        rows={2}
                        placeholder="In your own words"
                        onChange={(event) => {
                          const next = [...draft.borrow];
                          next[index] = { ...row, reason: event.target.value };
                          setBorrow(next);
                        }}
                      />
                    </div>
                  </div>
                ))
              )}

              {line.borrow_candidates.length === 0 ? (
                <Muted>No other location or project holds free stock of this item.</Muted>
              ) : (
                <Button type="button" variant="outline" size="sm" onClick={() => setAdding(true)}>
                  <Plus className="size-4" aria-hidden />
                  Add a borrow
                </Button>
              )}
            </div>
          )}
        </Row>

        <Row label="Buy">
          {frozen ? (
            <FrozenComponents components={frozenOf('buy')} uom={uom} />
          ) : (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <NumberInput
                  min="0"
                  step="any"
                  value={draft.buy_qty}
                  error={quantityError(draft.buy_qty)}
                  aria-label={`Buy on line ${line.line_no}`}
                  onChange={(event) => onChange({ ...draft, buy_qty: event.target.value })}
                  className="h-8 w-28 tabular-nums"
                />
                <span className="text-sm text-muted-foreground">To purchase</span>
              </div>
              {line.components
                .filter((component) => component.kind === 'buy')
                .map((component, index) => (
                  <Reason key={`buy-${index}`}>{component.reason}</Reason>
                ))}
              {line.is_discontinued && (
                <div className="space-y-1">
                  <label
                    className="block text-2xs uppercase tracking-wide text-muted-foreground"
                    htmlFor={`buy-reason-${line.project_line_id}`}
                  >
                    Reason
                  </label>
                  <Textarea
                    id={`buy-reason-${line.project_line_id}`}
                    value={draft.buy_reason}
                    rows={2}
                    placeholder="In your own words"
                    onChange={(event) => onChange({ ...draft, buy_reason: event.target.value })}
                  />
                </div>
              )}
            </div>
          )}
        </Row>

        <Row label="Later incoming">
          {line.advisory_spo.length === 0 ? (
            <Muted>No later incoming for this item at this location.</Muted>
          ) : (
            <div className="space-y-1">
              <SpoList refs={line.advisory_spo} />
              <Muted>Advisory: it arrives after the delivery date and covers nothing.</Muted>
            </div>
          )}
        </Row>
      </div>

      {/* The balance CS is editing against, and what stops the Confirm. */}
      <footer className="space-y-1.5 border-t border-border px-3 py-2.5">
        <div className="text-sm tabular-nums break-words">
          {frozen
            ? `${openQty} open = ${frozenTotal(frozenComponents)} composed`
            : `${draft.open_qty} open = ${fromMinor(balance.timelyMinor)} incoming + ${fromMinor(
                balance.reserveMinor,
              )} reserve + ${fromMinor(balance.borrowMinor)} borrow + ${fromMinor(
                balance.buyMinor,
              )} buy`}
        </div>
        {!frozen && blockers.length > 0 && (
          <ul className="space-y-0.5">
            {blockers.map((blocker) => (
              <li key={blocker} className="text-sm text-destructive break-words">
                {blocker}
              </li>
            ))}
          </ul>
        )}
      </footer>

      {adding && (
        <BorrowAddDialog
          lineNo={line.line_no}
          itemCode={line.item_code}
          lineId={line.project_line_id}
          candidates={line.borrow_candidates}
          onDone={() => setAdding(false)}
          onAdd={(candidate: BorrowCandidate, qty, reason) =>
            addBorrows([{ candidate, qty }], reason)
          }
          onAddMany={addBorrows}
        />
      )}
    </article>
  );
}

/**
 * A line that cannot be planned, and why, in the same shape as one that can: same header,
 * same facts row, so nothing moves between a blocked line and its neighbour.
 */
function BlockedLineCard({
  line,
  reason,
  salesOrderHref,
}: {
  line: SupplyLine;
  reason: string;
  salesOrderHref?: string | null;
}) {
  return (
    <article className="rounded-lg border border-border">
      <header className="flex flex-col gap-1 border-b border-border px-3 py-2.5 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">
            {`Line ${line.line_no}${line.item_code ? ` · ${line.item_code}` : ''}`}
          </div>
          <div className="truncate text-sm text-muted-foreground" title={line.description ?? ''}>
            {line.description || 'No description'}
          </div>
        </div>
        <div className="shrink-0 text-sm tabular-nums sm:text-end">
          <span className="font-medium">
            {`${line.open_qty}${line.uom ? ` ${line.uom}` : ''}`}
          </span>
          <span className="text-muted-foreground"> open</span>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-3 border-b border-border px-3 py-2.5 sm:grid-cols-3">
        <Fact label="Delivery date">
          {line.required_date ? (
            <span className="tabular-nums">{formatDateInMalaysia(line.required_date)}</span>
          ) : (
            <Muted>No date</Muted>
          )}
        </Fact>
        <Fact label="Fulfil from">
          {line.fulfilment_location ? (
            <span>{line.fulfilment_location}</span>
          ) : (
            <span className="text-destructive">Not on the sales order line</span>
          )}
        </Fact>
        <Fact label="Retail classification">
          <Muted>Not read</Muted>
        </Fact>
      </div>

      <div className="flex flex-col gap-2 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
        <p className="min-w-0 text-sm text-destructive break-words">{reason}</p>
        {salesOrderHref ? (
          <Button asChild variant="outline" size="sm" className="shrink-0">
            <Link href={salesOrderHref}>Open the sales order</Link>
          </Button>
        ) : null}
      </div>
    </article>
  );
}

function frozenTotal(components: SupplyComponent[]): string {
  return fromMinor(components.reduce((total, component) => total + toMinor(component.qty), 0));
}

function FrozenComponents({
  components,
  uom,
}: {
  components: SupplyComponent[];
  uom: string;
}) {
  if (components.length === 0) return <Muted>None</Muted>;
  return (
    <div className="space-y-1.5">
      {components.map((component, index) => (
        <div key={`${component.kind}-${index}`} className="space-y-0.5">
          <div className="text-sm tabular-nums">
            {`${component.qty}${uom}`}
            {component.source_location ? (
              <span className="text-muted-foreground">{` at ${component.source_location}`}</span>
            ) : null}
            {component.donor_project_ref ? (
              <span className="text-muted-foreground">{` for ${component.donor_project_ref}`}</span>
            ) : null}
          </div>
          <Reason>{component.reason}</Reason>
          {/* What a PERSON said, beside what the rule said - and never the rule's own
              sentence twice: a borrow confirmed as the engine proposed it freezes that
              sentence as the reason given for it. */}
          {component.cs_reason && component.cs_reason !== component.reason ? (
            <p className="text-sm break-words">{component.cs_reason}</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/**
 * A BORROW OFF AN INCOMING DOCUMENT, stated as the document it is (ladder v7.1 step 3, S4).
 *
 * The donor-impact triple underneath every other borrow row is a fact about STOCK AT A BIN -
 * what is free there now, what is left after taking it, what is already committed - and a
 * container still at sea is on no shelf, so the row read `0 free before, 0 after taking all
 * of it, 0 committed` and said nothing true about what was being taken. These are the same
 * facts the engine's own sentence carries: which document, when it lands, how much of it,
 * and whose order pays for it.
 *
 * FREE, not borrowed, when no donor is named: nobody was waiting on that share, so there is
 * no order to name and no debt month to state - exactly what makes the engine's sentence
 * read "Take" rather than "Borrow".
 */
function BorrowDocumentFacts({ row, uom }: { row: DraftBorrow; uom: string }) {
  const arrival = row.arrival_date ? formatDateInMalaysia(row.arrival_date) : null;
  return (
    <div
      data-testid={`borrow-document-${row.key}`}
      className="space-y-0.5 text-sm text-muted-foreground"
    >
      <p className="break-words">
        {`${row.supply_document}, ${row.qty}${uom}${arrival ? `, arriving ${arrival}` : ''}`}
      </p>
      <p className="break-words">{borrowDocumentDonorText(row)}</p>
    </div>
  );
}

/** Whose order pays for it, and in which month - or that nobody is waiting on it. */
function borrowDocumentDonorText(row: DraftBorrow): string {
  if (!row.donor_so_number) return 'Free: nobody is waiting on this document.';
  const line = row.donor_line_no != null ? ` line ${row.donor_line_no}` : '';
  const agent = row.donor_agent_code ? ` (${row.donor_agent_code})` : '';
  const due = row.donor_required_date
    ? `, due ${formatDateInMalaysia(row.donor_required_date)}`
    : '';
  const debt = row.donor_required_date
    ? `; its debt lands in ${debtMonthLabel(row.donor_required_date.slice(0, 7))}`
    : '';
  return `From ${row.donor_so_number}${line}${agent}${due}${debt}.`;
}

function SpoList({ refs }: { refs: SupplySpoRef[] }) {
  return (
    <ul className="space-y-0.5">
      {/* Keyed by POSITION as well as by document and date: one shipping order can hold two
          legs of the same item arriving the same day, and React was dropping one of them
          ("Encountered two children with the same key, SPO-2026/08-0061-2026-08-01"). */}
      {refs.map((entry, index) => (
        <li
          key={`${entry.spo_number}-${entry.arrival_date ?? ''}-${index}`}
          className="text-sm"
        >
          <span className="tabular-nums">{entry.spo_number}</span>
          <span className="text-muted-foreground">
            {` · ${entry.qty}${entry.arrival_date ? ` · ${formatDateInMalaysia(entry.arrival_date)}` : ''}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 px-3 py-2.5 sm:grid-cols-[9.5rem_minmax(0,1fr)] sm:gap-3">
      <div className="text-2xs uppercase tracking-wide text-muted-foreground sm:pt-1.5">
        {label}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-2xs uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="truncate text-sm">{children}</div>
    </div>
  );
}

function Reason({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted-foreground break-words">{children}</p>;
}

/** Block, not inline: two empty states in one section must not run into one sentence. */
function Muted({ children }: { children: React.ReactNode }) {
  return <span className="block text-sm text-muted-foreground">{children}</span>;
}

/**
 * Hot, cold or not classified, in the plain words the captain asked for (19 August 2026:
 * "don't give me jargon like abc classification, just tell me hot selling or cold selling,
 * at project or retail"). Up to two pills - one per demand class - the same vocabulary the
 * board's flag chips use (`BoardTrailPopover.ItemFlagChips`).
 */
function HotSellingPills({ line }: { line: SupplyLine }) {
  if (line.classification_unavailable) {
    return (
      <span
        className={`${STATUS_PILL_BASE} normal-case ${statusPillClass('unknown')}`}
        title="No retail or project deliveries of this item in the last 12 months"
      >
        Not classified
      </span>
    );
  }
  const pills: Array<{ key: string; label: string; title: string; tone: string }> = [];
  if (line.is_dealer_hot_selling) {
    pills.push({
      key: 'dealer-hot',
      label: 'Dealer hot-selling',
      title: 'Dealer hot-selling: the shared pool is not offered',
      tone: 'pending',
    });
  } else if (line.dealer_classified) {
    pills.push({
      key: 'dealer-cold',
      label: 'Cold at retail',
      title: 'Cold at retail: the shared pool is offered as it is for any ordinary item',
      tone: 'draft',
    });
  }
  if (line.is_project_hot_selling) {
    pills.push({
      key: 'project-hot',
      label: 'Project hot-selling',
      title: 'Project hot-selling: the shared pool is offered while it stays available',
      tone: 'pending',
    });
  } else if (line.project_classified) {
    pills.push({
      key: 'project-cold',
      label: 'Cold at project',
      title: 'Cold at project: the shared pool is offered as it is for any ordinary item',
      tone: 'draft',
    });
  }
  if (pills.length === 0) return <Muted>Not hot-selling</Muted>;
  return (
    <>
      {pills.map((pill) => (
        <span
          key={pill.key}
          className={`${STATUS_PILL_BASE} normal-case ${statusPillClass(pill.tone)}`}
          title={pill.title}
        >
          {pill.label}
        </span>
      ))}
    </>
  );
}

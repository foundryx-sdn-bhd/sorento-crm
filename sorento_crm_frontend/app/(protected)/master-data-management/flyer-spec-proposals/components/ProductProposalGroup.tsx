'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { Pencil, Plus, Trash2 } from 'lucide-react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { SpecProposalReview } from '@/components/spec-proposals';
import type {
  SpecProposalKind,
  SpecProposalValue,
} from '@/components/spec-proposals';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { readable, readableValue } from '@/lib/spec-readable';
import { cn } from '@/lib/utils';

import type {
  FlyerSpecOutcome,
  FlyerSpecProductGroup,
  FlyerSpecProposal,
} from '../services/flyerSpecProposalService';
import { AddProposalRowDialog } from './AddProposalRowDialog';
import { ProposalValueEditor, fromDraft, toDraft } from './ProposalValueEditor';
import { distinctName } from '@/lib/product-display';

/**
 * One product's share of a flyer batch.
 *
 * The batch is grouped by PRODUCT and not by specification key, because that is
 * what a reviewer holding the paper is looking at: a card on page 7 is one
 * product saying five things, and a list ordered by key would ask them to jump
 * between three products to judge a single card.
 *
 * The rows themselves are the shared `components/spec-proposals` review, which
 * stays product-blind - this file owns the product identity, the selection
 * translation and nothing else. The shared component's selection is keyed by
 * `spec_key`, because a pasted text has no ids; a stored batch is applied by
 * PROPOSAL ID (L8), and a key is unique per product within a batch, so the
 * translation is exact and lives here rather than in the shared component.
 *
 * **The group is where the whole act happens** (UAC section G): a value the
 * reader got wrong is corrected in the cell it is read in, a key the flyer
 * states in a way no rule caught is added, and a row that belongs to the
 * neighbouring card is dismissed. All three change PROPOSALS - the apply is
 * still the one write, and it still names ids.
 *
 * Rows that have been through an apply do not come back to the table. There is
 * nothing left to decide about them, and a tick that cannot be applied reads as
 * a broken control - so they move to a plain list underneath carrying what
 * happened to each, which is also the only place a refusal survives a reload.
 */

/**
 * What a BULK apply may tick.
 *
 * `conflict` is in here as of the captain's amendment (AC-F.4, superseding
 * L6/L7 in part): a conflict is a value a person set, and ticking one plus a
 * confirm dialog that names how many of them are about to be replaced IS the
 * confirmation. Before the amendment this list was `['new', 'change']` and a
 * conflict could only be answered on the product's own Specifications tab,
 * which meant leaving the flyer for every one of them.
 *
 * `unchanged` and `suppressed` stay out, and the shared component refuses them
 * whatever is passed: the first would write what is already there, and the
 * second would overturn a removal somebody made on purpose.
 */
export const BULK_SELECTABLE_KINDS: readonly SpecProposalKind[] = [
  'new',
  'change',
  'conflict',
];

/** The two kinds that offer no in-place edit: there is nothing to write either way. */
const NOT_EDITABLE: readonly SpecProposalKind[] = ['unchanged', 'suppressed'];

/**
 * Why a proposal stating several values at once is not corrected here.
 *
 * The editor holds ONE value - one input, one dropdown, one number - so opening
 * it on `["rose_gold", "matt_black"]` shows the first, and saving stores the
 * first: the second value is gone, and nothing on screen said it would be. A
 * key that carries a list is a list on the product's own Specifications tab,
 * which is built for it, so the pencil says so instead of quietly dropping half
 * the value. Applying the row untouched still writes BOTH.
 */
const MULTI_VALUE_HINT =
  'Multi-value specifications are edited on the product page';

/** A proposal stating more than one value for one key. One is not a list. */
function isMultiValue(value: SpecProposalValue): boolean {
  return Array.isArray(value) && value.length > 1;
}

/**
 * What each outcome is called on screen. No reason code ever reaches a reader.
 *
 * `conflict_not_confirmed` reads as a removal rather than a refusal to replace,
 * because that is the only thing it can mean now: since the captain's amendment
 * (AC-F.1) a ticked `conflict` IS written, so the one row this outcome comes
 * back for is a key somebody tombstoned on that product. "Not replaced" sent
 * the reader looking for a confirmation they were never asked for.
 */
export const OUTCOME_LABEL: Record<FlyerSpecOutcome, string> = {
  applied: 'Applied',
  already_matches: 'Already stored',
  conflict_not_confirmed: 'Removed by a person',
  product_spec_bad_value: 'Value refused',
  product_not_found: 'Product not found',
  not_in_batch: 'Not in this batch',
};

/** The shared pill vocabulary, per outcome. Written is green, refused is not. */
const OUTCOME_PILL_KEY: Record<FlyerSpecOutcome, string> = {
  applied: 'done',
  already_matches: 'derived',
  conflict_not_confirmed: 'rejected',
  product_spec_bad_value: 'failed',
  product_not_found: 'failed',
  not_in_batch: 'voided',
};

export function OutcomePill({ outcome }: { outcome: FlyerSpecOutcome }) {
  return (
    <span
      className={cn(
        STATUS_PILL_BASE,
        'shrink-0 normal-case',
        statusPillClass(OUTCOME_PILL_KEY[outcome]),
      )}
      data-flyer-spec-outcome={outcome}
    >
      {OUTCOME_LABEL[outcome]}
    </span>
  );
}

/** "p. 3" / "p. 7, 11". A reviewer is holding the paper. */
function printedOn(pages: number[]): string {
  if (pages.length === 0) return 'Unknown page';
  return `p. ${pages.join(', ')}`;
}

export interface ProductProposalGroupProps {
  group: FlyerSpecProductGroup;
  /** Every ticked id in the batch. Held by the page, so a product cannot own it. */
  selectedIds: ReadonlySet<string>;
  /** The ticked ids OF THIS PRODUCT after the change. The page merges them in. */
  onSelectionChange: (idsForThisProduct: string[]) => void;
  /** True while the batch is being written: rows stay readable, ticks freeze. */
  disabled?: boolean;
  /**
   * Store a corrected value on one row. Resolving closes the editor; rejecting
   * leaves it open with what the reviewer typed, because the sentence the server
   * refused with is about the value they are still holding.
   */
  onEditValue?: (
    proposalId: string,
    value: SpecProposalValue,
  ) => Promise<unknown>;
  /** Take one row off the batch. Omitted when the reader may not write. */
  onDismiss?: (proposalId: string) => Promise<unknown>;
  /** Add a key the flyer stated in a way no rule caught, to THIS product. */
  onAddRow?: (input: {
    spec_key: string;
    value: SpecProposalValue;
  }) => Promise<unknown>;
  /** `{spec_key: value_labels}` (E.2), from the registry the screen already loaded. */
  valueLabels?: Record<string, Record<string, string>>;
}

export function ProductProposalGroup({
  group,
  selectedIds,
  onSelectionChange,
  disabled = false,
  onEditValue,
  onDismiss,
  onAddRow,
  valueLabels,
}: ProductProposalGroupProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [dismissing, setDismissing] = useState<FlyerSpecProposal | null>(null);
  const [adding, setAdding] = useState(false);
  const [addSaving, setAddSaving] = useState(false);

  const pending = useMemo(
    () => group.proposals.filter((row) => row.outcome === null),
    [group.proposals],
  );
  const settled = useMemo(
    () => group.proposals.filter((row) => row.outcome !== null),
    [group.proposals],
  );

  const selectable = useMemo(
    () => pending.filter((row) => BULK_SELECTABLE_KINDS.includes(row.kind)),
    [pending],
  );

  const selectedKeys = useMemo(
    () =>
      pending
        .filter((row) => selectedIds.has(row.id))
        .map((row) => row.spec_key),
    [pending, selectedIds],
  );

  // Keyed by spec_key because that is the only identity the SHARED component
  // knows a row by - it is deliberately id-free, so both the selection
  // translation and the render-prop callbacks come back here without a cast.
  const pendingByKey = useMemo(
    () => new Map(pending.map((row) => [row.spec_key, row])),
    [pending],
  );

  const tickedCount = selectable.filter((row) =>
    selectedIds.has(row.id),
  ).length;
  const allTicked = selectable.length > 0 && tickedCount === selectable.length;
  const someTicked = tickedCount > 0 && !allTicked;

  const toggleAll = (next: boolean) => {
    onSelectionChange(next ? selectable.map((row) => row.id) : []);
  };

  const startEdit = (row: FlyerSpecProposal) => {
    setEditingId(row.id);
    setDraft(toDraft(row.value));
  };

  const saveEdit = async () => {
    if (!editingId || !onEditValue) return;
    const row = pending.find((entry) => entry.id === editingId);
    if (!row) return;
    const value = fromDraft(draft, row.data_type);
    if (value === null) return;
    setBusyId(editingId);
    try {
      await onEditValue(editingId, value);
      setEditingId(null);
    } catch {
      // The hook has already said what went wrong. The editor stays open on the
      // value the reviewer typed, because that is what the message is about.
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section
      className="flex flex-col gap-3 rounded-lg border border-border p-4"
      data-flyer-spec-product={group.product_code}
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/master-data-management/products/${group.product_id}`}
              className="font-mono text-sm font-semibold text-foreground hover:underline"
              title={group.product_code}
            >
              {group.product_code}
            </Link>
            {group.viaProductCode && (
              // The card that filled this group's gaps was printed under a
              // different code (PLAN-flyer-family-proposals.md, R1) - this is
              // the only place on the row that says so.
              <Badge
                variant="secondary"
                className="shrink-0 font-mono text-2xs font-normal"
                data-flyer-spec-via={group.viaProductCode}
              >
                via {group.viaProductCode}
              </Badge>
            )}
          </div>
          {distinctName(group.product_code, group.product_name) ? (
            <p className="min-w-0 break-words text-sm text-muted-foreground">
              {group.product_name}
            </p>
          ) : null}
          <p className="mt-0.5 text-xs text-muted-foreground">
            {printedOn(group.pages)}
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-3">
          {/* The select-all for THIS product, and there is no select-all above it:
              a batch can name two hundred products, and one control that ticks
              every change across all of them is a control nobody reviewed. */}
          {selectable.length > 0 && (
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <Checkbox
                checked={
                  allTicked ? true : someTicked ? 'indeterminate' : false
                }
                disabled={disabled}
                onCheckedChange={(value) => toggleAll(!!value)}
                aria-label={`Select every applicable row for ${group.product_code}`}
                data-flyer-spec-select-all={group.product_code}
              />
              <span>
                {tickedCount} of {selectable.length} ticked
              </span>
            </label>
          )}

          {onAddRow && (
            <Button
              variant="outline"
              size="sm"
              disabled={disabled}
              onClick={() => setAdding(true)}
              data-flyer-spec-add={group.product_code}
            >
              <Plus className="size-4" />
              Add specification
            </Button>
          )}
        </div>
      </div>

      {pending.length > 0 ? (
        <SpecProposalReview
          proposals={pending}
          selectedKeys={selectedKeys}
          onSelectionChange={(keys) =>
            onSelectionChange(
              keys
                .map((key) => pendingByKey.get(key)?.id)
                .filter((id): id is string => Boolean(id)),
            )
          }
          selectableKinds={BULK_SELECTABLE_KINDS}
          disabled={disabled}
          valueLabels={valueLabels}
          renderValue={(proposal, read) => {
            const row = pendingByKey.get(proposal.spec_key);
            if (!row || row.id !== editingId) return read;
            return (
              <ProposalValueEditor
                label={row.label || readable(row.spec_key)}
                dataType={row.data_type}
                unit={row.unit}
                allowedValues={row.allowed_values}
                draft={draft}
                onDraftChange={setDraft}
                onSave={() => void saveEdit()}
                onCancel={() => setEditingId(null)}
                busy={busyId === row.id}
              />
            );
          }}
          rowActions={
            onEditValue || onDismiss
              ? (proposal) => {
                  const row = pendingByKey.get(proposal.spec_key);
                  if (!row) return null;
                  return (
                    <>
                      {onEditValue &&
                        !NOT_EDITABLE.includes(row.kind) &&
                        (isMultiValue(row.value) ? (
                          // The title sits on the wrapper: a disabled button
                          // takes no pointer events, so its own tooltip would
                          // never open.
                          <span
                            title={MULTI_VALUE_HINT}
                            className="inline-flex"
                          >
                            <Button
                              size="icon"
                              variant="ghost"
                              className="size-8"
                              disabled
                              aria-label={`Edit ${row.label || readable(row.spec_key)}`}
                            >
                              <Pencil className="size-4" />
                            </Button>
                          </span>
                        ) : (
                          <Button
                            size="icon"
                            variant="ghost"
                            className="size-8"
                            disabled={disabled || editingId === row.id}
                            aria-label={`Edit ${row.label || readable(row.spec_key)}`}
                            onClick={() => startEdit(row)}
                          >
                            <Pencil className="size-4" />
                          </Button>
                        ))}
                      {onDismiss && (
                        <Button
                          size="icon"
                          variant="ghost"
                          className="size-8 text-muted-foreground hover:text-destructive"
                          disabled={disabled}
                          aria-label={`Dismiss ${row.label || readable(row.spec_key)}`}
                          onClick={() => setDismissing(row)}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      )}
                    </>
                  );
                }
              : undefined
          }
        />
      ) : (
        <p
          className="text-sm text-muted-foreground"
          data-flyer-spec-nothing-pending
        >
          Every row this flyer proposed for this product has been through an
          apply.
        </p>
      )}

      {settled.length > 0 && (
        <div
          className="flex flex-col gap-2"
          data-flyer-spec-settled={group.product_code}
        >
          <p className="text-xs uppercase tracking-wide text-muted-foreground">
            Already decided
          </p>
          <ul className="flex flex-col gap-1.5 text-sm">
            {settled.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-foreground">
                  {row.label || readable(row.spec_key)}
                </span>
                <span className="text-muted-foreground">
                  {readableValue(row.value, row.unit ?? undefined, valueLabels?.[row.spec_key])}
                </span>
                <OutcomePill outcome={row.outcome as FlyerSpecOutcome} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <AlertDialog
        open={dismissing !== null}
        onOpenChange={(open) => !open && setDismissing(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Dismiss this proposal?</AlertDialogTitle>
            <AlertDialogDescription>
              It will not be applied. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {dismissing && (
            <p className="text-sm text-muted-foreground">
              {dismissing.label || readable(dismissing.spec_key)}{' '}
              {readableValue(
                dismissing.value,
                dismissing.unit ?? undefined,
                valueLabels?.[dismissing.spec_key],
              )}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              data-testid="fsp-dismiss-confirm"
              onClick={() => {
                const row = dismissing;
                setDismissing(null);
                if (row && onDismiss) void onDismiss(row.id).catch(() => {});
              }}
            >
              Dismiss
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {onAddRow && (
        <AddProposalRowDialog
          open={adding}
          onOpenChange={setAdding}
          productCode={group.product_code}
          productName={group.product_name}
          existingKeys={group.proposals.map((row) => row.spec_key)}
          saving={addSaving}
          onAdd={(input) => {
            setAddSaving(true);
            void onAddRow(input)
              .then(() => setAdding(false))
              .catch(() => {})
              .finally(() => setAddSaving(false));
          }}
        />
      )}
    </section>
  );
}

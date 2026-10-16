'use client';

import { useEffect, useMemo, useState } from 'react';
import { MoreHorizontal, Plus } from 'lucide-react';
import { arrayMove } from '@dnd-kit/sortable';
import type { DragEndEvent } from '@dnd-kit/core';
import {
  type ColumnDef,
  type SortingState,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Button } from '@/components/ui/button';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridScroller, DataGridTable } from '@/components/ui/data-grid-table';
import {
  DataGridTableDndRowHandle,
  DataGridTableDndRows,
} from '@/components/ui/data-grid-table-dnd-rows';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { readableValue } from '@/lib/spec-readable';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { SPEC_REGISTRY_QUERY_KEY } from '../hooks/useSpecRegistryQuery';
import { specKeyProductsKey } from '../hooks/useSpecKeyProductsQuery';
import { compileBuilder, ruleCells } from '../lib/ruleSentence';
import { isSavedRule, ruleUid } from '../lib/ruleUid';
import { builderProblem, ruleChoices, ruleMessage } from '../lib/ruleValidation';
import type {
  SpecDerivationRule,
  SpecRegistryKey,
  SpecRuleBuilder,
  SpecRuleProductFact,
  SpecRuleSizePick,
  SpecTryRuleRead,
} from '../types/productSpec.types';

/** A patched builder replaces the rule's `pattern` too (contract section 3: the
 *  stored rule is `{builder, pattern}`, and `pattern` must be whatever
 *  `compileBuilder` says THIS builder compiles to, never a stale one from
 *  before the inline edit). */
function withPattern(builder: SpecRuleBuilder): SpecDerivationRule['pattern'] {
  return compileBuilder(builder).pattern;
}

const SIZE_PICK_OPTIONS: { value: string; label: string }[] = [
  { value: '1', label: '1st number' },
  { value: '2', label: '2nd number' },
  { value: '3', label: '3rd number' },
  { value: '4', label: '4th number' },
  { value: 'L', label: 'Labelled: L' },
  { value: 'W', label: 'Labelled: W' },
  { value: 'H', label: 'Labelled: H' },
];

const PRODUCT_FACT_OPTIONS: { value: SpecRuleProductFact; label: string }[] = [
  { value: 'class', label: "The product's category's class" },
  { value: 'name', label: "What the product's name says it is" },
  { value: 'length', label: "The product's length" },
  { value: 'width', label: "The product's width" },
  { value: 'height', label: "The product's height" },
];

interface GridRow {
  rule: SpecDerivationRule;
  index: number;
  uid: string;
}

export interface SpecRulesGridProps {
  rules: SpecDerivationRule[];
  spec: SpecRegistryKey;
  registry: SpecRegistryKey[];
  mode: 'view' | 'edit';
  onChange: (rules: SpecDerivationRule[]) => void;
  onEdit: (index: number) => void;
  onAdd: () => void;
  /** Aligned to `rules` by index (AC-S1.8), from a Try it run at the tab level. */
  reads?: SpecTryRuleRead[] | null;
  winnerIndex?: number | null;
}

const editableFindKinds = new Set(['words', 'code', 'size', 'product']);

/** Tailwind's `sm` is 640px: below it the grid keeps Order, What to find and Value
 *  it sets (AC-S1.14) and scrolls inside its own frame. */
const BELOW_SM_QUERY = '(max-width: 639px)';

/**
 * The numeric value cell. It saves on blur, and only a real figure is saved: an emptied or
 * unreadable cell stays open with what was typed and says why (Escape leaves it as it was).
 */
function NumericValueCell({
  initial,
  onCommit,
  onCancel,
}: {
  initial: string;
  onCommit: (value: number) => void;
  onCancel: () => void;
}) {
  const [error, setError] = useState<string | undefined>(undefined);
  // Once a sentence has been shown it follows the typing in both directions.
  const [follow, setFollow] = useState(false);
  return (
    <NumberInput
      autoFocus
      defaultValue={initial}
      error={error}
      // A shown sentence follows the typing and goes only when the figure is usable.
      onChange={(e) => {
        if (follow) setError(numberFieldError(e.target.value));
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onCancel();
      }}
      onBlur={(e) => {
        const message = numberFieldError(e.target.value);
        if (message) {
          setError(message);
          setFollow(true);
          return;
        }
        onCommit(parseNumberDraft(e.target.value) as number);
      }}
    />
  );
}

function useBelowSm(): boolean {
  const [below, setBelow] = useState(false);
  useEffect(() => {
    const mql = window.matchMedia(BELOW_SM_QUERY);
    const update = () => setBelow(mql.matches);
    update();
    mql.addEventListener?.('change', update);
    return () => mql.removeEventListener?.('change', update);
  }, []);
  return below;
}

const EMPTY_SAVED_RULES: SpecDerivationRule[] = [];

/**
 * How a spec is read: a structured grid, one row per rule, one column per part
 * (AC-S1.14), on the shared `DataGrid` (fixed, resizable columns - fix round 1).
 * Never a sentence. Sortable headers; What to find and Value it sets edit in
 * place; drag handles only while sorted by Order (AC-S1.9), on the same
 * `DataGridTableDndRows` the sales order lines table uses.
 *
 * Remove is `spec_rule.remove` (D7, D8, fix round 1): a server-deferred action,
 * not a local timer - the record is the SPEC KEY (one pending removal per key
 * across every rule), same as the Choices-and-words and Other-names grids.
 */
export function SpecRulesGrid({
  rules,
  spec,
  registry,
  mode,
  onChange,
  onEdit,
  onAdd,
  reads,
  winnerIndex,
}: SpecRulesGridProps) {
  const [sorting, setSorting] = useState<SortingState>([{ id: 'order', desc: false }]);
  const [editingCell, setEditingCell] = useState<{ uid: string; column: 'find' | 'value' } | null>(
    null,
  );
  const [removingUid, setRemovingUid] = useState<string | null>(null);
  // S-11: an in-place edit the server would refuse is not applied; its reason
  // shows under the cell instead, in the server's own words.
  const [cellError, setCellError] = useState<{ uid: string; message: string } | null>(null);
  const belowSm = useBelowSm();
  const savedRules = spec.effective_rules ?? EMPTY_SAVED_RULES;

  const removal = useDeferredAction({
    actionKey: 'spec_rule.remove',
    entityType: 'spec_rule',
    entityId: spec.spec_key || null,
    verb: 'Removing',
    subject: '',
    surface: 'inline',
    watchFromMount: mode === 'edit',
    successMessage: 'Rule removed',
    // S-12: a removed rule re-reads the products it filled in, so the Products
    // tab and the Choices counts are stale until they are fetched again.
    invalidateKeys: [SPEC_REGISTRY_QUERY_KEY, specKeyProductsKey(spec.spec_key)],
    onCommitted: () => {
      const uid = removingUid;
      if (uid) onChange(rules.filter((r, i) => ruleUid(r, i) !== uid));
      setRemovingUid(null);
    },
  });

  const lookupSpec = useMemo(
    () => (specKey: string) => registry.find((k) => k.spec_key === specKey),
    [registry],
  );

  const wordChoices = useMemo(() => {
    const known = new Set<string>();
    for (const rule of rules) {
      if (rule.builder?.kind === 'words') rule.builder.words.forEach((w) => known.add(w));
      if (rule.builder?.kind === 'code') rule.builder.texts.forEach((w) => known.add(w));
    }
    Object.values(spec.synonyms ?? {}).forEach((words) =>
      words.forEach((w) => known.add(w.toUpperCase())),
    );
    return Array.from(known).sort();
  }, [rules, spec.synonyms]);

  const valueOptions = useMemo(
    () =>
      spec.allowed_values.map((value) => ({
        value,
        label: readableValue(value, undefined, spec.value_labels),
      })),
    [spec.allowed_values, spec.value_labels],
  );

  const data = useMemo<GridRow[]>(
    () => rules.map((rule, index) => ({ rule, index, uid: ruleUid(rule, index) })),
    [rules],
  );

  // B-5: one removal at a time. `start()` refuses a second one while the first is
  // parked, but `removingUid` would already have moved to the second row - the
  // server then removes the first rule while the draft drops the second.
  const removalBusy = removal.isPending || removal.isBlocked;

  const startRemoval = (row: GridRow) => {
    // A rule that exists only in this draft has nothing on the server to remove:
    // it is dropped here, and the next Save carries that.
    if (!isSavedRule(row.rule, savedRules)) {
      onChange(rules.filter((r, i) => ruleUid(r, i) !== row.uid));
      return;
    }
    if (removalBusy) return;
    setRemovingUid(row.uid);
    removal.start({ builder: row.rule.builder });
  };

  const moveBy = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= rules.length) return;
    onChange(arrayMove(rules, index, target));
  };

  const renderFindCell = (row: GridRow, cells: ReturnType<typeof ruleCells>) => {
    const uid = row.uid;
    const builder = row.rule.builder;
    const editing = editingCell?.uid === uid && editingCell.column === 'find';
    // Every patch recompiles `pattern` from the NEW builder (contract section 3):
    // a stale pattern left over from before the inline edit is what a save's own
    // builder/pattern comparison would refuse.
    const patchBuilder = (next: SpecRuleBuilder) => {
      const problem = builderProblem(next, ruleChoices(spec));
      if (problem) {
        setCellError({ uid, message: ruleMessage(problem, row.index + 1) });
        return;
      }
      setCellError(null);
      onChange(
        rules.map((r, i) => (ruleUid(r, i) === uid ? { ...r, builder: next, pattern: withPattern(next) } : r)),
      );
    };
    const errorForCell =
      cellError?.uid === uid ? (
        <p role="alert" className="text-xs text-destructive">
          {cellError.message}
        </p>
      ) : null;

    if (mode === 'edit' && editing && editableFindKinds.has(builder.kind)) {
      if (builder.kind === 'words') {
        return (
          <div className="flex flex-col gap-1">
            <SearchableMultiSelect
              value={builder.words}
              onChange={(words) => patchBuilder({ ...builder, words })}
              options={wordChoices.map((w) => ({ value: w, label: w }))}
              createOption={{
                label: (query) => <span>Add &ldquo;{query.toUpperCase()}&rdquo;</span>,
                onCreate: (query) => {
                  const upper = query.toUpperCase();
                  if (!builder.words.includes(upper)) {
                    patchBuilder({ ...builder, words: [...builder.words, upper] });
                  }
                },
              }}
            />
            {errorForCell}
          </div>
        );
      }
      if (builder.kind === 'code') {
        return (
          <div className="flex flex-col gap-1">
            <SearchableMultiSelect
              value={builder.texts}
              onChange={(texts) => patchBuilder({ ...builder, texts })}
              options={wordChoices.map((w) => ({ value: w, label: w }))}
              createOption={{
                label: (query) => <span>Add &ldquo;{query.toUpperCase()}&rdquo;</span>,
                onCreate: (query) => {
                  const upper = query.toUpperCase();
                  if (!builder.texts.includes(upper)) {
                    patchBuilder({ ...builder, texts: [...builder.texts, upper] });
                  }
                },
              }}
            />
            {errorForCell}
          </div>
        );
      }
      if (builder.kind === 'size') {
        return (
          <SearchableSelect
            value={String(builder.pick)}
            onChange={(value) => {
              const pick = (['L', 'W', 'H'].includes(value) ? value : Number(value)) as SpecRuleSizePick;
              patchBuilder({ ...builder, pick });
              setEditingCell(null);
            }}
            options={SIZE_PICK_OPTIONS}
          />
        );
      }
      if (builder.kind === 'product') {
        return (
          <SearchableSelect
            value={builder.fact}
            onChange={(value) => {
              patchBuilder({ ...builder, fact: value as SpecRuleProductFact });
              setEditingCell(null);
            }}
            options={PRODUCT_FACT_OPTIONS}
          />
        );
      }
    }
    // Ruling 13: a Number rule's blanks (before / after / written in / ignore
    // below) do not fit a cell, so its What to find opens the rule modal instead.
    const opensModal = builder.kind === 'number';
    const canEditFind = mode === 'edit' && (opensModal || editableFindKinds.has(builder.kind));
    const readForRow = reads?.[row.index];
    return (
      <button
        type="button"
        disabled={!canEditFind}
        onClick={() => {
          if (!canEditFind) return;
          if (opensModal) onEdit(row.index);
          else {
            setCellError(null);
            setEditingCell({ uid, column: 'find' });
          }
        }}
        className={`block w-full text-left ${canEditFind ? 'cursor-pointer hover:underline' : ''}`}
      >
        <span className="block truncate font-medium" title={cells.whatToFind.primary}>
          {cells.whatToFind.primary || '-'}
        </span>
        {cells.whatToFind.secondary.map((line) => (
          <span key={line} className="block truncate text-xs text-muted-foreground" title={line}>
            {line}
          </span>
        ))}
        {readForRow !== undefined && (
          <span className="block truncate text-xs text-muted-foreground">
            Reads:{' '}
            {readForRow?.value === null || readForRow?.value === undefined
              ? readForRow?.evidence || 'nothing'
              : readableValue(readForRow.value, spec.unit ?? undefined, spec.value_labels)}
          </span>
        )}
      </button>
    );
  };

  const renderValueCell = (row: GridRow, cells: ReturnType<typeof ruleCells>) => {
    const uid = row.uid;
    const builder = row.rule.builder;
    const canEdit =
      mode === 'edit' && (builder.kind === 'words' || builder.kind === 'code') && spec.data_type !== 'boolean';
    const editing = editingCell?.uid === uid && editingCell.column === 'value';
    const patchBuilderValue = (value: string | number | boolean) =>
      onChange(
        rules.map((r, i) =>
          ruleUid(r, i) === uid && (r.builder.kind === 'words' || r.builder.kind === 'code')
            ? { ...r, builder: { ...r.builder, value }, pattern: withPattern({ ...r.builder, value }) }
            : r,
        ),
      );
    if (editing && canEdit) {
      if (spec.data_type === 'numeric') {
        return (
          <NumericValueCell
            initial={String(builder.value ?? '')}
            onCancel={() => setEditingCell(null)}
            onCommit={(value) => {
              patchBuilderValue(value);
              setEditingCell(null);
            }}
          />
        );
      }
      return (
        <SearchableSelect
          value={String(builder.value)}
          onChange={(value) => {
            patchBuilderValue(value);
            setEditingCell(null);
          }}
          options={valueOptions}
        />
      );
    }
    return (
      <button
        type="button"
        disabled={!canEdit}
        onClick={() => canEdit && setEditingCell({ uid, column: 'value' })}
        className={`block w-full truncate text-left ${canEdit ? 'cursor-pointer hover:underline' : ''}`}
        title={cells.valueItSets}
      >
        {cells.valueItSets}
      </button>
    );
  };

  const isSortedByOrder = sorting.length === 0 || (sorting[0]?.id === 'order' && !sorting[0]?.desc);

  const columns = useMemo<ColumnDef<GridRow>[]>(() => {
    const base: ColumnDef<GridRow>[] = [
      {
        id: 'order',
        accessorFn: (row) => row.index,
        header: ({ column }) => <DataGridColumnHeader title="Order" column={column} />,
        cell: ({ row }) => <span className="tabular-nums">{row.original.index + 1}</span>,
        // Under sm only Order, What to find and Value it sets show, and all three fit
        // the card at 375 without scrolling sideways (S-13).
        size: belowSm ? 62 : 64,
        minSize: 44,
      },
      {
        id: 'where',
        accessorFn: (row) => ruleCells(row.rule.builder, spec, lookupSpec).whereToLook,
        header: ({ column }) => <DataGridColumnHeader title="Where to look" column={column} />,
        cell: ({ row }) => {
          const text = ruleCells(row.original.rule.builder, spec, lookupSpec).whereToLook;
          return (
            <span className="block truncate" title={text}>
              {text}
            </span>
          );
        },
        size: 130,
        minSize: 90,
      },
      {
        id: 'kind',
        accessorFn: (row) => ruleCells(row.rule.builder, spec, lookupSpec).kind,
        header: ({ column }) => <DataGridColumnHeader title="Kind" column={column} />,
        cell: ({ row }) => ruleCells(row.original.rule.builder, spec, lookupSpec).kind,
        size: 80,
        minSize: 64,
      },
      {
        id: 'find',
        accessorFn: (row) => ruleCells(row.rule.builder, spec, lookupSpec).whatToFind.primary,
        header: ({ column }) => <DataGridColumnHeader title="What to find" column={column} />,
        cell: ({ row }) => renderFindCell(row.original, ruleCells(row.original.rule.builder, spec, lookupSpec)),
        size: belowSm ? 140 : 220,
        minSize: 110,
      },
      {
        id: 'value',
        accessorFn: (row) => ruleCells(row.rule.builder, spec, lookupSpec).valueItSets,
        header: ({ column }) => <DataGridColumnHeader title="Value it sets" column={column} />,
        cell: ({ row }) => renderValueCell(row.original, ruleCells(row.original.rule.builder, spec, lookupSpec)),
        size: belowSm ? 104 : 130,
        minSize: 80,
      },
      {
        id: 'only_when',
        accessorFn: (row) => ruleCells(row.rule.builder, spec, lookupSpec).onlyWhen,
        header: ({ column }) => <DataGridColumnHeader title="Only when" column={column} />,
        cell: ({ row }) => {
          const text = ruleCells(row.original.rule.builder, spec, lookupSpec).onlyWhen;
          return (
            <span className="block truncate" title={text}>
              {text || '-'}
            </span>
          );
        },
        size: 140,
        minSize: 90,
      },
    ];

    if (mode !== 'edit') return base;

    const actionsColumn: ColumnDef<GridRow> = {
      id: 'actions',
      header: () => <span className="sr-only">Actions</span>,
      enableSorting: false,
      enableResizing: false,
      cell: ({ row }) => {
        const gridRow = row.original;
        if (removingUid === gridRow.uid && removal.pending) return removal.countdown;
        const saved = isSavedRule(gridRow.rule, savedRules);
        return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label={`Rule ${gridRow.index + 1} actions`}
                className="size-7 text-muted-foreground"
                disabled={removal.isBlocked}
              >
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => onEdit(gridRow.index)}>Edit</DropdownMenuItem>
              <DropdownMenuItem disabled={!isSortedByOrder} onClick={() => moveBy(gridRow.index, -1)}>
                Move up
              </DropdownMenuItem>
              <DropdownMenuItem disabled={!isSortedByOrder} onClick={() => moveBy(gridRow.index, 1)}>
                Move down
              </DropdownMenuItem>
              <DropdownMenuItem
                variant="destructive"
                disabled={saved && removalBusy}
                onClick={() => startRemoval(gridRow)}
              >
                Remove
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        );
      },
      size: 52,
      minSize: 52,
    };

    return [...base, actionsColumn];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    spec,
    lookupSpec,
    mode,
    editingCell,
    wordChoices,
    valueOptions,
    reads,
    removingUid,
    removal.countdown,
    removal.pending,
    removal.isBlocked,
    removalBusy,
    savedRules,
    cellError,
    isSortedByOrder,
    rules,
    onChange,
    onEdit,
    belowSm,
  ]);

  const reorderable = mode === 'edit' && isSortedByOrder;

  // The handle leads the row whenever it may be reordered - no toggle to press first.
  const tableColumns = useMemo<ColumnDef<GridRow>[]>(
    () =>
      reorderable
        ? [
            {
              id: 'drag_handle',
              header: () => <span className="sr-only">Reorder</span>,
              cell: ({ row }) => <DataGridTableDndRowHandle rowId={row.original.uid} />,
              size: 40,
              minSize: 40,
              enableResizing: false,
              enableSorting: false,
            },
            ...columns,
          ]
        : columns,
    [columns, reorderable],
  );

  const table = useReactTable({
    data,
    columns: tableColumns,
    state: {
      sorting,
      columnVisibility: { where: !belowSm, kind: !belowSm, only_when: !belowSm },
    },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: (row) => row.uid,
    columnResizeMode: 'onChange',
  });

  /** A drop saves straight into the draft at once, the same as the sales order
   *  lines table - no separate "reorder mode" to press into first. */
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = rules.findIndex((r, i) => ruleUid(r, i) === active.id);
    const to = rules.findIndex((r, i) => ruleUid(r, i) === over.id);
    if (from < 0 || to < 0) return;
    onChange(arrayMove(rules, from, to));
  };

  if (rules.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-md border border-dashed p-8 text-center">
        <p className="text-sm font-medium">No rules yet</p>
        <p className="text-sm text-muted-foreground">
          Nothing will fill this specification in until a rule is added.
        </p>
        <Button type="button" size="sm" variant="outline" onClick={onAdd}>
          Add a rule
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-hidden rounded-md border">
        {/* Add a rule sits at the top right, on the toolbar row above the column
            header, in read mode and edit mode (fix round 5, owner ruling 27 Sep
            2026: under 22 rows it was "too low"). */}
        <div
          data-testid="rules-grid-toolbar"
          className="flex flex-wrap items-center justify-end gap-2 border-b p-2"
        >
          <Button type="button" size="sm" variant="outline" onClick={onAdd}>
            <Plus className="size-4" aria-hidden />
            Add a rule
          </Button>
        </div>
        <DataGrid
          table={table}
          recordCount={data.length}
          isLoading={false}
          listingKey={null}
          tableLayout={{ width: 'fixed', columnsResizable: true }}
          rowClassName={(row) => (winnerIndex === row.index ? 'bg-primary/5' : undefined)}
        >
          {reorderable ? (
            <DataGridScroller>
              <DataGridTableDndRows
                handleDragEnd={handleDragEnd}
                dataIds={table.getRowModel().rows.map((row) => row.id)}
              />
            </DataGridScroller>
          ) : (
            <DataGridTable />
          )}
        </DataGrid>
      </div>
      <div data-testid="rules-grid-footer" className="p-2 text-xs text-muted-foreground">
        {rules.length} rule{rules.length === 1 ? '' : 's'}.
      </div>
    </div>
  );
}

export default SpecRulesGrid;

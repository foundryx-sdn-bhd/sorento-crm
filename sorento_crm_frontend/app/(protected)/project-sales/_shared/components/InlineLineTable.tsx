'use client';

import * as React from 'react';
import { Check, Plus, StickyNote, Trash2, Undo2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { DatePicker } from '@/components/ui/date-picker';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { focusIsInsideFloating } from '@/components/common/floatingAncestry';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';

/**
 * Every field of a line, as strings.
 *
 * Strings all the way through on purpose: money and quantity are strings from the API to
 * the API, and turning one into a number on its way to a cell is how `392.85` comes back
 * as `392.85000000000002`. Fields with no column of their own (notes) ride along in here
 * untouched, so a save never drops what the table does not draw.
 */
export type InlineDraft = Record<string, string>;

export type InlineCellKind =
  | 'text'
  | 'number'
  | 'select'
  | 'searchable-select'
  | 'checkbox'
  | 'date'
  | 'derived';

/**
 * A ticked box, as a string, because a draft is strings all the way through.
 *
 * The value is exported rather than left to each call site to invent: two screens spelling
 * "on" differently (`'1'` here, `'true'` there) would each read the other's saved row as off.
 */
export const INLINE_CHECKED = 'true';

/** Tolerant on the way in, so a draft seeded `'1'` from an older payload still reads as on. */
export function isInlineChecked(value: string | undefined): boolean {
  return value === INLINE_CHECKED || value === '1';
}

export interface InlineLineColumn<TRow> {
  /** Also the draft field name, and half of a cell's focus coordinate. */
  key: string;
  header: string;
  /** Pixels. The table scrolls sideways rather than squeezing a cell below this. */
  width: number;
  kind: InlineCellKind;
  align?: 'start' | 'end';
  placeholder?: string;
  /** `select`: the whole option set, filtered in the popover. */
  options?: SearchableSelectOption[];
  /** `select`: the options read failed; the menu says so instead of "No matches". */
  loadError?: unknown;
  onRetry?: () => void;
  /** `searchable-select`: debounced server search. */
  fetchOptions?: (query: string) => Promise<SearchableSelectOption[]>;
  /**
   * `searchable-select`: the option currently held. Async mode fetches one page at a
   * time, so without this the trigger reads empty on a saved row until somebody searches
   * for the thing that is already selected.
   */
  resolveSelected?: (row: TRow | null, draft: InlineDraft) => SearchableSelectOption | undefined;
  /**
   * `select` / `searchable-select`: the OTHER draft fields this option decides.
   *
   * Picking a catalogue item is one decision that answers five cells (its description, its
   * brand, its unit, its list price), and re-typing them by hand is both work and a chance to
   * disagree with the record. The column says what the option means; the table applies it
   * (see `applyOptionFill`), so the rule is one rule rather than one per screen.
   *
   * Return every key the option speaks for, INCLUDING the ones it leaves blank: a key returned
   * empty is how the previous choice's leftovers get cleared. Return `{}` (e.g. for a cleared
   * selection) to leave the row exactly as it is.
   */
  onOptionSelected?: (
    option: SearchableSelectOption | null,
    draft: InlineDraft,
  ) => InlineDraft;
  /**
   * `derived`: recomputed from the live draft on every keystroke, never stored.
   *
   * A node rather than a string so a cell can say something that is not a number - a
   * rate-only line prints the words `rate only` where its total would be, and both a blank
   * and `RM 0.00` would be read as a fault rather than as a deliberate exclusion.
   *
   * `index` is the row's position in the table, so a column can BE the position: an item
   * number is the row it sits on, not a string somebody types and has to renumber by hand
   * after every insert.
   *
   * `row` is the STORED row, or null for one staged in this edit session. A column that draws a
   * fact the server decided - a product's chosen photograph, say - reads it from here and
   * renders nothing while it is null, the same way `annotate` already does. Deriving such a fact
   * from the draft would mean a second implementation in the browser that eventually disagrees.
   */
  derive?: (draft: InlineDraft, index: number, row: TRow | null) => React.ReactNode;
  /**
   * A unit printed INSIDE the cell, before the value - `RM` on a price.
   *
   * On the input rather than in the header because a column of bare numbers beside another
   * column of bare numbers (a price beside a percentage) gives the reader nothing to tell
   * them apart mid-scroll, and a header scrolls out of sight on a table of ninety rows.
   *
   * It is decoration only: the draft still holds the plain decimal string the API wants, so
   * no call site has to strip a currency symbol back off before saving.
   */
  prefix?: string;
  /** Caps what can be typed, so a column with a server length limit cannot 422. */
  maxLength?: number;
  /** Per-cell shape check. A message marks the cell; it does not raise a toast. */
  validate?: (value: string) => string | null;
  /** Read-only annotation under the editor: list price, quoted price, badges. */
  annotate?: (row: TRow | null, draft: InlineDraft) => React.ReactNode;
  /**
   * A totals cell under this column, in the table's own footer.
   *
   * Same rule the shared DataGrid follows: a total belongs under the column it sums. Put beside
   * the version chips instead - "RM 1,805,907.02  Issued by ...  Opened ..." - it reads as one
   * more fact in a row of metadata, and nothing says WHICH column it totals.
   *
   * Handed the LIVE drafts, in row order, rather than the saved rows: a total that only moves
   * after a save contradicts the cells above it while somebody is typing, and the first thing
   * a person does with a quantity is check what it did to the bottom line.
   */
  footer?: (drafts: InlineDraft[]) => React.ReactNode;
  /**
   * How the raw value reads when the table cannot be edited. An input must hold the
   * string the API wants (`900.00`), but a frozen version should still say `RM 900.00`.
   */
  formatReadOnly?: (value: string, row: TRow | null) => string;
}

/** A field that deserves keeping but not a column of its own. */
export interface InlineRowDetail {
  key: string;
  label: string;
  placeholder?: string;
  rows?: number;
}

/**
 * A heading that opens a section, carried by the line it sits above.
 *
 * It renders as a row spanning every column INSIDE this table rather than as a separate
 * list, so a band cannot drift away from the lines it introduces: reorder, filter or
 * paginate the lines and the heading travels with the one that owns it. That is also why
 * the label lives on the line and not in a table of its own.
 *
 * Started from "Add a section" in the footer, beside "Add a line": a section IS a line that
 * carries a heading, so both buttons do the same thing and only the caret lands differently.
 */
export interface InlineBandRow {
  /** Draft field holding the heading. Non-empty means this line opens a section. */
  key: string;
  label: string;
  placeholder?: string;
  maxLength?: number;
}

/** One row as it currently stands, identified by the key the table draws it under. */
export interface InlineStagedRow {
  /** The row's id when it came from `rows`, or the table's own key for one added here. */
  rowKey: string;
  draft: InlineDraft;
}

/**
 * Edit-view mode: the SCREEN owns the changes, and the table writes nothing.
 *
 * Per-row saving is right for a table that is the whole feature, and wrong for one section of a
 * document with a single Save over all of it: the client's words were "every addition of line
 * doesn't trigger a save, cause now i delete each line, then you ask me to confirm, then when i
 * add line, you also trigger save, very annoying". So with `staging` set the table stops
 * committing on blur, drops the per-row tick and the "Unsaved" pill, and reports every change at
 * once instead. `onCreate` / `onUpdate` / `onDelete` are never called.
 *
 * Removal stages too: the row stays on screen struck-through and restorable, and the
 * confirm-before-delete rule is honoured at the screen's Save, which is where something is
 * actually destroyed. See `PLAN-quotation-edit-view.md`.
 */
export interface InlineStaging<TRow> {
  /** Every row's key and draft, in display order, whenever any of them changes. */
  onChange: (rows: InlineStagedRow[]) => void;
  /** Staged for removal: struck through, not editable, and gone only once the screen saves. */
  isRemoved: (row: TRow) => boolean;
  /** Stage or unstage a removal. Deliberately no dialog: Save is the commit point. */
  toggleRemove: (rowKey: string, row: TRow | null) => void;
}

export interface InlineLineTableProps<TRow> {
  rows: TRow[];
  getRowId: (row: TRow) => string;
  columns: InlineLineColumn<TRow>[];
  /** A saved row as an editable draft. Must include every field `onUpdate` will send. */
  toDraft: (row: TRow) => InlineDraft;
  /** A brand-new row's starting draft. Also the "untouched" comparison. */
  emptyDraft: () => InlineDraft;
  /**
   * The three writes. The table hands over a draft and nothing else: shaping the payload
   * stays at the call site, so the request that leaves the browser is the same one the
   * dialog used to send.
   *
   * Required unless `staging` is set, which replaces all three with one report upwards.
   */
  onCreate?: (draft: InlineDraft) => Promise<void>;
  onUpdate?: (row: TRow, draft: InlineDraft) => Promise<void>;
  onDelete?: (row: TRow) => Promise<void>;
  /** Set to hand the changes to the screen instead of writing them. See `InlineStaging`. */
  staging?: InlineStaging<TRow>;
  /** Cross-field rules. Keys are column keys, so a message lands on the cell at fault. */
  validateRow?: (draft: InlineDraft) => Record<string, string>;
  rowDetail?: InlineRowDetail;
  band?: InlineBandRow;
  /** Names a row for labels and for the delete confirmation, e.g. "line 2" or a code. */
  describeRow: (row: TRow | null, index: number) => string;
  readOnly?: boolean;
  isLoading?: boolean;
  addLabel?: string;
  /** Only rendered when the table has a `band`. */
  addSectionLabel?: string;
  /** Shown in place of rows when there are none. The header and the add row stay put. */
  emptyHint?: string;
  deleteDescription?: (row: TRow, index: number) => string;
  /**
   * Every row's live draft, in row order, whenever one of them changes.
   *
   * For a total that lives OUTSIDE the table (a document header, say). The table owns the
   * drafts, so anything summing them has to be handed them - reading the server rows instead
   * is what made the header disagree with the footer directly under it.
   */
  onDraftsChange?: (drafts: InlineDraft[]) => void;
  /**
   * A filter over the LIVE drafts. Rows that fail it are HIDDEN, not removed.
   *
   * The distinction is the whole design: filtering the `rows` prop instead would tear the
   * non-matching rows out of the table's state - their unsaved drafts with them - so
   * searching mid-edit would silently discard what somebody had typed on line 34. A hidden
   * row keeps its state, keeps its place in the drafts array (totals still sum the WHOLE
   * table), and keeps its item number, because "item 12" printed on the customer's paper
   * must not become "item 3" just because a search is narrowing the view.
   */
  rowFilter?: (row: TRow | null, draft: InlineDraft) => boolean;
  /** Shown in place of rows when the filter hides all of them. */
  filterEmptyHint?: string;
}

interface RowState {
  draft: InlineDraft;
  /** What the server last accepted. Escape and the dirty check both read this. */
  committed: InlineDraft;
}

interface PendingDelete<TRow> {
  rowKey: string;
  /** Null for a row that was added but never saved: there is nothing to ask the server. */
  row: TRow | null;
  index: number;
}

const NEW_ROW_PREFIX = 'new:';

/** `date` cells store the API's ISO string; the picker works in local `Date` objects. */
function isoToLocalDate(value: string): Date | undefined {
  const trimmed = value.trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return undefined;
  const [year, month, day] = trimmed.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

function localDateToIso(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function sameDraft(a: InlineDraft, b: InlineDraft): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) {
    if ((a[key] ?? '') !== (b[key] ?? '')) return false;
  }
  return true;
}

/**
 * Lines as a spreadsheet, not as a form behind a button.
 *
 * The behaviour being copied is Odoo's, and it is a behaviour rather than a layout: "Add a
 * line" puts an empty row at the bottom and the caret in its first cell, every cell is
 * edited where it sits, Tab walks across and rolls onto the next row, Enter drops down a
 * row, Escape puts one cell back. Adding four lines costs four rows of typing instead of
 * four rounds of open-fill-submit-reopen.
 *
 * WHY NOT THE SHARED `DataGrid`: three of its guarantees are the opposite of what an
 * editor wants. It holds skeleton rows until the column-preferences query settles, which
 * for a sub-table inside a detail page means the lines flash before they arrive; its cell
 * contract is `truncate` + `title`, which is right for reading a listing and wrong for a
 * cell that has to give its whole width to an input; and with no rows it replaces the
 * header with an empty message, while the ask here is that the header and the add row
 * survive an empty table. It also has nowhere to put an UNSAVED draft row, which is the
 * whole trick. So the table below is written out, without `table-fixed`, sized by explicit
 * per-column widths inside an `overflow-x-auto` box so a wide line table scrolls in its
 * own gutter and the page never moves sideways at 375px. It also renders a row that SPANS
 * every column (`band`), which a column-driven grid has nowhere to put.
 *
 * WHEN A ROW SAVES: when focus leaves it, for a row that is dirty and valid. That is the
 * Odoo rule and it needs no button. There is a button anyway, one tick per dirty row,
 * because a phone has no Tab key and tapping between cells of the same row would otherwise
 * never save anything.
 *
 * Saving goes through whatever the call site passes as `onCreate` / `onUpdate`, one row at
 * a time, which is what the per-line endpoints already do.
 */
export function InlineLineTable<TRow>({
  rows,
  getRowId,
  columns,
  toDraft,
  emptyDraft,
  onCreate,
  onUpdate,
  onDelete,
  staging,
  validateRow,
  rowDetail,
  band,
  describeRow,
  readOnly = false,
  isLoading = false,
  addLabel = 'Add a line',
  addSectionLabel = 'Add a section',
  emptyHint,
  deleteDescription,
  onDraftsChange,
  rowFilter,
  filterEmptyHint,
}: InlineLineTableProps<TRow>) {
  const [states, setStates] = React.useState<Record<string, RowState>>({});
  const [newRowKeys, setNewRowKeys] = React.useState<string[]>([]);
  const [errors, setErrors] = React.useState<Record<string, Record<string, string>>>({});
  const [saving, setSaving] = React.useState<string[]>([]);
  const [pendingDelete, setPendingDelete] = React.useState<PendingDelete<TRow> | null>(null);
  /**
   * Rows whose band editor is open although the heading is still empty.
   *
   * A band cannot be started by typing into a row that has no heading yet, so "Add a section"
   * appends a row with an empty one open. Left empty, it closes again when the caret leaves the
   * row: a mis-click should not strand a blank heading above a line.
   */
  const [bandOpen, setBandOpen] = React.useState<string[]>([]);

  /**
   * Whether the screen owns the changes instead of this table writing them.
   *
   * Read once, near the top, because it decides three separate behaviours further down (no
   * commit, no delete dialog, no per-row save affordances) and they have to agree.
   */
  const isStaged = Boolean(staging);

  const nextNewId = React.useRef(0);
  /**
   * A per-MOUNT prefix on the keys of rows added here.
   *
   * In staged mode the screen hands those rows straight back through `rows`, so after a tab
   * switch this table remounts holding keys it minted last time. Restarting the counter at 1
   * would hand a freshly added row the key an existing one already answers to.
   */
  const instanceKey = React.useId();
  const activeRowKey = React.useRef<string | null>(null);
  const cellHosts = React.useRef(new Map<string, HTMLElement | null>());
  // Read by handlers that fire before React has re-rendered with the newest state.
  const statesRef = React.useRef(states);
  statesRef.current = states;
  /**
   * The double-save guard, in a ref rather than in `saving` state.
   *
   * Several paths commit the same row: focus leaving it, the tick beside it, "Add a line"
   * flushing it before appending, a notes popover closing. Two of them can land before a
   * re-render, and a state-based check would still read "idle" and CREATE the row twice.
   */
  const inFlight = React.useRef(new Set<string>());

  const editableColumns = React.useMemo(
    () => columns.filter((column) => column.kind !== 'derived'),
    [columns],
  );
  const rowByKey = React.useMemo(() => {
    const map = new Map<string, TRow>();
    rows.forEach((row) => map.set(getRowId(row), row));
    return map;
  }, [getRowId, rows]);
  const rowKeys = React.useMemo(
    () => [
      ...rows.map(getRowId),
      // Filtered, because in staged mode the screen echoes an added row back through `rows`
      // while this table is still tracking it. Without this the row is drawn twice, under one
      // key, and every keystroke lands in both copies.
      ...newRowKeys.filter((key) => !rowByKey.has(key)),
    ],
    [getRowId, newRowKeys, rowByKey, rows],
  );

  /**
   * Re-seed a saved row from the server, unless the person is mid-edit on it. Without the
   * dirty check a refetch triggered by a neighbouring row would wipe what they are typing.
   */
  React.useEffect(() => {
    setStates((previous) => {
      const next: Record<string, RowState> = {};
      let changed = false;
      rows.forEach((row) => {
        const key = getRowId(row);
        const server = toDraft(row);
        const held = previous[key];
        if (held && !sameDraft(held.draft, held.committed)) {
          next[key] = held;
          return;
        }
        if (held && sameDraft(held.committed, server)) {
          next[key] = held;
          return;
        }
        next[key] = { draft: server, committed: server };
        changed = true;
      });
      Object.keys(previous).forEach((key) => {
        if (key.startsWith(NEW_ROW_PREFIX)) next[key] = previous[key];
      });
      if (!changed && Object.keys(next).length === Object.keys(previous).length) {
        return previous;
      }
      return next;
    });
  }, [getRowId, rows, toDraft]);

  /**
   * The rows as they are RIGHT NOW, in the order they are drawn, each with the key it is drawn
   * under. A total sums the drafts; a staged save needs the keys as well, because they are what
   * tells an existing line from one added a moment ago.
   */
  const orderedRows = React.useMemo(
    () =>
      rowKeys
        .map((rowKey) => ({ rowKey, draft: states[rowKey]?.draft }))
        .filter((entry): entry is InlineStagedRow => Boolean(entry.draft)),
    [rowKeys, states],
  );
  /**
   * What a total must sum: the rows that will still be there afterwards.
   *
   * A row staged for removal is deliberately left out. It is on its way off the quotation, and
   * a footer that still counted it would state a figure nobody is ever going to be charged.
   */
  const orderedDrafts = React.useMemo(
    () =>
      orderedRows
        .filter((entry) => {
          const row = rowByKey.get(entry.rowKey);
          return !(row && staging?.isRemoved(row));
        })
        .map((entry) => entry.draft),
    [orderedRows, rowByKey, staging],
  );

  /**
   * Has every row been turned into a draft yet?
   *
   * On the first render after `rows` arrives, the seeding effect has not run, so there are rows
   * on screen and no drafts behind them. Reporting THAT would tell the screen the table is empty,
   * and a screen that stores what it is told would wipe the very rows it just handed down.
   */
  const isSeeded = orderedRows.length === rowKeys.length;

  React.useEffect(() => {
    if (!isSeeded) return;
    onDraftsChange?.(orderedDrafts);
  }, [isSeeded, onDraftsChange, orderedDrafts]);

  const reportStaged = staging?.onChange;
  React.useEffect(() => {
    if (!isSeeded) return;
    reportStaged?.(orderedRows);
  }, [isSeeded, orderedRows, reportStaged]);

  const cellId = (rowKey: string, columnKey: string) => `${rowKey}::${columnKey}`;

  const focusCell = React.useCallback((rowKey: string, columnKey: string) => {
    const host = cellHosts.current.get(cellId(rowKey, columnKey));
    if (!host) return false;
    const target = host.querySelector<HTMLElement>(
      'input:not([type="hidden"]), textarea, button',
    );
    if (!target) return false;
    target.focus();
    if (target instanceof HTMLInputElement) target.select();
    return true;
  }, []);

  /** Several fields of one row in one write, so a fill is one render and one undo step. */
  const setDraftValues = React.useCallback((rowKey: string, patch: InlineDraft) => {
    if (Object.keys(patch).length === 0) return;
    setStates((previous) => {
      const held = previous[rowKey];
      if (!held) return previous;
      return { ...previous, [rowKey]: { ...held, draft: { ...held.draft, ...patch } } };
    });
  }, []);

  const setDraftValue = React.useCallback(
    (rowKey: string, columnKey: string, value: string) => {
      setDraftValues(rowKey, { [columnKey]: value });
    },
    [setDraftValues],
  );

  /**
   * What a picked option means for the rest of its row, applied as the option states it.
   *
   * THE RULE, decided by the client and deliberately blunt: the option WINS, every time. A
   * re-pick resets every field it speaks for, over a hand-typed value included, and a key it
   * returns empty is cleared. The alternative (keep what looks hand-edited) was put to them
   * with its cost - one product means one set of fields, and picking it twice gives the same
   * row both times - and they chose predictability over protecting an edit. The tradeoff is
   * known and accepted: an edit made BEFORE a re-pick is lost. Do not quietly add a
   * "preserve my edits" exception here; it would make the fill unexplainable at the desk.
   */
  const applyOptionFill = React.useCallback(
    (rowKey: string, column: InlineLineColumn<TRow>, option: SearchableSelectOption | null) => {
      const decide = column.onOptionSelected;
      if (!decide) return;
      const held = statesRef.current[rowKey];
      if (!held) return;
      setDraftValues(rowKey, decide(option, held.draft));
    },
    [setDraftValues],
  );

  /** Folds an empty band editor away once the caret has left the row that opened it. */
  const closeEmptyBand = React.useCallback(
    (rowKey: string) => {
      if (!band) return;
      const held = statesRef.current[rowKey];
      if (held && (held.draft[band.key] ?? '').trim() !== '') return;
      setBandOpen((keys) => keys.filter((key) => key !== rowKey));
    },
    [band],
  );

  const dropRow = React.useCallback((rowKey: string) => {
    if (activeRowKey.current === rowKey) activeRowKey.current = null;
    setNewRowKeys((keys) => keys.filter((key) => key !== rowKey));
    setBandOpen((keys) => keys.filter((key) => key !== rowKey));
    setStates((previous) => {
      const next = { ...previous };
      delete next[rowKey];
      return next;
    });
    setErrors((previous) => {
      const next = { ...previous };
      delete next[rowKey];
      return next;
    });
  }, []);

  /** Everything wrong with a row, cell by cell. Empty object means it can be saved. */
  const collectErrors = React.useCallback(
    (draft: InlineDraft): Record<string, string> => {
      const found: Record<string, string> = { ...(validateRow?.(draft) ?? {}) };
      columns.forEach((column) => {
        if (!column.validate) return;
        const message = column.validate(draft[column.key] ?? '');
        if (message) found[column.key] = message;
      });
      return found;
    },
    [columns, validateRow],
  );

  const commitRow = React.useCallback(
    async (rowKey: string) => {
      // Nothing is written in staged mode: the screen already has every change, and the one
      // Save it offers is the only thing that reaches the server.
      if (isStaged) return;
      const held = statesRef.current[rowKey];
      if (!held) return;
      if (inFlight.current.has(rowKey)) return;

      const isNew = rowKey.startsWith(NEW_ROW_PREFIX);
      const row = rowByKey.get(rowKey) ?? null;

      // An added row nobody has typed into yet is simply not ready. It is left exactly
      // where it is: not saved, and NOT marked red for cells the user has not reached.
      //
      // It used to be discarded here, on the theory that an untouched row was a
      // mis-click. That theory cost the client a row every time: clicking the product
      // dropdown blurs the row before the popover exists, the row read as untouched and
      // abandoned, and it vanished under the cursor mid-click. A row that disappears
      // while you are using it is far worse than one that waits. Removing a row is the
      // Remove button's job, and it asks first.
      if (isNew && sameDraft(held.draft, emptyDraft())) return;

      if (!isNew && sameDraft(held.draft, held.committed)) {
        setErrors((previous) => ({ ...previous, [rowKey]: {} }));
        return;
      }

      const found = collectErrors(held.draft);
      setErrors((previous) => ({ ...previous, [rowKey]: found }));
      if (Object.keys(found).length > 0) return;

      inFlight.current.add(rowKey);
      setSaving((previous) => [...previous, rowKey]);
      try {
        if (isNew) {
          await onCreate?.(held.draft);
          dropRow(rowKey);
        } else if (row) {
          await onUpdate?.(row, held.draft);
          setStates((previous) => {
            const current = previous[rowKey];
            if (!current) return previous;
            // What was SENT becomes committed, not whatever the draft holds now: somebody
            // who kept typing during the request still has unsaved work, and marking it
            // saved would lose it at the next refetch.
            return { ...previous, [rowKey]: { ...current, committed: held.draft } };
          });
        }
      } catch {
        // The mutation hooks already say what went wrong. Leaving the row dirty is the
        // point: what the person typed is still on screen and still saveable.
      } finally {
        inFlight.current.delete(rowKey);
        setSaving((previous) => previous.filter((key) => key !== rowKey));
      }
    },
    [collectErrors, dropRow, emptyDraft, isStaged, onCreate, onUpdate, rowByKey],
  );

  /**
   * A new row at the bottom, with the caret in it.
   *
   * `withBand` is the ONLY difference between "Add a line" and "Add a section": a section is a
   * line that carries a heading, so it is the same append with the heading editor open and the
   * caret in it, and the line's own cells waiting underneath. Two buttons side by side beat the
   * per-row control this replaced, which asked people to add a line first and then work out
   * which icon on it turned that line into a heading.
   */
  const addRow = React.useCallback(
    (options?: { withBand?: boolean }) => {
      const pending = activeRowKey.current;
      if (pending) void commitRow(pending);

      nextNewId.current += 1;
      const key = `${NEW_ROW_PREFIX}${instanceKey}:${nextNewId.current}`;
      const draft = emptyDraft();
      setStates((previous) => ({ ...previous, [key]: { draft, committed: draft } }));
      setNewRowKeys((keys) => [...keys, key]);

      const openBand = Boolean(options?.withBand && band);
      if (openBand) setBandOpen((keys) => [...keys, key]);

      // The caret belongs in the new row, not on the button that made it: on the heading when
      // that is what was asked for, otherwise on the row's first cell.
      const target = openBand && band ? band.key : editableColumns[0]?.key;
      if (target) {
        window.setTimeout(() => {
          activeRowKey.current = key;
          focusCell(key, target);
        }, 0);
      }
    },
    [band, commitRow, editableColumns, emptyDraft, focusCell, instanceKey],
  );

  /**
   * A row saves when focus lands on a DIFFERENT row, or leaves the table for good.
   *
   * Rows rather than cells, because a line is one write: committing per cell would fire
   * five requests for one line and, on a new row, five creates. And a popover counts as
   * still being on the row: the product picker and the notes box render in a portal, so a
   * focus move into one would otherwise read as leaving.
   */
  const handleFocusIn = (event: React.FocusEvent<HTMLDivElement>) => {
    const rowKey =
      (event.target as HTMLElement).closest<HTMLElement>('[data-row-key]')?.dataset.rowKey ??
      null;
    if (rowKey === activeRowKey.current) return;
    const leaving = activeRowKey.current;
    activeRowKey.current = rowKey;
    if (leaving) {
      void commitRow(leaving);
      closeEmptyBand(leaving);
    }
  };

  /**
   * Is this where focus landed still part of the table, or of something it opened?
   *
   * `focusIsInsideFloating` also covers `[data-radix-focus-guard]` - Radix's tab-trap
   * sentinel, appended as a sibling of every portal root rather than inside any of them.
   * A real browser can hop focus through one for a tick while a picker's modal Popover
   * settles its own focus trap; reading that transient hop as "left the table" would
   * discard the row before the hop resolves.
   */
  const stillInside = (node: Element | null, container: HTMLElement) => {
    if (!node) return false;
    if (container.contains(node)) return true;
    return focusIsInsideFloating(node);
  };

  const handleFocusOut = (event: React.FocusEvent<HTMLDivElement>) => {
    const container = event.currentTarget;
    const next = event.relatedTarget as HTMLElement | null;
    if (next) {
      if (stillInside(next, container)) return;
      leaveRow();
      return;
    }
    // No relatedTarget at all, which is what opening a Radix popover looks like: the
    // trigger blurs before the popover exists, so there is nothing to point at yet.
    // Reading that as "the caret left the table" discarded a freshly added row the
    // moment its product dropdown was clicked. Ask again once the browser has settled
    // on a focus owner, and only leave if it really is outside.
    window.setTimeout(() => {
      if (stillInside(document.activeElement, container)) return;
      leaveRow();
    }, 0);
  };

  const leaveRow = () => {
    const leaving = activeRowKey.current;
    activeRowKey.current = null;
    if (leaving) {
      void commitRow(leaving);
      closeEmptyBand(leaving);
    }
  };

  const moveFocus = (rowKey: string, columnIndex: number, delta: number) => {
    const rowIndex = rowKeys.indexOf(rowKey);
    if (rowIndex < 0) return false;
    let nextColumn = columnIndex + delta;
    let nextRow = rowIndex;
    if (nextColumn >= editableColumns.length) {
      nextColumn = 0;
      nextRow += 1;
    } else if (nextColumn < 0) {
      nextColumn = editableColumns.length - 1;
      nextRow -= 1;
    }
    if (nextRow < 0 || nextRow >= rowKeys.length) return false;
    return focusCell(rowKeys[nextRow], editableColumns[nextColumn].key);
  };

  const handleCellKeyDown = (
    event: React.KeyboardEvent<HTMLDivElement>,
    rowKey: string,
    column: InlineLineColumn<TRow>,
    columnIndex: number,
  ) => {
    const isPicker = column.kind === 'select' || column.kind === 'searchable-select';

    if (event.key === 'Tab') {
      if (moveFocus(rowKey, columnIndex, event.shiftKey ? -1 : 1)) event.preventDefault();
      return;
    }
    // Enter on a picker trigger belongs to the picker: it is how the popover opens.
    if (event.key === 'Enter' && !isPicker) {
      event.preventDefault();
      const rowIndex = rowKeys.indexOf(rowKey);
      const below = rowKeys[rowIndex + 1];
      if (below) {
        focusCell(below, column.key);
      } else {
        (event.target as HTMLElement).blur();
      }
      return;
    }
    if (event.key === 'Escape' && !isPicker) {
      const held = statesRef.current[rowKey];
      if (!held) return;
      event.preventDefault();
      setDraftValue(rowKey, column.key, held.committed[column.key] ?? '');
    }
  };

  const requestDelete = (rowKey: string, row: TRow | null, index: number) => {
    // Staged mode destroys nothing here, so there is nothing to confirm: the row is marked,
    // stays on screen struck through, and the screen's Save asks once for all of them.
    if (staging) {
      staging.toggleRemove(rowKey, row);
      return;
    }
    const held = statesRef.current[rowKey];
    // An untouched added row destroys nothing, so it goes without a question. A row that
    // was typed into gets the same confirmation a saved line gets.
    if (!row && held && sameDraft(held.draft, emptyDraft())) {
      dropRow(rowKey);
      return;
    }
    setPendingDelete({ rowKey, row, index });
  };

  // Sized for the buttons that actually render (remove, the save tick, and a note when the
  // call site asks for one), or three icons would spill past a fixed 88.
  const actionsWidth = 88 + (rowDetail ? 36 : 0);
  const totalWidth =
    columns.reduce((sum, column) => sum + column.width, 0) + (readOnly ? 0 : actionsWidth);

  if (isLoading) return <Skeleton className="h-24 w-full" />;

  return (
    <div className="min-w-0 space-y-2">
      <div
        /**
         * `relative` is load-bearing, not decoration.
         *
         * Every `sr-only` label in here is `position: absolute` (that is what the utility does).
         * With a STATIC scroller those absolute boxes resolve against a containing block OUTSIDE
         * it, so they escape its clipping and extend the DOCUMENT's scrollable width - which made
         * the whole page scroll ~1,500px sideways at phone width while the table itself scrolled
         * correctly in its own gutter. Making the scroller a containing block keeps them inside.
         *
         * `w-full` + `max-w-full` pin the box to the space it is given rather than to the table's
         * intrinsic width, so an ancestor that forgets `min-w-0` cannot stretch it either.
         */
        className="relative w-full min-w-0 max-w-full overflow-x-auto rounded-lg border border-border"
        onFocus={handleFocusIn}
        onBlur={handleFocusOut}
      >
        <table className="w-full text-sm" style={{ minWidth: totalWidth }}>
          <thead>
            <tr className="border-b border-border bg-muted/40 text-xs text-muted-foreground">
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  style={{ width: column.width, minWidth: column.width }}
                  className={`px-2 py-2 font-medium ${
                    column.align === 'end' ? 'text-end' : 'text-start'
                  }`}
                >
                  {column.header}
                </th>
              ))}
              {!readOnly && (
                <th
                  scope="col"
                  style={{ width: actionsWidth, minWidth: actionsWidth }}
                  className="px-2 py-2"
                >
                  <span className="sr-only">Row actions</span>
                </th>
              )}
            </tr>
          </thead>

          <tbody>
            {rowKeys.length === 0 && (
              <tr>
                <td
                  colSpan={columns.length + (readOnly ? 0 : 1)}
                  className="px-3 py-6 text-center text-sm text-muted-foreground"
                >
                  {emptyHint ?? 'No lines yet.'}
                </td>
              </tr>
            )}

            {/* The filter hides rows AT RENDER; the map below still runs over every key so
                `rowIndex` stays each row's true position - item numbers must not renumber
                because a search is narrowing the view. State, drafts and totals are
                untouched: a hidden row is still part of the table. */}
            {rowKeys.length > 0 &&
              rowFilter &&
              rowKeys.every((key) => {
                const held = states[key];
                return held
                  ? !rowFilter(rowByKey.get(key) ?? null, held.draft)
                  : true;
              }) && (
                <tr>
                  <td
                    colSpan={columns.length + (readOnly ? 0 : 1)}
                    className="px-3 py-6 text-center text-sm text-muted-foreground"
                  >
                    {filterEmptyHint ?? 'No line matches.'}
                  </td>
                </tr>
              )}

            {rowKeys.map((rowKey, rowIndex) => {
              const row = rowByKey.get(rowKey) ?? null;
              const held = states[rowKey];
              if (!held) return null;
              if (rowFilter && !rowFilter(row, held.draft)) return null;
              // Once a row has been challenged, its marks track what is typed, so fixing
              // the cell clears the mark instead of leaving it red until the next save.
              //
              // In staged mode there is no per-row save to be challenged BY, and a person who
              // only finds out at Save that line 34 needs a description has to hunt for it. So
              // a row that holds anything at all is marked live - but an added row nobody has
              // typed into yet is not, because it is not wrong, it is empty.
              const challenged = isStaged
                ? !sameDraft(held.draft, emptyDraft())
                : Object.keys(errors[rowKey] ?? {}).length > 0;
              const rowErrors = challenged ? collectErrors(held.draft) : {};
              const dirty = rowKey.startsWith(NEW_ROW_PREFIX)
                ? true
                : !sameDraft(held.draft, held.committed);
              const label = describeRow(row, rowIndex);
              const bandValue = band ? (held.draft[band.key] ?? '') : '';
              const showBand =
                Boolean(band) && (bandValue.trim() !== '' || bandOpen.includes(rowKey));
              // Staged for removal: still drawn, so the removal can be seen and taken back,
              // but not typed into - editing a line on its way out is meaningless.
              const removed = Boolean(row && staging?.isRemoved(row));
              const cellsReadOnly = readOnly || removed;
              let editableIndex = -1;

              return (
                <React.Fragment key={rowKey}>
                  {showBand && band && (
                    // Same `data-row-key` as the line below it, so the heading counts as part of
                    // that row: focus moving into it must not read as leaving, or the row would
                    // commit mid-edit and the heading would never be saved with its line.
                    <tr data-row-key={rowKey} className="border-b border-border/60 bg-muted/40">
                      <td colSpan={columns.length + (readOnly ? 0 : 1)} className="px-2 py-1.5">
                        {cellsReadOnly ? (
                          <span
                            className="block truncate text-sm font-semibold"
                            title={bandValue}
                          >
                            {bandValue}
                          </span>
                        ) : (
                          <div
                            ref={(element) => {
                              cellHosts.current.set(cellId(rowKey, band.key), element);
                            }}
                            className="min-w-0"
                          >
                            <Input
                              value={bandValue}
                              aria-label={`${band.label} on ${label}`}
                              title={bandValue}
                              placeholder={band.placeholder}
                              maxLength={band.maxLength}
                              className="h-8 max-w-md text-sm font-semibold"
                              onChange={(event) =>
                                setDraftValue(rowKey, band.key, event.target.value)
                              }
                            />
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                  <tr
                    data-row-key={rowKey}
                    className={`border-b border-border/60 align-top last:border-b-0 ${
                      removed ? 'text-muted-foreground line-through opacity-70' : ''
                    }`}
                  >
                    {columns.map((column) => {
                      if (column.kind !== 'derived') editableIndex += 1;
                      const columnIndex = editableIndex;
                      const value = held.draft[column.key] ?? '';
                      const message = rowErrors[column.key];
                      const describedBy = message
                        ? `${cellId(rowKey, column.key)}-error`
                        : undefined;

                      return (
                        <td
                          key={column.key}
                          style={{ width: column.width, minWidth: column.width }}
                          className={`px-2 py-1.5 ${column.align === 'end' ? 'text-end' : ''}`}
                        >
                          <div
                            ref={(element) => {
                              cellHosts.current.set(cellId(rowKey, column.key), element);
                            }}
                            className="min-w-0"
                            onKeyDown={
                              column.kind === 'derived' || cellsReadOnly
                                ? undefined
                                : (event) =>
                                    handleCellKeyDown(event, rowKey, column, columnIndex)
                            }
                          >
                            <InlineCell
                              column={column}
                              row={row}
                              draft={held.draft}
                              rowIndex={rowIndex}
                              cellId={cellId(rowKey, column.key)}
                              value={value}
                              invalid={Boolean(message)}
                              describedBy={describedBy}
                              label={`${column.header} on ${label}`}
                              readOnly={cellsReadOnly}
                              onChange={(next) => setDraftValue(rowKey, column.key, next)}
                              onOptionChange={(option) =>
                                applyOptionFill(rowKey, column, option)
                              }
                            />
                            {column.annotate?.(row, held.draft)}
                            {message && (
                              <p
                                id={describedBy}
                                className="mt-1 text-xs text-destructive"
                              >
                                {message}
                              </p>
                            )}
                          </div>
                        </td>
                      );
                    })}

                    {!readOnly && (
                      <td className="px-2 py-1.5 no-underline">
                        <div className="flex items-center justify-end gap-0.5">
                          {rowDetail && !removed && (
                            <RowDetailPopover
                              detail={rowDetail}
                              label={label}
                              value={held.draft[rowDetail.key] ?? ''}
                              onChange={(next) =>
                                setDraftValue(rowKey, rowDetail.key, next)
                              }
                              onClosed={() => void commitRow(rowKey)}
                            />
                          )}
                          {/* The tick and the "Unsaved" pill belong to per-row saving. In
                              staged mode every row is unsaved until the screen's one Save, so
                              saying it per row says nothing. */}
                          {dirty && !isStaged && (
                            <Button
                              type="button"
                              mode="icon"
                              variant="ghost"
                              size="sm"
                              aria-label={`Save ${label}`}
                              disabled={saving.includes(rowKey)}
                              onClick={() => void commitRow(rowKey)}
                            >
                              <Check className="size-3.5 text-primary" />
                            </Button>
                          )}
                          {removed ? (
                            <Button
                              type="button"
                              mode="icon"
                              variant="ghost"
                              size="sm"
                              aria-label={`Restore ${label}`}
                              onClick={() => staging?.toggleRemove(rowKey, row)}
                            >
                              <Undo2 className="size-3.5 text-primary" />
                            </Button>
                          ) : (
                            <Button
                              type="button"
                              mode="icon"
                              variant="ghost"
                              size="sm"
                              aria-label={`Remove ${label}`}
                              onClick={() => requestDelete(rowKey, row, rowIndex)}
                            >
                              <Trash2 className="size-3.5 text-destructive" />
                            </Button>
                          )}
                        </div>
                        {dirty && !isStaged && (
                          <p className="mt-0.5 text-end text-[11px] text-muted-foreground">
                            Unsaved
                          </p>
                        )}
                        {removed && (
                          <p className="mt-0.5 text-end text-[11px] text-muted-foreground">
                            Removed on save
                          </p>
                        )}
                      </td>
                    )}
                  </tr>
                </React.Fragment>
              );
            })}
          </tbody>

          {(columns.some((column) => column.footer) || !readOnly) && (
            <tfoot>
              {columns.some((column) => column.footer) && (
                <tr className="border-t border-border bg-muted/30 text-sm font-semibold">
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      style={{ width: column.width, minWidth: column.width }}
                      className={`px-2 py-2 ${column.align === 'end' ? 'text-end' : 'text-start'}`}
                    >
                      {column.footer ? column.footer(orderedDrafts) : null}
                    </td>
                  ))}
                  {!readOnly && <td className="px-2 py-2" />}
                </tr>
              )}
              {!readOnly && (
                <tr className="border-t border-border">
                  <td colSpan={columns.length + 1} className="px-2 py-1.5">
                    {/* Side by side, and wrapping rather than overflowing at 375px. */}
                    <div className="flex flex-wrap items-center gap-1">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="text-primary hover:text-primary"
                        onClick={() => addRow()}
                      >
                        <Plus className="size-4" aria-hidden />
                        {addLabel}
                      </Button>
                      {band && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="text-primary hover:text-primary"
                          onClick={() => addRow({ withBand: true })}
                        >
                          <Plus className="size-4" aria-hidden />
                          {addSectionLabel}
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </tfoot>
          )}
        </table>
      </div>

      <ConfirmDeleteDialog
        open={Boolean(pendingDelete)}
        onOpenChange={(next) => !next && setPendingDelete(null)}
        title="Confirm delete"
        description={
          !pendingDelete
            ? ''
            : pendingDelete.row
              ? (deleteDescription?.(pendingDelete.row, pendingDelete.index) ??
                `Remove ${describeRow(pendingDelete.row, pendingDelete.index)}? This action cannot be undone.`)
              : 'Discard this line? It was never saved, and this action cannot be undone.'
        }
        onDelete={async () => {
          if (!pendingDelete) return;
          if (pendingDelete.row) await onDelete?.(pendingDelete.row);
          else dropRow(pendingDelete.rowKey);
        }}
        onSuccess={() => setPendingDelete(null)}
        successMessage="Line removed"
      />
    </div>
  );
}

function InlineCell<TRow>({
  column,
  row,
  draft,
  rowIndex,
  cellId,
  value,
  invalid,
  describedBy,
  label,
  readOnly,
  onChange,
  onOptionChange,
}: {
  column: InlineLineColumn<TRow>;
  row: TRow | null;
  draft: InlineDraft;
  rowIndex: number;
  cellId: string;
  value: string;
  invalid: boolean;
  describedBy?: string;
  label: string;
  readOnly: boolean;
  onChange: (value: string) => void;
  onOptionChange: (option: SearchableSelectOption | null) => void;
}) {
  const alignment = column.align === 'end' ? 'text-end tabular-nums' : '';

  if (column.kind === 'derived') {
    return (
      <span className={`block text-sm font-medium ${alignment}`}>
        {column.derive?.(draft, rowIndex, row) ?? ''}
      </span>
    );
  }

  if (readOnly) {
    const shown =
      column.kind === 'select' || column.kind === 'searchable-select'
        ? (column.resolveSelected?.(row, draft)?.label ?? (value ? value : '-'))
        : column.kind === 'checkbox'
          ? // A frozen version reads rather than clicks, and a greyed-out box is easy to
            // mistake for one that simply has not loaded.
            (isInlineChecked(value) ? 'Yes' : '-')
          : value
            ? (column.formatReadOnly?.(value, row) ?? value)
            : '-';
    return (
      <span className={`block truncate text-sm ${alignment}`} title={shown}>
        {shown}
      </span>
    );
  }

  if (column.kind === 'select' || column.kind === 'searchable-select') {
    const selectId = `${cellId.replace(/\W+/g, '-')}-picker`;
    return (
      <>
        {/* SearchableSelect forwards `id`, not arbitrary aria props, so the accessible
            name has to come from a real label. */}
        <label className="sr-only" htmlFor={selectId}>
          {label}
        </label>
        <SearchableSelect
          id={selectId}
          size="sm"
          clearable
          value={value}
          onChange={onChange}
          onOptionChange={onOptionChange}
          options={column.kind === 'select' ? column.options : undefined}
          loadError={column.kind === 'select' ? column.loadError : undefined}
          onRetry={column.kind === 'select' ? column.onRetry : undefined}
          fetchOptions={
            column.kind === 'searchable-select' ? column.fetchOptions : undefined
          }
          selectedOption={column.resolveSelected?.(row, draft)}
          placeholder={column.placeholder ?? 'Select'}
          emptyMessage="No matches"
        />
      </>
    );
  }

  if (column.kind === 'checkbox') {
    return (
      <Checkbox
        size="sm"
        aria-label={label}
        checked={isInlineChecked(value)}
        onCheckedChange={(next) => onChange(next === true ? INLINE_CHECKED : '')}
      />
    );
  }

  if (column.kind === 'date') {
    return (
      <DatePicker
        id={`${cellId.replace(/\W+/g, '-')}-date`}
        ariaLabel={label}
        value={isoToLocalDate(value)}
        onChange={(date) => onChange(date ? localDateToIso(date) : '')}
        placeholder={column.placeholder ?? 'DD/MM/YYYY'}
        inputClassName="h-8"
        className="w-full"
      />
    );
  }

  const input = (
    <Input
      value={value}
      aria-label={label}
      aria-invalid={invalid || undefined}
      aria-describedby={describedBy}
      title={value}
      maxLength={column.maxLength}
      placeholder={column.placeholder}
      // Money and quantity stay text: a number input hands back a re-serialised float,
      // and the contract on both endpoints is a decimal STRING.
      inputMode={column.kind === 'number' ? 'decimal' : undefined}
      className={`h-8 ${alignment} ${column.prefix ? 'ps-9' : ''} ${
        invalid ? 'border-destructive focus-visible:ring-destructive/30' : ''
      }`}
      onChange={(event) => onChange(event.target.value)}
    />
  );

  if (!column.prefix) return input;

  // `pointer-events-none` so clicking the unit still lands in the field: a symbol that
  // swallows the click makes the left third of a money cell feel broken.
  return (
    <div className="relative">
      <span className="pointer-events-none absolute inset-y-0 start-0 flex items-center ps-2.5 text-xs text-muted-foreground">
        {column.prefix}
      </span>
      {input}
    </div>
  );
}

/**
 * The fields with no column: a paragraph does not belong in a cell six characters wide,
 * and dropping it would lose what somebody wrote about why a price was agreed.
 */
function RowDetailPopover({
  detail,
  label,
  value,
  onChange,
  onClosed,
}: {
  detail: InlineRowDetail;
  label: string;
  value: string;
  onChange: (value: string) => void;
  onClosed: () => void;
}) {
  const [open, setOpen] = React.useState(false);
  const filled = value.trim().length > 0;

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) onClosed();
      }}
    >
      <PopoverTrigger asChild>
        <Button
          type="button"
          mode="icon"
          variant="ghost"
          size="sm"
          aria-label={`${detail.label} on ${label}`}
        >
          <StickyNote
            className={`size-3.5 ${filled ? 'text-primary' : 'text-muted-foreground'}`}
          />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 space-y-1.5">
        <p className="text-xs font-medium">{detail.label}</p>
        <Textarea
          autoFocus
          aria-label={`${detail.label} on ${label}`}
          rows={detail.rows ?? 3}
          value={value}
          placeholder={detail.placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      </PopoverContent>
    </Popover>
  );
}

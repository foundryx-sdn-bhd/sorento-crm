'use client';

import * as React from 'react';
import { Check, ChevronDown, Loader2, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Popover, PopoverContent, PopoverPortal, PopoverTrigger } from '@/components/ui/popover';
import { SEARCH_DEBOUNCE_MS } from '@/hooks/useDebouncedSearch';
import {
  selectTriggerVariants,
  type SelectTriggerSize,
} from '@/components/common/select-trigger-variants';
import { isInsideOpenDialog } from '@/components/common/floatingAncestry';
import { PopoverScrollLock } from '@/components/common/PopoverScrollLock';
import {
  SelectLoadFailure,
  hasLoadError,
  selectLoadFailurePlaceholder,
} from '@/components/common/SelectLoadFailure';

export type SearchableMultiSelectOption = {
  value: string;
  label: string;
  /** Optional grouping header (renders one CommandGroup per distinct group). */
  group?: string;
  /** Free-text used by the fuzzy filter (static mode); falls back to label + description. */
  searchText?: string;
  /** Optional secondary line under the label. */
  description?: string;
  /** When set, render a small badge after the label (e.g. "owned by X"). */
  badgeText?: string;
  /** Per-option disabled. */
  disabled?: boolean;
};

export type SearchableMultiSelectProps = {
  value: string[];
  onChange: (value: string[]) => void;
  /** Static mode: full option set, filtered client-side. Mutually exclusive with `fetchOptions`. */
  options?: SearchableMultiSelectOption[];
  /** Async mode: server-search, debounced 300ms, stale-dropped. Empty query on open = first page. */
  fetchOptions?: (query: string) => Promise<SearchableMultiSelectOption[]>;
  /**
   * Async mode: the currently-selected options, so trigger chips + checkmarks survive when the
   * selected values aren't in the fetched page.
   */
  selectedOptions?: SearchableMultiSelectOption[];
  /** Trigger size - shared with Radix SelectTrigger. Default `md`. */
  size?: SelectTriggerSize;
  /**
   * Size the MENU to its widest option instead of to the trigger. Option labels wrap
   * in both modes either way. Same contract as `SearchableSelect.wrapOptions`, kept in
   * step so the two halves of one standard cannot behave differently.
   */
  wrapOptions?: boolean;
  /** Forwarded to the trigger so a <Label htmlFor> can point at it, as SearchableSelect does. */
  id?: string;
  placeholder?: string;
  emptyMessage?: string;
  /** The options query's `error`; see the same prop on `SearchableSelect` (lever L5). */
  loadError?: unknown;
  /** Refetch the options; shown as Retry next to a non-refusal `loadError`. */
  onRetry?: () => void;
  disabled?: boolean;
  className?: string;
  triggerClassName?: string;
  /** Override the trigger body entirely (default = removable chips). */
  renderTriggerLabel?: (selected: SearchableMultiSelectOption[]) => React.ReactNode;
  /**
   * Render the option body (everything right of the checkbox). Parity with SearchableSelect.
   * Needed by pickers whose rows carry their own interactive bits - a tooltip trigger, say - 
   * which `badgeText` / `description` cannot express.
   */
  renderOption?: (opt: SearchableMultiSelectOption) => React.ReactNode;
  /**
   * Replace the whole trigger - for pickers that hang off an icon button rather than a select
   * box, where the default trigger + chevron would be wrong. Must render one focusable element.
   */
  renderTrigger?: (state: {
    selected: SearchableMultiSelectOption[];
    open: boolean;
    disabled: boolean;
  }) => React.ReactNode;
  /**
   * Show a clear-all X in the trigger while something is chosen (parity with
   * `SearchableSelect.clearable`). Opt-in: the chips' own X and the list's "Clear all"
   * already empty it, so existing callers are untouched.
   */
  clearable?: boolean;
  /** Notified whenever the search text changes, in both modes. */
  onSearchChange?: (query: string) => void;
  /**
   * A first row offering to add the typed text as a value not yet in the list -
   * mirrors `SearchableSelect.createOption`. Opt-in, so every existing caller is
   * untouched: a picker over a fixed vocabulary must never invite someone to
   * invent a member of it. Rendered ABOVE the option list (the words a rule
   * multi-select offers are found by scanning, so the one action a search text
   * does not already match belongs first, not buried under it). Picking it adds
   * the value and clears the search box so the trigger's chips show it at once;
   * the popover stays open, matching how every other pick here behaves.
   */
  createOption?: {
    label: (query: string) => React.ReactNode | null;
    onCreate: (query: string) => void;
  };
};

/** cmdk needs a unique value per item; this one names no real option. */
const CREATE_ITEM_VALUE = '__searchable-multi-select-create__';

export function SearchableMultiSelect({
  value,
  onChange,
  options,
  fetchOptions,
  selectedOptions,
  size,
  wrapOptions = false,
  id,
  placeholder = 'Select...',
  emptyMessage = 'No results found.',
  loadError,
  onRetry,
  disabled = false,
  className,
  triggerClassName,
  renderTriggerLabel,
  renderOption,
  renderTrigger,
  clearable = false,
  onSearchChange,
  createOption,
}: SearchableMultiSelectProps) {
  const isAsync = typeof fetchOptions === 'function';
  const [open, setOpen] = React.useState(false);

  // See the identical, longer comment in SearchableSelect.tsx: this never asks Radix
  // `Popover` for `modal` (its `hideOthers` broke a DIFFERENT dialog's own Update button
  // here specifically, since this component's list stays open across multiple picks -
  // ContactAccessTypesAdmin.portalForms.test.tsx). `PopoverScrollLock` applies just the
  // scroll-lock half, scoped to Dialog-nested triggers, detected once from the trigger's
  // own DOM position rather than declared per call site.
  const triggerElRef = React.useRef<HTMLElement | null>(null);
  const [triggerInsideDialog, setTriggerInsideDialog] = React.useState(false);
  const setTriggerRef = React.useCallback((node: HTMLElement | null) => {
    triggerElRef.current = node;
  }, []);
  React.useEffect(() => {
    setTriggerInsideDialog(isInsideOpenDialog(triggerElRef.current));
  }, []);
  const needsDialogScrollLock = renderTrigger ? true : triggerInsideDialog;
  // `PopoverScrollLock` additionally requires `open`: RemoveScroll locks the body for as
  // long as it is MOUNTED, not for as long as anything is visibly open, so this component's
  // own popover-open state has to gate it directly rather than only through this flag.

  const [asyncOptions, setAsyncOptions] = React.useState<SearchableMultiSelectOption[]>([]);
  const [asyncError, setAsyncError] = React.useState<unknown>(null);
  const [loading, setLoading] = React.useState(false);
  const [query, setQuery] = React.useState('');
  const lastQueryRef = React.useRef<string>(' ');

  const runFetch = React.useCallback(
    async (q: string) => {
      if (!fetchOptions) return;
      lastQueryRef.current = q;
      setLoading(true);
      try {
        const items = await fetchOptions(q);
        if (lastQueryRef.current !== q) return;
        setAsyncError(null);
        setAsyncOptions(items);
      } catch (error) {
        if (lastQueryRef.current !== q) return;
        // A failed read is not an empty list (L5): keep the error so the menu says so.
        setAsyncError(error ?? new Error('These options could not be loaded.'));
        setAsyncOptions([]);
      } finally {
        if (lastQueryRef.current === q) setLoading(false);
      }
    },
    [fetchOptions],
  );

  React.useEffect(() => {
    if (!isAsync || !open) return;
    const t = setTimeout(() => void runFetch(query), query === '' ? 0 : SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [isAsync, open, query, runFetch]);

  React.useEffect(() => {
    if (open) return;
    setQuery('');
    lastQueryRef.current = ' ';
  }, [open]);

  const baseOptions = React.useMemo(
    () => (isAsync ? asyncOptions : (options ?? [])),
    [isAsync, asyncOptions, options],
  );
  const selectedSet = React.useMemo(() => new Set(value), [value]);

  // Resolve selected options for chips: prefer fetched/static, fall back to selectedOptions prop.
  const chosen = React.useMemo(() => {
    const byValue = new Map<string, SearchableMultiSelectOption>();
    for (const o of baseOptions) if (selectedSet.has(o.value)) byValue.set(o.value, o);
    if (isAsync) {
      for (const o of selectedOptions ?? []) {
        if (selectedSet.has(o.value) && !byValue.has(o.value)) byValue.set(o.value, o);
      }
    }
    // Preserve `value` ordering; unknown values render as bare chips.
    return value.map((v) => byValue.get(v) ?? { value: v, label: v });
  }, [baseOptions, selectedSet, isAsync, selectedOptions, value]);

  // Static mode filters here rather than delegating to cmdk, because Select all must act on
  // exactly the rows the user can see - and cmdk never tells us which those are. Async mode is
  // already filtered server-side, so its options pass through untouched.
  const visibleOptions = React.useMemo(() => {
    if (isAsync) return baseOptions;
    const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return baseOptions;
    return baseOptions.filter((opt) => {
      const haystack = (
        opt.searchText ?? `${opt.label} ${opt.description ?? ''} ${opt.badgeText ?? ''}`
      ).toLowerCase();
      return tokens.every((t) => haystack.includes(t));
    });
  }, [isAsync, baseOptions, query]);

  const grouped = React.useMemo(() => {
    const map = new Map<string, SearchableMultiSelectOption[]>();
    for (const opt of visibleOptions) {
      const key = opt.group ?? '';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(opt);
    }
    return Array.from(map.entries());
  }, [visibleOptions]);

  // Select all targets the visible, non-disabled rows only - never a disabled option, and never
  // a row the active search has filtered out.
  const selectable = React.useMemo(
    () => visibleOptions.filter((o) => !o.disabled),
    [visibleOptions],
  );
  const allVisibleSelected =
    selectable.length > 0 && selectable.every((o) => selectedSet.has(o.value));

  const isFiltered = !isAsync && query.trim().length > 0;
  const selectAllLabel = allVisibleSelected
    ? isFiltered
      ? `Clear ${selectable.length} matching`
      : 'Clear all'
    : isFiltered
      ? `Select ${selectable.length} matching`
      : isAsync
        ? // Async only ever holds the loaded page, so promising "all" would be a lie.
          `Select all ${selectable.length} loaded`
        : `Select all (${selectable.length})`;

  const toggleAll = () => {
    const visibleValues = selectable.map((o) => o.value);
    if (allVisibleSelected) {
      const drop = new Set(visibleValues);
      onChange(value.filter((v) => !drop.has(v)));
    } else {
      const merged = [...value];
      const have = new Set(value);
      for (const v of visibleValues) if (!have.has(v)) merged.push(v);
      onChange(merged);
    }
  };

  const failure = hasLoadError(loadError) ? loadError : isAsync ? asyncError : null;
  const failed = hasLoadError(failure);
  const retryFailure = hasLoadError(loadError) ? onRetry : () => void runFetch(query);

  const misconfigured = !isAsync && options === undefined;
  const isDisabled = disabled || misconfigured;

  const toggle = (v: string) => {
    if (selectedSet.has(v)) onChange(value.filter((x) => x !== v));
    else onChange([...value, v]);
  };
  const remove = (v: string) => onChange(value.filter((x) => x !== v));

  const handleQueryChange = (q: string) => {
    setQuery(q);
    onSearchChange?.(q);
  };

  // Resolved once per render so the empty-state branch and the row itself cannot
  // disagree, same reasoning as `SearchableSelect`. Never offered for a word the
  // list already holds, in any case - as an option or as a chip already chosen.
  const createQuery = query.trim();
  const folded = createQuery.toLowerCase();
  const alreadyThere =
    !!folded &&
    (value.some((v) => v.toLowerCase() === folded) ||
      baseOptions.some((o) => o.value.toLowerCase() === folded || o.label.toLowerCase() === folded) ||
      (selectedOptions ?? []).some((o) => o.value.toLowerCase() === folded || o.label.toLowerCase() === folded));
  const createLabel =
    createOption && createQuery && !alreadyThere ? createOption.label(createQuery) : null;
  const createValue = createOption
    ? () => {
        createOption.onCreate(createQuery);
        setQuery('');
      }
    : undefined;

  return (
    // See `needsDialogScrollLock` above - the two halves of this standard are kept
    // identical on purpose.
    <Popover open={open} onOpenChange={(o) => !isDisabled && setOpen(o)}>
      <PopoverTrigger asChild>
        {renderTrigger ? (
          renderTrigger({ selected: chosen, open, disabled: isDisabled })
        ) : (
        <button
          ref={setTriggerRef}
          type="button"
          disabled={isDisabled}
          id={id}
          data-slot="searchable-multi-select-trigger"
          // Radix SelectTrigger exposes role=combobox; keep parity so callers and tests
          // can find the trigger by role, and screen readers announce expanded state.
          role="combobox"
          aria-expanded={open}
          className={cn(
            selectTriggerVariants({ size }),
            // Chips grow the trigger vertically; keep border/focus identical.
            'flex-wrap gap-1',
            triggerClassName,
          )}
        >
          {renderTriggerLabel ? (
            <span className="min-w-0 flex-1 text-left">{renderTriggerLabel(chosen)}</span>
          ) : chosen.length === 0 ? (
            <span className="min-w-0 flex-1 text-left text-muted-foreground break-words">
              {hasLoadError(loadError) ? selectLoadFailurePlaceholder(loadError) : placeholder}
            </span>
          ) : (
            // `min-w-0` is what actually makes the chips wrap inside the
            // trigger: a flex child refuses to shrink below its content without
            // it, so in a narrow control the chips ran past the border - and
            // each chip's remove X went with them, landing over whatever sat
            // beside the field.
            <span className="flex min-w-0 flex-1 flex-wrap gap-1">
              {chosen.map((opt) => (
                <span
                  key={opt.value}
                  className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-xs"
                >
                  {/* No truncation: a chip that reads "CHAOZHOU JINBAICHUAN SANITAR..."
                      does not say which supplier was chosen. It wraps instead. */}
                  <span className="break-words">{opt.label}</span>
                  <span
                    role="button"
                    tabIndex={-1}
                    aria-label={`Remove ${opt.label}`}
                    className="opacity-60 hover:opacity-100"
                    onPointerDown={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      remove(opt.value);
                    }}
                  >
                    <X className="size-3" />
                  </span>
                </span>
              ))}
            </span>
          )}
          {clearable && value.length > 0 && !isDisabled ? (
            <span
              role="button"
              tabIndex={-1}
              aria-label="Clear selection"
              className="mt-0.5 shrink-0 self-start rounded-sm opacity-60 hover:opacity-100"
              onPointerDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onChange([]);
              }}
            >
              <X className="size-4" />
            </span>
          ) : (
            <ChevronDown className="mt-0.5 size-4 shrink-0 self-start opacity-60 -me-0.5" />
          )}
        </button>
        )}
      </PopoverTrigger>
      {/* Portalled so a dialog's overflow can't clip the menu when it flips upward. */}
      <PopoverPortal>
      <PopoverScrollLock open={open} active={needsDialogScrollLock}>
      <PopoverContent className={cn(
            // Cap to the space Radix measured, or a long list makes the menu taller than
            // the viewport and the search box gets pushed off-screen on short windows.
            //
            // Same floor as SearchableSelect: the menu follows its trigger but never goes
            // below 16rem, so a narrow cell cannot squeeze the list to one word per line.
            // The two halves of this standard are kept identical on purpose.
            'max-h-(--radix-popper-available-height) flex flex-col p-0',
            wrapOptions
              ? // Grow to the widest option, never past the viewport, and never
                // narrower than the control it hangs off.
                'w-auto min-w-[max(var(--radix-popper-anchor-width),16rem)] max-w-[min(28rem,calc(100vw-2rem))]'
              : // Follow the trigger's width, capped at the space Radix measured so the
                // 16rem floor can never push the menu off a 375px screen.
                'w-[max(var(--radix-popper-anchor-width),16rem)] max-w-(--radix-popper-available-width)',
            className,
          )} align="start">
        <Command shouldFilter={false} className="max-h-full min-h-0 flex flex-col">
          <CommandInput placeholder="Search..." value={query} onValueChange={handleQueryChange} />
          {!loading && selectable.length > 0 ? (
            <div className="border-b p-1">
              <button
                type="button"
                data-slot="searchable-multi-select-all"
                onClick={toggleAll}
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
              >
                <div className="flex size-4 items-center justify-center rounded-sm border border-input">
                  {allVisibleSelected ? <Check className="size-3" /> : null}
                </div>
                <span className="flex-1 text-left font-medium">{selectAllLabel}</span>
              </button>
            </div>
          ) : null}
          {/* The list is a listbox where more than one row can be chosen at
              once. Saying so is what tells a reader that a tick on one row does
              not cancel the tick on another. */}
          <CommandList aria-multiselectable className="min-h-0 flex-1 overflow-y-auto">
            {/* A real list item, first, so it is the one cmdk highlights and Enter
                in the search box adds the typed word (N-10) - a plain button above
                the list was reachable only by mouse. */}
            {!loading && createLabel ? (
              <CommandGroup className="border-b">
                <CommandItem
                  value={CREATE_ITEM_VALUE}
                  data-slot="searchable-multi-select-create"
                  onSelect={() => createValue?.()}
                  className="flex w-full items-center gap-2 text-start"
                >
                  {createLabel}
                </CommandItem>
              </CommandGroup>
            ) : null}
            {loading ? (
              <div className="flex items-center gap-2 px-3 py-3 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Searching...
              </div>
            ) : failed ? (
              <SelectLoadFailure error={failure} onRetry={retryFailure} />
            ) : visibleOptions.length === 0 && !createLabel ? (
              <CommandEmpty>{emptyMessage}</CommandEmpty>
            ) : null}
            {grouped.map(([groupKey, opts]) => (
              <CommandGroup key={groupKey || '__ungrouped__'} heading={groupKey || undefined}>
                {opts.map((opt) => {
                  const isSelected = selectedSet.has(opt.value);
                  return (
                    <CommandItem
                      key={opt.value}
                      // Filtering is ours now, so this only has to be unique - two options
                      // sharing a label would otherwise collide in cmdk's keyboard nav.
                      value={opt.value}
                      disabled={opt.disabled}
                      // Whether this option is CHOSEN. It has to be an attribute
                      // and not only the tick below, because the tick is a bare
                      // icon with no text: to anything reading the page rather
                      // than looking at it - a screen reader, or a browser agent
                      // reading the accessibility tree - the chosen rows were
                      // indistinguishable from the unchosen ones.
                      //
                      // It cannot be `aria-selected`, which is the ARIA state for
                      // exactly this, because cmdk owns that attribute on its
                      // rows and puts it on the KEYBOARD-HIGHLIGHTED row instead.
                      // The highlight follows the last row you touched, so a
                      // reader saw "selected" jump from option to option on every
                      // click and reported a working multi-select as a single
                      // select. `aria-checked` is a supported state on
                      // `role="option"` and is the one this component controls.
                      aria-checked={isSelected}
                      onSelect={() => toggle(opt.value)}
                      className="flex items-start gap-2"
                    >
                      <div
                        aria-hidden
                        className="mt-0.5 flex size-4 items-center justify-center rounded-sm border border-input"
                      >
                        {isSelected ? <Check className="size-3" /> : null}
                      </div>
                      {renderOption ? (
                        renderOption(opt)
                      ) : (
                      <div className="flex flex-1 flex-col min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="break-words">{opt.label}</span>
                          {opt.badgeText ? (
                            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-900">
                              {opt.badgeText}
                            </span>
                          ) : null}
                        </div>
                        {opt.description ? (
                          <span
                            className="text-xs text-muted-foreground break-words"
                            title={opt.description}
                          >
                            {opt.description}
                          </span>
                        ) : null}
                      </div>
                      )}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
      </PopoverScrollLock>
      </PopoverPortal>
    </Popover>
  );
}

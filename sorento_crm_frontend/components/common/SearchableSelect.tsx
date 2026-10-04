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
import {
  selectTriggerVariants,
  type SelectTriggerSize,
} from '@/components/common/select-trigger-variants';
import { SEARCH_DEBOUNCE_MS } from '@/hooks/useDebouncedSearch';
import { isInsideOpenDialog } from '@/components/common/floatingAncestry';
import { PopoverScrollLock } from '@/components/common/PopoverScrollLock';
import {
  SelectLoadFailure,
  hasLoadError,
  selectLoadFailurePlaceholder,
} from '@/components/common/SelectLoadFailure';

export type SearchableSelectOption = {
  value: string;
  label: string;
  /** Free-text used by the fuzzy filter (static mode); falls back to label + description. */
  searchText?: string;
  /** Optional secondary line under the label (e.g. customer code). */
  description?: string;
  /**
   * The record's own human key, where the label spells out "CODE - Name" and a caller
   * has to fill another cell from the pick. Carried on the option because splitting the
   * label back apart guesses at a separator the option was never promised to use.
   */
  code?: string;
  /** Optional grouping header (renders one CommandGroup per distinct group). */
  group?: string;
  /** Per-option disabled (Radix `SelectItem disabled` parity). */
  disabled?: boolean;
};

export type SearchableSelectProps = {
  value: string;
  onChange: (value: string) => void;
  /**
   * The whole option that was just chosen, alongside `onChange`, and `null` when the selection
   * is cleared.
   *
   * For callers whose next step needs more than the id: an inline line table fills the rest of
   * the row from the picked product, and re-fetching a record it has just been handed would be
   * a round trip for data already on screen.
   */
  onOptionChange?: (option: SearchableSelectOption | null) => void;
  /** Static mode: full option set, filtered client-side. Mutually exclusive with `fetchOptions`. */
  options?: SearchableSelectOption[];
  /**
   * Async mode: server-search. Called with the popover search text (empty on open for the
   * first page), debounced by `SEARCH_DEBOUNCE_MS`. Results replace the list; stale
   * responses are dropped.
   */
  fetchOptions?: (query: string, pageIndex: number) => Promise<SearchableSelectOption[]>;
  /**
   * Async mode: append pages instead of replacing, with a "Load more" row while the last
   * fetch came back full. Without this the list is whatever one page returns, which
   * quietly caps how much of a large catalog is reachable.
   */
  paginated?: boolean;
  /** Async + paginated: page size used to decide whether another page may exist. Default 50. */
  pageSize?: number;
  /**
   * Notified whenever the search text changes, in BOTH modes. Static-mode callers use this to
   * widen a locally-filtered list with a server refetch.
   */
  onSearchChange?: (query: string) => void;
  /**
   * Search text the popover opens with, for callers that already know what is being looked
   * for. It is put IN the search box rather than applied invisibly, so the user can see what
   * narrowed the list and widen it by editing or clearing the box. Restored on every open.
   */
  initialQuery?: string;
  /**
   * Async mode: the currently-selected option, so the trigger label + checkmark survive
   * when `value` isn't in the fetched page. Callers already hold the selected entity.
   */
  selectedOption?: SearchableSelectOption;
  /** When true, show an explicit × to clear to empty. Default false (required fields). */
  clearable?: boolean;
  /**
   * Size the MENU to its widest option instead of to the trigger.
   *
   * Option labels wrap in both modes - nothing in either menu is ever cut with an
   * ellipsis - so this prop is only about width: the default menu is as wide as the
   * trigger (floored at 16rem), and a picker hanging off a narrow cell reads better
   * when the menu is allowed to grow to its content instead of wrapping every row.
   */
  wrapOptions?: boolean;
  /** Trigger size - shared with Radix SelectTrigger. Default `md`. */
  size?: SelectTriggerSize;
  /** Forwarded to the trigger so a <Label htmlFor> can point at it. */
  id?: string;
  /**
   * Forwarded to the trigger's `aria-label`, for a caller whose accessible name isn't the
   * DOM `id` - an `id` is a stable, kebab-case identifier, not necessarily the readable
   * name a label or a test's `getByLabelText` wants.
   */
  'aria-label'?: string;
  placeholder?: string;
  emptyMessage?: string;
  /**
   * The options query's `error` (NEVER-STUCK-UI S3, lever L5). Set, the menu says "no
   * access" or "could not load" with a Retry instead of "No results found.", and an empty
   * trigger says so too. Async mode (`fetchOptions`) catches its own rejections and needs
   * no prop.
   */
  loadError?: unknown;
  /** Refetch the options; shown as Retry next to a non-refusal `loadError`. */
  onRetry?: () => void;
  disabled?: boolean;
  className?: string;
  triggerClassName?: string;
  /**
   * Truncate the trigger label to one line with an ellipsis (full text in `title`).
   * For triggers inside fixed-width table cells, where the default grow-and-wrap
   * behavior cannot work: the cell's nowrap defeats wrapping and the label paints
   * over the clear/chevron icon instead.
   */
  truncateTriggerLabel?: boolean;
  /** Render the trigger label differently from the option label. */
  renderTriggerLabel?: (opt: SearchableSelectOption) => React.ReactNode;
  /** Render a custom option body (status dots, icons). Defaults to label + description. */
  renderOption?: (opt: SearchableSelectOption) => React.ReactNode;
  /**
   * Replace the whole trigger. For pickers that hang off something other than a select box - 
   * an icon button, say - where the default trigger + chevron would be wrong.
   * Receives the resolved selection and open state; must render a single focusable element.
   */
  renderTrigger?: (state: {
    selected: SearchableSelectOption | null;
    open: boolean;
    disabled: boolean;
  }) => React.ReactNode;
  /**
   * A last row offering to create what was typed, for vocabularies a user may extend.
   *
   * Opt-in and absent by default, so every existing caller is untouched: a select over
   * customers or warehouses must never invite someone to invent one. `label` is called
   * with the trimmed search text; returning null suppresses the row for that text,
   * which is how a caller says "that word already exists under another name" without
   * this component needing to know the matching rules.
   */
  createOption?: {
    label: (query: string) => React.ReactNode | null;
    onCreate: (query: string) => void;
  };
};

export function SearchableSelect({
  value,
  onChange,
  onOptionChange,
  options,
  fetchOptions,
  selectedOption,
  clearable = false,
  wrapOptions = false,
  paginated = false,
  pageSize = 50,
  onSearchChange,
  initialQuery = '',
  id,
  'aria-label': ariaLabel,
  size,
  placeholder = 'Select...',
  emptyMessage = 'No results found.',
  loadError,
  onRetry,
  disabled = false,
  className,
  triggerClassName,
  truncateTriggerLabel = false,
  renderTriggerLabel,
  renderOption,
  renderTrigger,
  createOption,
}: SearchableSelectProps) {
  const isAsync = typeof fetchOptions === 'function';
  const [open, setOpen] = React.useState(false);

  /**
   * A popover portalled inside an open Dialog needs to win the Dialog's own
   * `react-remove-scroll` lock, or a wheel over its own list is silently swallowed (the
   * Dialog's lock exempts only its OWN content subtree, and this popover portals to
   * `<body>`, outside it). Radix's `Popover` `modal` prop gets that lock, but bundles in
   * two things nobody asked for: `hideOthers` marks EVERYTHING outside the popover
   * `aria-hidden` while it is open, and it traps focus - both fine for a real modal, wrong
   * here twice over. Nested in an editable table row, that hid the row's OTHER cells the
   * instant one cell's own dropdown opened ("keeps a freshly added row alive when its
   * picker is opened", InlineLineTable.test.tsx). Nested in a real Dialog, it hid the
   * DIALOG's OWN Update/Save button, because that button lives in a SEPARATE portal
   * subtree from the popover's - Radix Dialog is itself portalled, so from `hideOthers`'
   * point of view the dialog's whole content is just another sibling to hide
   * (ContactAccessTypesAdmin.portalForms.test.tsx, a `SearchableMultiSelect` whose list
   * stays open across multiple picks and only then submits).
   *
   * So neither component ever asks Radix `Popover` for `modal`. The ONE thing actually
   * needed - winning the scroll-lock stack - is applied by hand: `RemoveScroll` (the same
   * library Radix's own modal Popover uses internally) wraps `PopoverContent` via `Slot`
   * so it adds no DOM node, scoped to when the trigger is physically inside an open
   * Dialog's content (detected once from the trigger's own DOM position - no call site
   * needs a new prop, out of 278 combined consumers of the two components).
   * `renderTrigger` callers get the wrap unconditionally: composing a ref through an
   * arbitrary custom trigger element has no evidenced Dialog-nested case yet, and the
   * wrap has no aria/focus side effect to weigh against defaulting it on.
   */
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

  // Async state
  const [asyncOptions, setAsyncOptions] = React.useState<SearchableSelectOption[]>([]);
  const [asyncError, setAsyncError] = React.useState<unknown>(null);
  const [loading, setLoading] = React.useState(false);
  const [query, setQuery] = React.useState(initialQuery);
  const lastQueryRef = React.useRef<string>('\u0000'); // sentinel: never equals a real query

  const [page, setPage] = React.useState(0);
  const [hasMore, setHasMore] = React.useState(false);
  const [loadingMore, setLoadingMore] = React.useState(false);

  const runFetch = React.useCallback(
    async (q: string) => {
      if (!fetchOptions) return;
      lastQueryRef.current = q;
      setLoading(true);
      try {
        const items = await fetchOptions(q, 0);
        if (lastQueryRef.current !== q) return; // stale
        setAsyncError(null);
        setAsyncOptions(items);
        setPage(0);
        setHasMore(paginated && items.length >= pageSize);
      } catch (error) {
        if (lastQueryRef.current !== q) return;
        // A failed read is not an empty list (L5): keep the error so the menu says so.
        setAsyncError(error ?? new Error('These options could not be loaded.'));
        setAsyncOptions([]);
        setHasMore(false);
      } finally {
        if (lastQueryRef.current === q) setLoading(false);
      }
    },
    [fetchOptions, paginated, pageSize],
  );

  const loadMore = React.useCallback(async () => {
    if (!fetchOptions || loadingMore) return;
    const next = page + 1;
    setLoadingMore(true);
    try {
      const items = await fetchOptions(query, next);
      // A query change mid-flight invalidates this page.
      if (lastQueryRef.current !== query) return;
      setAsyncOptions((prev) => [...prev, ...items]);
      setPage(next);
      setHasMore(items.length >= pageSize);
    } catch {
      setHasMore(false);
    } finally {
      setLoadingMore(false);
    }
  }, [fetchOptions, loadingMore, page, query, pageSize]);

  // Eager first page on open; debounced fetch on query change (async mode only). A seeded
  // query is not a keystroke, it is what the caller already knew, so it fetches on open at
  // once rather than after the typing debounce.
  React.useEffect(() => {
    if (!isAsync || !open) return;
    const seeded = query === '' || query === initialQuery;
    const t = setTimeout(() => void runFetch(query), seeded ? 0 : SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [isAsync, open, query, initialQuery, runFetch]);

  // Reset transient async state when the popover closes. Back to the seed, not to blank:
  // reopening asks the same question it asked the first time.
  React.useEffect(() => {
    if (open) return;
    setQuery(initialQuery);
    lastQueryRef.current = '\u0000';
  }, [open, initialQuery]);

  const baseOptions = React.useMemo(
    () => (isAsync ? asyncOptions : (options ?? [])),
    [isAsync, asyncOptions, options],
  );

  // Resolve the selected option for the trigger label + checkmark.
  const selected = React.useMemo(() => {
    if (!value) return null;
    const found = baseOptions.find((o) => o.value === value);
    if (found) return found;
    if (isAsync && selectedOption?.value === value) return selectedOption;
    return null;
  }, [baseOptions, value, isAsync, selectedOption]);

  // Static mode filters here rather than delegating to cmdk, so that (a) `onSearchChange`
  // callers see the same text the list is filtered by, and (b) matching is the same
  // all-tokens substring rule SearchableMultiSelect uses. cmdk's fuzzy subsequence scoring
  // surfaced loose matches ("kuala" hitting "North Dakota/Beulah"), which is hard to explain
  // and differed between the two halves of one standard. Async mode is filtered server-side.
  const visibleOptions = React.useMemo(() => {
    if (isAsync) return baseOptions;
    const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return baseOptions;
    return baseOptions.filter((opt) => {
      const haystack = (
        opt.searchText ?? `${opt.label} ${opt.description ?? ''}`
      ).toLowerCase();
      return tokens.every((t) => haystack.includes(t));
    });
  }, [isAsync, baseOptions, query]);

  const grouped = React.useMemo(() => {
    const map = new Map<string, SearchableSelectOption[]>();
    for (const opt of visibleOptions) {
      const key = opt.group ?? '';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(opt);
    }
    return Array.from(map.entries());
  }, [visibleOptions]);

  const failure = hasLoadError(loadError) ? loadError : isAsync ? asyncError : null;
  const failed = hasLoadError(failure);
  const retryFailure = hasLoadError(loadError)
    ? onRetry
    : () => void runFetch(query);

  const misconfigured = !isAsync && options === undefined;
  const isDisabled = disabled || misconfigured;
  const showClear = clearable && !!value && !isDisabled;

  const select = (opt: SearchableSelectOption) => {
    onChange(opt.value);
    onOptionChange?.(opt);
    setOpen(false);
  };

  // Resolved once per render so the empty-state branch and the row itself cannot
  // disagree: without it, a query matching nothing renders "No results found." AND the
  // create row, which reads as the list contradicting itself.
  const createQuery = query.trim();
  const createLabel = createOption && createQuery ? createOption.label(createQuery) : null;

  const handleQueryChange = (q: string) => {
    setQuery(q);
    onSearchChange?.(q);
  };

  return (
    // See `needsDialogScrollLock` above for why `Popover` never gets `modal`.
    <Popover open={open} onOpenChange={(o) => !isDisabled && setOpen(o)}>
      <PopoverTrigger asChild>
        {renderTrigger ? (
          renderTrigger({ selected, open, disabled: isDisabled })
        ) : (
        <button
          ref={setTriggerRef}
          type="button"
          disabled={isDisabled}
          id={id}
          aria-label={ariaLabel}
          data-slot="searchable-select-trigger"
          // Radix SelectTrigger exposes role=combobox; keep parity so callers and tests
          // can find the trigger by role, and screen readers announce expanded state.
          role="combobox"
          aria-expanded={open}
          className={cn(selectTriggerVariants({ size }), triggerClassName)}
        >
          {/* Default: no truncation - the chosen option is the one thing the closed
              control has to say, so it wraps and the trigger grows with it (min-height,
              not height). `truncateTriggerLabel` opts a fixed-width-cell trigger into
              one-line ellipsis instead, with the full text on `title`. */}
          <span
            className={cn(
              'min-w-0 flex-1 text-left',
              truncateTriggerLabel ? 'truncate' : 'break-words',
              !selected && 'text-muted-foreground',
            )}
            title={
              truncateTriggerLabel && selected && typeof selected.label === 'string'
                ? selected.label
                : undefined
            }
          >
            {selected
              ? renderTriggerLabel
                ? renderTriggerLabel(selected)
                : selected.label
              : hasLoadError(loadError)
                ? selectLoadFailurePlaceholder(loadError)
                : placeholder}
          </span>
          {showClear ? (
            <span
              role="button"
              tabIndex={-1}
              aria-label="Clear selection"
              className={cn(
                'shrink-0 rounded-sm opacity-60 hover:opacity-100',
                truncateTriggerLabel ? 'self-center' : 'mt-0.5 self-start',
              )}
              onPointerDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onChange('');
                onOptionChange?.(null);
              }}
            >
              <X className="size-4" />
            </span>
          ) : (
            <ChevronDown
              className={cn(
                'size-4 shrink-0 opacity-60 -me-0.5',
                truncateTriggerLabel ? 'self-center' : 'mt-0.5 self-start',
              )}
            />
          )}
        </button>
        )}
      </PopoverTrigger>
      {/* Portalled so a dialog's overflow can't clip the menu when it flips upward. */}
      <PopoverPortal>
      <PopoverScrollLock open={open} active={needsDialogScrollLock}>
      <PopoverContent
        className={cn(
            // Cap to the space Radix measured, or a long list makes the menu taller than
            // the viewport and the search box gets pushed off-screen on short windows.
            //
            // Either way the menu never goes below 16rem: a narrow cell (a UOM column is
            // 110px) made a legible list unreadable, one squeezed word per line, and the
            // column cannot be widened to suit its dropdown.
            'max-h-(--radix-popper-available-height) flex flex-col p-0',
            wrapOptions
              ? // Grow to the widest option, never past the viewport, and never
                // narrower than the control it hangs off.
                'w-auto min-w-[max(var(--radix-popper-anchor-width),16rem)] max-w-[min(28rem,calc(100vw-2rem))]'
              : // Follow the trigger's width, capped at the space Radix measured so the
                // 16rem floor can never push the menu off a 375px screen.
                'w-[max(var(--radix-popper-anchor-width),16rem)] max-w-(--radix-popper-available-width)',
            className,
          )}
        align="start"
      >
        <Command shouldFilter={false} className="max-h-full min-h-0 flex flex-col">
          <CommandInput placeholder="Search..." value={query} onValueChange={handleQueryChange} />
          <CommandList className="min-h-0 flex-1 overflow-y-auto">
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
                {opts.map((opt) => (
                  <CommandItem
                    key={opt.value}
                    // The item's `value` is cmdk's IDENTITY, not its filter text: two options
                    // sharing a label (21 suppliers named "Testing Company" is a real prod
                    // case) used to collide on `searchText ?? label + description` here, so
                    // cmdk's single-highlight state matched every one of them at once and a
                    // hover/arrow landed on all of them together. The id is unique by
                    // construction; `shouldFilter={false}` above means this value never drives
                    // filtering (that is `visibleOptions`, computed manually), so `keywords`
                    // carries the searchable text instead, for parity with cmdk's contract.
                    value={opt.value}
                    keywords={[opt.label, opt.description ?? '']}
                    disabled={opt.disabled}
                    onSelect={() => select(opt)}
                    className="flex items-start gap-2"
                  >
                    <Check
                      className={cn(
                        'mt-0.5 size-4 shrink-0',
                        opt.value === value ? 'opacity-100' : 'opacity-0',
                      )}
                    />
                    {renderOption ? (
                      renderOption(opt)
                    ) : (
                      <div className="flex flex-1 flex-col min-w-0">
                        <span className="break-words">{opt.label}</span>
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
                ))}
              </CommandGroup>
            ))}
            {createLabel ? (
              <div className="border-t p-1">
                <button
                  type="button"
                  data-slot="searchable-select-create"
                  onClick={() => {
                    createOption!.onCreate(createQuery);
                    setOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-start text-sm hover:bg-accent"
                >
                  {createLabel}
                </button>
              </div>
            ) : null}
            {hasMore ? (
              <div className="border-t p-1">
                <button
                  type="button"
                  data-slot="searchable-select-load-more"
                  onClick={() => void loadMore()}
                  disabled={loadingMore}
                  className="flex w-full items-center justify-center gap-2 rounded-sm px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent disabled:opacity-60"
                >
                  {loadingMore ? (
                    <Loader2 className="size-4 animate-spin" aria-label="Loading" />
                  ) : (
                    'Load more'
                  )}
                </button>
              </div>
            ) : null}
          </CommandList>
        </Command>
      </PopoverContent>
      </PopoverScrollLock>
      </PopoverPortal>
    </Popover>
  );
}

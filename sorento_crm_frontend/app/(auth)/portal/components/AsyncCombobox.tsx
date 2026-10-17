'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { sameDisplayText } from '@/lib/product-display';

export interface AsyncComboboxProps<T> {
  value: string;
  onChange: (value: string, item?: T) => void;
  fetchOptions: (q: string) => Promise<T[]>;
  optionValue: (o: T) => string;
  optionLabel: (o: T) => string;
  optionMeta?: (o: T) => string;
  /**
   * A row that is shown but not choosable - the sales opportunity form's "Seri Indah is
   * another agent's customer" match (S2-15). Rendered muted, not clickable, and never
   * committed by keyboard Enter either.
   */
  optionDisabled?: (o: T) => boolean;
  placeholder?: string;
  disabled?: boolean;
  allowFreeText?: boolean;
  /**
   * Human-readable text for the CURRENT `value` when the two differ - a picker whose
   * `optionValue` is an id (project) rather than the label itself. Without it a reloaded
   * form would show the raw id, which is never what the user picked. Values chosen in
   * this session are remembered from the option, so this only has to cover the reload.
   */
  displayValue?: string;
  id?: string;
  /**
   * Accessible name for a box that has no visible label of its own (a table cell). Giving
   * it also marks the input as a combobox for assistive tech.
   */
  ariaLabel?: string;
  /**
   * When true, renders the input as an auto-growing `<textarea>` so long
   * values wrap onto multiple lines instead of scrolling horizontally. The
   * search dropdown still works; pressing Enter while a result is highlighted
   * commits the selection (no newline inserted).
   */
  multiline?: boolean;
}

/**
 * Searchable single-select dropdown with free-text fallback.
 * - Debounced 300ms search-on-input.
 * - Keyboard: ArrowUp/ArrowDown/Enter/Escape.
 * - When `allowFreeText` (default true), blurring keeps the typed value
 *   as free-text if no option is selected. With it off, typing that matched
 *   nothing is discarded on blur, so the caller only ever gets a real option.
 */
export function AsyncCombobox<T>({
  value,
  onChange,
  fetchOptions,
  optionValue,
  optionLabel,
  optionMeta,
  optionDisabled,
  placeholder,
  disabled,
  allowFreeText = true,
  displayValue,
  id,
  ariaLabel,
  multiline,
}: AsyncComboboxProps<T>) {
  /** Label of the option picked in THIS session, so the sync-from-parent effect
   *  below does not overwrite it with the id that selection just wrote. */
  const pickedRef = useRef<{ value: string; label: string } | null>(null);
  const displayFor = useCallback(
    (v: string) => {
      if (!v) return '';
      if (pickedRef.current?.value === v) return pickedRef.current.label;
      return displayValue || v;
    },
    [displayValue],
  );
  const [text, setText] = useState(() => displayFor(value));
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<T[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const lastQueryRef = useRef<string>('');
  // M6-06: the shared debounce standard, not a hand-rolled 300ms timer. `text`
  // stays the source (it also carries the picked label, the focus-triggered
  // fetch, the external sync), so it is fed IN via `setValue` rather than
  // handed to the hook as an initial value.
  const { setValue: seedDebounce, debouncedValue: debouncedText, isSettling } =
    useDebouncedSearch();
  // Dropdown is portaled to <body> so it escapes ancestor overflow clipping
  // (e.g. the products table's overflow-x-auto). Position tracks the input rect.
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null);

  const updateRect = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setRect({ top: r.bottom, left: r.left, width: r.width });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    updateRect();
    window.addEventListener('scroll', updateRect, true);
    window.addEventListener('resize', updateRect);
    return () => {
      window.removeEventListener('scroll', updateRect, true);
      window.removeEventListener('resize', updateRect);
    };
  }, [open, updateRect]);

  // Keep input in sync if parent changes value externally
  useEffect(() => {
    setText(displayFor(value));
  }, [value, displayFor]);

  const runFetch = useCallback(
    async (q: string) => {
      lastQueryRef.current = q;
      setLoading(true);
      try {
        const items = await fetchOptions(q);
        // Drop stale results
        if (lastQueryRef.current !== q) return;
        setOptions(items);
        setActiveIndex(items.length > 0 ? 0 : -1);
      } catch {
        if (lastQueryRef.current !== q) return;
        setOptions([]);
        setActiveIndex(-1);
      } finally {
        if (lastQueryRef.current === q) setLoading(false);
      }
    },
    [fetchOptions],
  );

  // Keep the debounce hook's own value in step with `text`.
  useEffect(() => {
    seedDebounce(text);
  }, [text, seedDebounce]);

  // Debounced fetch once `text` has settled, while open (M6-06).
  useEffect(() => {
    if (!open) return;
    void runFetch(debouncedText);
  }, [debouncedText, open, runFetch]);

  // Click-outside to close
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (containerRef.current?.contains(target)) return;
      if (dropdownRef.current?.contains(target)) return; // portaled menu
      setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  const commitSelection = (item: T) => {
    const v = optionValue(item);
    const label = optionLabel(item);
    pickedRef.current = { value: v, label };
    setText(label);
    setOpen(false);
    onChange(v, item);
  };

  const handleKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setActiveIndex((idx) => (options.length === 0 ? -1 : (idx + 1) % options.length));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setActiveIndex((idx) =>
        options.length === 0 ? -1 : (idx - 1 + options.length) % options.length,
      );
    } else if (e.key === 'Enter') {
      // In multiline mode allow Enter to insert a newline UNLESS the user is
      // actively picking from the dropdown (activeIndex is highlighted).
      if (
        open &&
        activeIndex >= 0 &&
        activeIndex < options.length &&
        !optionDisabled?.(options[activeIndex])
      ) {
        e.preventDefault();
        commitSelection(options[activeIndex]);
      } else if (!multiline && allowFreeText) {
        e.preventDefault();
        onChange(text);
        setOpen(false);
      }
    } else if (e.key === 'Escape') {
      if (open) {
        e.preventDefault();
        setOpen(false);
      }
    }
  };

  const handleBlur = () => {
    // Defer so option clicks fire first
    setTimeout(() => {
      if (!containerRef.current) return;
      if (containerRef.current.contains(document.activeElement)) return;
      setOpen(false);
      if (allowFreeText) {
        if (text !== value) onChange(text);
        return;
      }
      // Free text is not a value here, so typing that matched no option is thrown
      // away rather than sent on as if it were an id. Emptying the box still clears
      // the selection - that is a deliberate act, not an unmatched search.
      if (!text.trim()) {
        pickedRef.current = null;
        if (value) onChange('');
        return;
      }
      setText(displayFor(value));
    }, 150);
  };

  // Auto-grow the textarea to fit its content so wrapped values are fully
  // visible without scrolling.
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    if (!multiline) return;
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [text, multiline]);

  return (
    <div ref={containerRef} className="relative">
      {multiline ? (
        <Textarea
          ref={textareaRef}
          id={id}
          value={text}
          placeholder={placeholder}
          disabled={disabled}
          rows={2}
          onChange={(e) => {
            setText(e.target.value);
            if (!open) setOpen(true);
          }}
          onFocus={() => {
            if (!open && !disabled) {
              setOpen(true);
              void runFetch(text);
            }
          }}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          autoComplete="off"
          className="resize-none overflow-hidden"
        />
      ) : (
        <Input
          id={id}
          type="text"
          {...(ariaLabel
            ? { role: 'combobox', 'aria-label': ariaLabel, 'aria-expanded': open }
            : {})}
          value={text}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(e) => {
            setText(e.target.value);
            if (!open) setOpen(true);
          }}
          onFocus={() => {
            if (!open && !disabled) {
              setOpen(true);
              void runFetch(text);
            }
          }}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          autoComplete="off"
        />
      )}
      {open && !disabled && rect && typeof document !== 'undefined' && createPortal(
        <div
          ref={dropdownRef}
          style={{
            position: 'fixed',
            top: rect.top + 4,
            left: rect.left,
            width: rect.width,
          }}
          className="z-[100] max-h-[200px] overflow-auto border rounded-md bg-background shadow"
          role="listbox"
        >
          {/* M6-06: `isSettling` covers the debounce window itself, `loading`
              the network request after it - both read as "still searching",
              the way ListSearchInput's spinner does. */}
          {(loading || isSettling) && (
            <div className="px-3 py-2 text-sm text-muted-foreground">Searching...</div>
          )}
          {!loading && !isSettling && options.length === 0 && (
            <div className="px-3 py-2 text-sm text-muted-foreground">No matches</div>
          )}
          {!loading &&
            !isSettling &&
            options.map((opt, i) => {
              const v = optionValue(opt);
              const label = optionLabel(opt);
              const meta = optionMeta?.(opt);
              const isActive = i === activeIndex;
              const isDisabled = !!optionDisabled?.(opt);
              return (
                <button
                  type="button"
                  key={`${v}-${i}`}
                  role="option"
                  aria-selected={isActive}
                  aria-disabled={isDisabled}
                  disabled={isDisabled}
                  className={
                    'block w-full text-left px-3 py-2 text-sm transition-colors ' +
                    (isDisabled
                      ? 'cursor-not-allowed text-muted-foreground opacity-60'
                      : isActive
                        ? 'bg-accent text-accent-foreground'
                        : 'hover:bg-accent/60')
                  }
                  onMouseEnter={() => {
                    if (!isDisabled) setActiveIndex(i);
                  }}
                  onMouseDown={(e) => {
                    // Prevent input blur before click handler runs
                    e.preventDefault();
                  }}
                  onClick={() => {
                    if (!isDisabled) commitSelection(opt);
                  }}
                >
                  <div className="font-medium truncate">{label}</div>
                  {meta && !sameDisplayText(label, meta) && (
                    <div className="text-xs text-muted-foreground truncate">{meta}</div>
                  )}
                </button>
              );
            })}
        </div>,
        document.body,
      )}
    </div>
  );
}

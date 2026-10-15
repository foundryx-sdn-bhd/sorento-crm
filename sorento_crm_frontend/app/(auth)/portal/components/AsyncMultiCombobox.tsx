'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { sameDisplayText } from '@/lib/product-display';

export interface AsyncMultiComboboxProps<T> {
  value: string[];
  onChange: (values: string[]) => void;
  fetchOptions: (q: string) => Promise<T[]>;
  optionValue: (o: T) => string;
  optionLabel: (o: T) => string;
  optionMeta?: (o: T) => string;
  placeholder?: string;
  disabled?: boolean;
  allowFreeText?: boolean;
  id?: string;
}

/**
 * Multi-select searchable combobox with chips.
 * - Selected values render as chips above the input.
 * - Click chip 'x' to remove.
 * - Press Enter on free text to add as a chip (when allowFreeText).
 * - Up/Down arrows + Enter to select an option.
 */
export function AsyncMultiCombobox<T>({
  value,
  onChange,
  fetchOptions,
  optionValue,
  optionLabel,
  optionMeta,
  placeholder,
  disabled,
  allowFreeText = true,
  id,
}: AsyncMultiComboboxProps<T>) {
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<T[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const lastQueryRef = useRef<string>('');
  // M6-06: the shared debounce standard, not a hand-rolled 300ms timer.
  const { setValue: seedDebounce, debouncedValue: debouncedText, isSettling } =
    useDebouncedSearch();

  const runFetch = useCallback(
    async (q: string) => {
      lastQueryRef.current = q;
      setLoading(true);
      try {
        const items = await fetchOptions(q);
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

  // Fetch once `text` has settled, while open (M6-06).
  useEffect(() => {
    if (!open) return;
    void runFetch(debouncedText);
  }, [debouncedText, open, runFetch]);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (!containerRef.current) return;
      if (!containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  const addValue = (v: string) => {
    const trimmed = v.trim();
    if (!trimmed) return;
    if (value.includes(trimmed)) return;
    onChange([...value, trimmed]);
  };

  const removeValue = (v: string) => {
    onChange(value.filter((x) => x !== v));
  };

  const commitSelection = (item: T) => {
    addValue(optionValue(item));
    setText('');
    // Keep popover open to allow further selections.
    void runFetch('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
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
      if (open && activeIndex >= 0 && activeIndex < options.length) {
        e.preventDefault();
        commitSelection(options[activeIndex]);
      } else if (allowFreeText && text.trim()) {
        e.preventDefault();
        addValue(text);
        setText('');
      }
    } else if (e.key === 'Backspace' && text === '' && value.length > 0) {
      e.preventDefault();
      onChange(value.slice(0, -1));
    } else if (e.key === 'Escape') {
      if (open) {
        e.preventDefault();
        setOpen(false);
      }
    }
  };

  return (
    <div ref={containerRef} className="relative">
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-2">
          {value.map((v) => (
            <span
              key={v}
              className="inline-flex items-center gap-1 rounded-md bg-secondary px-2 py-1 text-xs text-secondary-foreground"
            >
              <span className="truncate max-w-[200px]">{v}</span>
              {!disabled && (
                <button
                  type="button"
                  className="opacity-60 hover:opacity-100"
                  onClick={() => removeValue(v)}
                  aria-label={`Remove ${v}`}
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      <Input
        id={id}
        type="text"
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
        onKeyDown={handleKeyDown}
        autoComplete="off"
      />
      {open && !disabled && (
        <div
          className="absolute z-50 mt-1 left-0 right-0 max-h-[200px] overflow-auto border rounded-md bg-background shadow"
          role="listbox"
        >
          {/* M6-06: `isSettling` covers the debounce window, `loading` the
              network request after it - both read as "still searching". */}
          {(loading || isSettling) && (
            <div className="px-3 py-2 text-sm text-muted-foreground">Searching...</div>
          )}
          {!loading && !isSettling && options.length === 0 && (
            <div className="px-3 py-2 text-sm text-muted-foreground">
              {allowFreeText ? 'Press Enter to add free text' : 'No matches'}
            </div>
          )}
          {!loading &&
            !isSettling &&
            options.map((opt, i) => {
              const v = optionValue(opt);
              const label = optionLabel(opt);
              const meta = optionMeta?.(opt);
              const isActive = i === activeIndex;
              const isSelected = value.includes(v);
              return (
                <button
                  type="button"
                  key={`${v}-${i}`}
                  role="option"
                  aria-selected={isActive}
                  disabled={isSelected}
                  className={
                    'block w-full text-left px-3 py-2 text-sm transition-colors ' +
                    (isSelected
                      ? 'opacity-50 cursor-not-allowed'
                      : isActive
                        ? 'bg-accent text-accent-foreground'
                        : 'hover:bg-accent/60')
                  }
                  onMouseEnter={() => !isSelected && setActiveIndex(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                  }}
                  onClick={() => !isSelected && commitSelection(opt)}
                >
                  <div className="font-medium truncate">{label}</div>
                  {meta && !sameDisplayText(label, meta) && (
                    <div className="text-xs text-muted-foreground truncate">{meta}</div>
                  )}
                </button>
              );
            })}
        </div>
      )}
    </div>
  );
}

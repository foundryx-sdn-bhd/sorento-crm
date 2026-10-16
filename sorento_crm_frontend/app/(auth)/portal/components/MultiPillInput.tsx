'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { sameDisplayText } from '@/lib/product-display';

export interface MultiPillOption {
  value: string;
  label?: string;
  meta?: string;
  data?: unknown;
}

export interface MultiPillInputProps {
  id?: string;
  value: string;
  onChange: (csv: string) => void;
  placeholder?: string;
  disabled?: boolean;
  fetchOptions?: (q: string) => Promise<MultiPillOption[]>;
  onItemSelect?: (value: string, opt: MultiPillOption) => void;
}

const SPLIT_RE = /[,\n]/;

function parsePills(csv: string): string[] {
  return (csv || '')
    .split(SPLIT_RE)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function MultiPillInput({
  id,
  value,
  onChange,
  placeholder,
  disabled,
  fetchOptions,
  onItemSelect,
}: MultiPillInputProps) {
  const [pills, setPills] = useState<string[]>(() => parsePills(value));
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<MultiPillOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const lastQueryRef = useRef('');
  // M6-06: the shared debounce standard, not a hand-rolled 250ms timer.
  const { setValue: seedDebounce, debouncedValue: debouncedText, isSettling } =
    useDebouncedSearch();

  useEffect(() => {
    setPills(parsePills(value));
  }, [value]);

  const addPills = useCallback(
    (raw: string, opt?: MultiPillOption) => {
      const incoming = parsePills(raw);
      if (incoming.length === 0) return;
      setPills((prev) => {
        const seen = new Set(prev);
        const next = [...prev];
        for (const v of incoming) {
          if (!seen.has(v)) {
            next.push(v);
            seen.add(v);
          }
        }
        if (next.length !== prev.length) onChange(next.join(', '));
        return next;
      });
      setText('');
      if (opt && onItemSelect) onItemSelect(opt.value, opt);
    },
    [onChange, onItemSelect],
  );

  const removePill = useCallback(
    (v: string) => {
      setPills((prev) => {
        const next = prev.filter((p) => p !== v);
        onChange(next.join(', '));
        return next;
      });
    },
    [onChange],
  );

  // Keep the debounce hook's own value in step with `text`.
  useEffect(() => {
    seedDebounce(text);
  }, [text, seedDebounce]);

  // Fetch once `text` has settled, while open (M6-06).
  useEffect(() => {
    if (!open || !fetchOptions) return;
    const q = debouncedText;
    lastQueryRef.current = q;
    setLoading(true);
    void (async () => {
      try {
        const opts = await fetchOptions(q);
        if (lastQueryRef.current !== q) return;
        setOptions(opts);
        setActiveIndex(opts.length > 0 ? 0 : -1);
      } catch {
        if (lastQueryRef.current !== q) return;
        setOptions([]);
        setActiveIndex(-1);
      } finally {
        if (lastQueryRef.current === q) setLoading(false);
      }
    })();
  }, [debouncedText, open, fetchOptions]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!containerRef.current) return;
      if (!containerRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const handleKeyDown: React.KeyboardEventHandler<HTMLInputElement> = (e) => {
    if (e.key === 'Enter' || e.key === ',') {
      if (open && fetchOptions && activeIndex >= 0 && activeIndex < options.length) {
        e.preventDefault();
        const opt = options[activeIndex];
        addPills(opt.value, opt);
        return;
      }
      if (text.trim()) {
        e.preventDefault();
        addPills(text);
      }
    } else if (e.key === 'Backspace' && !text && pills.length > 0) {
      e.preventDefault();
      removePill(pills[pills.length - 1]);
    } else if (e.key === 'ArrowDown' && fetchOptions) {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setActiveIndex((i) => (options.length === 0 ? -1 : (i + 1) % options.length));
    } else if (e.key === 'ArrowUp' && fetchOptions) {
      e.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      setActiveIndex((i) =>
        options.length === 0 ? -1 : (i - 1 + options.length) % options.length,
      );
    } else if (e.key === 'Escape') {
      if (open) {
        e.preventDefault();
        setOpen(false);
      }
    }
  };

  const handleBlur = () => {
    setTimeout(() => {
      if (!containerRef.current) return;
      if (containerRef.current.contains(document.activeElement)) return;
      setOpen(false);
      if (text.trim()) addPills(text);
    }, 150);
  };

  return (
    <div ref={containerRef} className="relative">
      <div
        className={
          'pill-shell flex flex-wrap items-center gap-1.5 min-h-9 rounded-md border border-input bg-background px-2 py-1.5 text-sm ' +
          (disabled
            ? 'opacity-60 cursor-not-allowed'
            : 'focus-within:ring-2 focus-within:ring-ring')
        }
        onClick={() => inputRef.current?.focus()}
      >
        {pills.map((p) => (
          <span
            key={p}
            className="inline-flex items-center gap-1 rounded-md bg-secondary px-2 py-0.5 text-xs text-secondary-foreground"
          >
            <span className="break-words">{p}</span>
            {!disabled && (
              <button
                type="button"
                aria-label={`Remove ${p}`}
                className="opacity-60 hover:opacity-100"
                onClick={(e) => {
                  e.stopPropagation();
                  removePill(p);
                }}
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </span>
        ))}
        <input
          ref={inputRef}
          id={id}
          type="text"
          className="flex-1 min-w-[80px] bg-transparent outline-none disabled:cursor-not-allowed"
          value={text}
          placeholder={pills.length === 0 ? placeholder : undefined}
          disabled={disabled}
          onChange={(e) => {
            setText(e.target.value);
            if (fetchOptions && !open) setOpen(true);
          }}
          onFocus={() => {
            if (fetchOptions && !disabled) setOpen(true);
          }}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          autoComplete="off"
        />
      </div>
      {fetchOptions && open && !disabled && (
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
            <div className="px-3 py-2 text-sm text-muted-foreground">No matches</div>
          )}
          {!loading &&
            !isSettling &&
            options.map((opt, i) => {
              const isActive = i === activeIndex;
              return (
                <button
                  type="button"
                  key={`${opt.value}-${i}`}
                  role="option"
                  aria-selected={isActive}
                  className={
                    'block w-full text-left px-3 py-2 text-sm transition-colors ' +
                    (isActive
                      ? 'bg-accent text-accent-foreground'
                      : 'hover:bg-accent/60')
                  }
                  onMouseEnter={() => setActiveIndex(i)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => addPills(opt.value, opt)}
                >
                  <div className="font-medium truncate">{opt.label ?? opt.value}</div>
                  {opt.meta && !sameDisplayText(opt.label ?? opt.value, opt.meta) && (
                    <div className="text-xs text-muted-foreground truncate">{opt.meta}</div>
                  )}
                </button>
              );
            })}
        </div>
      )}
    </div>
  );
}

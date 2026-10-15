'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent as ReactClipboardEvent } from 'react';
import { ChevronDown, ChevronUp, Search, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { DOLookupItem, DOProductLine, lookupDeliveryOrders } from '../lib/portal-client';
import {
  PeriodPicker,
  PeriodValue,
  periodToIsoBounds,
} from './PeriodPicker';
import { distinctName } from '@/lib/product-display';

interface Props {
  id?: string;
  value: string[];
  onChange: (next: string[], items?: DOLookupItem[]) => void;
  onProductsConfirmed?: (payload: {
    items: DOLookupItem[];
    productCodes: string[];
  }) => void;
  disabled?: boolean;
}

interface ConfirmProductRow {
  code: string;
  name?: string | null;
  matched: boolean;
}

/**
 * DO multi-select with a filter dialog.
 *
 * Chips render selected DO numbers. The "Search & select" button opens a
 * dialog where the user can filter by date range, product code, and customer
 * (debtor) name to narrow down results, then check rows to select.
 */
export function DOFilterMultiSelect({
  id,
  value,
  onChange,
  onProductsConfirmed,
  disabled,
}: Props) {
  const [open, setOpen] = useState(false);
  const [period, setPeriod] = useState<PeriodValue>({ granularity: 'day' });
  const [productCode, setProductCode] = useState('');
  const [customer, setCustomer] = useState('');
  const [results, setResults] = useState<DOLookupItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftSelected, setDraftSelected] = useState<Set<string>>(new Set());
  const [draftItems, setDraftItems] = useState<DOLookupItem[]>([]);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmRows, setConfirmRows] = useState<ConfirmProductRow[]>([]);
  const [confirmSelected, setConfirmSelected] = useState<Set<string>>(new Set());
  const [pendingItems, setPendingItems] = useState<DOLookupItem[]>([]);
  const [pendingOrderNumbers, setPendingOrderNumbers] = useState<string[]>([]);
  const [pendingFilter, setPendingFilter] = useState<string>('');

  const runSearch = useCallback(async () => {
    const bounds = periodToIsoBounds(period);
    setLoading(true);
    setError(null);
    try {
      const items = await lookupDeliveryOrders('', 50, {
        start_date: bounds.start_date,
        end_date: bounds.end_date,
        product_code: productCode.trim() || undefined,
        debtor_name: customer.trim() || undefined,
      });
      setResults(items);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load delivery orders.');
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, [period, productCode, customer]);

  // Auto-load when the dialog opens with current selection seeded.
  useEffect(() => {
    if (!open) return;
    setDraftSelected(new Set(value));
    void runSearch();
    // Only re-run on dialog open; user re-runs manually via Search button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const toggleRow = (row: DOLookupItem) => {
    setDraftSelected((prev) => {
      const next = new Set(prev);
      if (next.has(row.order_number)) next.delete(row.order_number);
      else next.add(row.order_number);
      return next;
    });
    setDraftItems((prev) => {
      const exists = prev.some((p) => p.order_number === row.order_number);
      if (exists) return prev.filter((p) => p.order_number !== row.order_number);
      return [...prev, row];
    });
  };

  const handleApply = () => {
    const orderNumbers = Array.from(draftSelected);
    const itemMap = new Map<string, DOLookupItem>();
    for (const r of results) itemMap.set(r.order_number, r);
    for (const r of draftItems) if (!itemMap.has(r.order_number)) itemMap.set(r.order_number, r);
    const orderedItems = orderNumbers
      .map((n) => itemMap.get(n))
      .filter((x): x is DOLookupItem => Boolean(x));

    if (!onProductsConfirmed || orderedItems.length === 0) {
      onChange(orderNumbers, orderedItems);
      setOpen(false);
      return;
    }

    const filter = productCode.trim().toLowerCase();
    const seen = new Map<string, ConfirmProductRow>();
    for (const it of orderedItems) {
      const lines = it.product_lines ?? [];
      if (lines.length > 0) {
        for (const pl of lines) {
          if (!pl.product_code) continue;
          if (!seen.has(pl.product_code)) {
            seen.set(pl.product_code, {
              code: pl.product_code,
              name: pl.product_name ?? null,
              matched:
                filter === '' || pl.product_code.toLowerCase().includes(filter),
            });
          }
        }
      } else {
        for (const c of it.products ?? []) {
          if (!c) continue;
          if (!seen.has(c)) {
            seen.set(c, {
              code: c,
              matched: filter === '' || c.toLowerCase().includes(filter),
            });
          }
        }
      }
    }
    const rows = Array.from(seen.values()).sort((a, b) => {
      if (a.matched !== b.matched) return a.matched ? -1 : 1;
      return a.code.localeCompare(b.code);
    });
    if (rows.length === 0) {
      onChange(orderNumbers);
      onProductsConfirmed?.({ items: orderedItems, productCodes: [] });
      setOpen(false);
      return;
    }
    setConfirmRows(rows);
    setConfirmSelected(new Set(rows.filter((r) => r.matched).map((r) => r.code)));
    setPendingItems(orderedItems);
    setPendingOrderNumbers(orderNumbers);
    setPendingFilter(productCode.trim());
    setConfirmOpen(true);
  };

  const toggleConfirmRow = (code: string) => {
    setConfirmSelected((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const handleConfirmCancel = () => {
    setConfirmOpen(false);
  };

  const handleConfirmProceed = () => {
    const codes = confirmRows
      .map((r) => r.code)
      .filter((c) => confirmSelected.has(c));
    onChange(pendingOrderNumbers);
    onProductsConfirmed?.({ items: pendingItems, productCodes: codes });
    setConfirmOpen(false);
    setOpen(false);
  };

  const removeChip = (v: string) => {
    onChange(value.filter((x) => x !== v));
  };

  const selectionCount = useMemo(() => draftSelected.size, [draftSelected]);

  const [freeText, setFreeText] = useState('');
  const commitFreeText = () => {
    const v = freeText.trim();
    if (!v) return;
    if (value.includes(v)) {
      setFreeText('');
      return;
    }
    onChange([...value, v]);
    setFreeText('');
  };
  // Paste support: a pasted blob of DO numbers (comma / newline / tab /
  // semicolon / whitespace separated) is split and each unique value becomes
  // its own pill, deduped against existing values.
  const commitManyFromText = (text: string) => {
    const parts = text
      .split(/[\s,;]+/)
      .map((p) => p.trim())
      .filter(Boolean);
    if (parts.length === 0) return;
    const next = [...value];
    for (const p of parts) {
      if (!next.includes(p)) next.push(p);
    }
    onChange(next);
    setFreeText('');
  };
  const handlePaste = (e: ReactClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData('text');
    if (!text) return;
    // Only intercept multi-value pastes; single token falls through to the
    // normal input so the user can keep editing before committing.
    if (/[\s,;]/.test(text.trim())) {
      e.preventDefault();
      commitManyFromText(text);
    }
  };

  return (
    <div className="space-y-2" id={id}>
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1">
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
                  onClick={() => removeChip(v)}
                  aria-label={`Remove ${v}`}
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      {/* Free-text entry - DOs not in the system can still be filed. Press
          Enter (or blur) to commit each value as a pill. The dialog search
          remains available for known DOs. */}
      <Input
        type="text"
        value={freeText}
        onChange={(e) => setFreeText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            commitFreeText();
          }
        }}
        onPaste={handlePaste}
        onBlur={commitFreeText}
        placeholder="Type or paste DO number(s), press Enter"
        disabled={disabled}
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <Search className="h-4 w-4 mr-2" />
        {value.length > 0 ? 'Edit selection' : 'Search delivery orders'}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Select delivery orders</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="do-period">Period</Label>
              <PeriodPicker value={period} onChange={setPeriod} />
              <p className="text-xs text-muted-foreground">
                Pick a year, month, or exact day range using the calendar
                button.
              </p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="do-product">Product code</Label>
                <Input
                  id="do-product"
                  value={productCode}
                  onChange={(e) => setProductCode(e.target.value)}
                  placeholder="e.g. SRTWC12345"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="do-customer">Customer (debtor)</Label>
                <Input
                  id="do-customer"
                  value={customer}
                  onChange={(e) => setCustomer(e.target.value)}
                  placeholder="Customer or debtor name"
                />
              </div>
            </div>
            <div className="flex justify-end">
              <Button type="button" size="sm" onClick={runSearch} disabled={loading}>
                <Search className="h-4 w-4 mr-2" />
                {loading ? 'Searching…' : 'Search'}
              </Button>
            </div>
            <div className="max-h-[320px] overflow-auto rounded-md border border-border">
              {loading ? (
                <p className="p-4 text-sm text-muted-foreground">Searching…</p>
              ) : error ? (
                <p className="p-4 text-sm text-destructive">{error}</p>
              ) : results.length === 0 ? (
                <p className="p-4 text-sm text-muted-foreground">
                  No delivery orders match these filters.
                </p>
              ) : (
                <ul className="divide-y divide-border">
                  {results.map((row) => {
                    const checked = draftSelected.has(row.order_number);
                    return (
                      <DOResultRow
                        key={row.order_number}
                        row={row}
                        checked={checked}
                        onToggle={() => toggleRow(row)}
                      />
                    );
                  })}
                </ul>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {selectionCount} selected
            </p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="button" onClick={handleApply}>
              Add selected
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Confirm products</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {pendingFilter
                ? `Products from ${pendingItems.length} selected DO${pendingItems.length === 1 ? '' : 's'} matching “${pendingFilter}” are pre-selected. Adjust if needed before proceeding.`
                : `Products from ${pendingItems.length} selected DO${pendingItems.length === 1 ? '' : 's'}. Adjust if needed before proceeding.`}
            </p>
            <div className="max-h-[320px] overflow-auto rounded-md border border-border">
              {confirmRows.length === 0 ? (
                <p className="p-4 text-sm text-muted-foreground">
                  No products found in the selected delivery orders.
                </p>
              ) : (
                <ul className="divide-y divide-border">
                  {confirmRows.map((r) => {
                    const checked = confirmSelected.has(r.code);
                    return (
                      <li
                        key={r.code}
                        className="flex items-start gap-3 px-3 py-2 hover:bg-accent/40 cursor-pointer select-none"
                        onClick={() => toggleConfirmRow(r.code)}
                      >
                        <Checkbox
                          checked={checked}
                          aria-label={`Select ${r.code}`}
                          className="mt-1 pointer-events-none"
                          tabIndex={-1}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium break-words">
                            {r.code}
                            {r.matched && pendingFilter && (
                              <span className="ml-2 text-xs font-normal text-primary">
                                matched
                              </span>
                            )}
                          </p>
                          {distinctName(r.code, r.name) && (
                            <p className="text-xs text-muted-foreground break-words">
                              {r.name}
                            </p>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {confirmSelected.size} of {confirmRows.length} selected
            </p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={handleConfirmCancel}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={handleConfirmProceed}
              disabled={confirmRows.length === 0}
            >
              Proceed
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function DOResultRow({
  row,
  checked,
  onToggle,
}: {
  row: DOLookupItem;
  checked: boolean;
  onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const longPressFired = useRef(false);
  const meta = [
    row.debtor_name,
    row.customer_name,
    row.order_date
      ? new Date(row.order_date).toLocaleDateString(undefined, { dateStyle: 'medium' })
      : null,
  ]
    .filter(Boolean)
    .join(' • ');
  const productList = row.products ?? [];
  const productLines = row.product_lines ?? [];

  const startPress = () => {
    longPressFired.current = false;
    longPressTimer.current = setTimeout(() => {
      longPressFired.current = true;
      setExpanded((v) => !v);
    }, 450);
  };
  const clearPress = () => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  };
  const handleClick = () => {
    if (longPressFired.current) {
      longPressFired.current = false;
      return;
    }
    onToggle();
  };

  return (
    <li
      className="flex items-start gap-3 px-3 py-2 hover:bg-accent/40 cursor-pointer select-none"
      onClick={handleClick}
      onContextMenu={(e) => {
        e.preventDefault();
        setExpanded((v) => !v);
      }}
      onTouchStart={startPress}
      onTouchEnd={clearPress}
      onTouchMove={clearPress}
      onTouchCancel={clearPress}
      onMouseDown={startPress}
      onMouseUp={clearPress}
      onMouseLeave={clearPress}
    >
      {/* pointer-events-none so clicks pass through to <li> - fixes the
          "tickbox doesn't toggle, only the text does" bug. */}
      <Checkbox
        checked={checked}
        aria-label={`Select ${row.order_number}`}
        className="mt-1 pointer-events-none"
        tabIndex={-1}
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium break-words">{row.order_number}</p>
        <p className={`text-xs text-muted-foreground ${expanded ? 'break-words' : 'truncate'}`}>
          {meta}
        </p>
        {!expanded && productList.length > 0 && (
          <p className="text-xs text-muted-foreground truncate">
            {productList.join(', ')}
          </p>
        )}
        {expanded && (productLines.length > 0 || productList.length > 0) && (
          <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
            {(productLines.length > 0
              ? productLines
              : productList.map((c) => ({ product_code: c }) as DOProductLine)
            ).map((line, idx) => (
              <li key={`${line.product_code}-${idx}`} className="break-words">
                <span className="font-medium text-foreground">{line.product_code}</span>
                {distinctName(line.product_code, line.product_name) ? (
                  <span> - {line.product_name}</span>
                ) : null}
                {line.quantity != null ? (
                  <span className="ml-1 tabular-nums">× {line.quantity}</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
      <button
        type="button"
        aria-label={expanded ? 'Collapse details' : 'Expand details'}
        className="shrink-0 p-1 -m-1 rounded hover:bg-accent text-muted-foreground"
        onClick={(e) => {
          e.stopPropagation();
          setExpanded((v) => !v);
        }}
      >
        {expanded ? (
          <ChevronUp className="h-4 w-4" />
        ) : (
          <ChevronDown className="h-4 w-4" />
        )}
      </button>
    </li>
  );
}

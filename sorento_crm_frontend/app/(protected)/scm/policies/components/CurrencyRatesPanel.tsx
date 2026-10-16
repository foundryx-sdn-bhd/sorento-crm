'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { TriangleAlert } from 'lucide-react';
import { numberFieldError } from '@/lib/number-field';
import { fmtDate } from '../../lib/format';
import {
  getCurrencyRates,
  saveCurrencyRate,
} from '../../services/currencyRateService';

/**
 * Exchange rates - the thing that lets the plan compare two supplier prices.
 *
 * The purchase-order book prices in four currencies, and most items with more than one
 * priced supplier have those suppliers in different ones. Without a rate the plan will not
 * rank those suppliers on cost and will not fund them, which is correct and is also a dead
 * end on its own. So this panel leads with the currencies the book actually uses that have
 * no rate: the work is named, not deduced from a row that quietly refuses to fund.
 */

const KEY = ['scm', 'config', 'currency-rates'] as const;

interface Draft {
  currency: string;
  /** Kept as a string so a half-typed "0." does not become 0 mid-keystroke. */
  rate: string;
  as_of: string;
  note: string;
  /** True when the currency already has a row, so the dialog can say which it is. */
  existing: boolean;
  /** Set once Save was pressed with a bad rate: the message then follows the field live. */
  showErrors?: boolean;
}

/** A rate must be a number above zero (a zero rate prices the currency at nothing). */
function rateError(raw: string) {
  return numberFieldError(raw, { min: 0, minExclusive: true });
}

export function CurrencyRatesPanel() {
  const qc = useQueryClient();
  const rates = useQuery({ queryKey: KEY, queryFn: getCurrencyRates });
  const [draft, setDraft] = useState<Draft | null>(null);
  // Delete asks nothing (D7): a toast counts down with Cancel.
  const deletion = useDeferredRowAction({
    actionKey: 'currency_rate.delete',
    entityType: 'currency_rate',
    verb: 'Removing',
    successMessage: 'Rate removed.',
    invalidateKeys: [[...KEY]],
  });

  const save = useMutation({
    mutationFn: (d: Draft) =>
      saveCurrencyRate(d.currency.trim().toUpperCase(), {
        rate_to_base: Number(d.rate),
        as_of: d.as_of || null,
        note: d.note.trim() || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      setDraft(null);
      toast.success('Rate saved.');
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const base = rates.data?.base_currency ?? '';
  const missing = rates.data?.missing ?? [];
  const hasCurrency = !!draft && draft.currency.trim().length > 0;
  const rateMessage = draft ? rateError(draft.rate) : undefined;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">Exchange rates</h3>
          <p className="text-2xs text-muted-foreground">
            {base ? `What one unit is worth in ${base}.` : 'What one unit is worth in the base currency.'}
          </p>
        </div>
        <Button
          size="sm"
          onClick={() =>
            setDraft({ currency: '', rate: '', as_of: '', note: '', existing: false })
          }
        >
          <Plus className="size-4" />
          Add rate
        </Button>
      </div>

      {missing.length ? (
        <Alert variant="warning" appearance="light">
          <AlertIcon>
            <TriangleAlert />
          </AlertIcon>
          <AlertTitle>
            <div className="flex flex-wrap items-center gap-2">
              <span>
                No exchange rate for {missing.join(', ')}. Suppliers priced in{' '}
                {missing.length > 1 ? 'those currencies' : 'that currency'} cannot be
                compared on cost or funded.
              </span>
              {missing.map((code) => (
                <Button
                  key={code}
                  size="sm"
                  variant="outline"
                  aria-label={`Add rate for ${code}`}
                  onClick={() =>
                    setDraft({ currency: code, rate: '', as_of: '', note: '', existing: false })
                  }
                >
                  Add {code}
                </Button>
              ))}
            </div>
          </AlertTitle>
        </Alert>
      ) : null}

      {rates.isLoading ? (
        <Skeleton className="h-32 w-full rounded-xl" />
      ) : !rates.data?.rates.length ? (
        <Card className="p-8 text-center">
          <p className="text-sm font-medium">No exchange rates yet.</p>
          <p className="text-2xs text-muted-foreground">
            A rate lets the plan compare a supplier priced in one currency against a supplier
            priced in another.
          </p>
        </Card>
      ) : (
        <Card className="divide-y divide-border">
          {rates.data.rates.map((r) => (
            <div key={r.currency} className="flex items-center justify-between gap-3 p-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{r.currency}</span>
                  <Badge variant="secondary" appearance="light">
                    1 {r.currency} = {r.rate_to_base} {base}
                  </Badge>
                </div>
                <p className="truncate text-2xs text-muted-foreground">
                  {r.as_of ? `As at ${fmtDate(r.as_of)}` : 'No date recorded'}
                  {r.note ? ` · ${r.note}` : ''}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  variant="ghost"
                  mode="icon"
                  size="sm"
                  aria-label={`Edit ${r.currency}`}
                  onClick={() =>
                    setDraft({
                      currency: r.currency,
                      rate: String(r.rate_to_base),
                      as_of: r.as_of ?? '',
                      note: r.note ?? '',
                      existing: true,
                    })
                  }
                >
                  <Pencil className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  mode="icon"
                  size="sm"
                  aria-label={`Remove ${r.currency}`}
                  onClick={() => deletion.run({ id: r.currency, subject: r.currency })}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ))}
        </Card>
      )}

      <Dialog open={!!draft} onOpenChange={(open) => (open ? null : setDraft(null))}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{draft?.existing ? 'Edit rate' : 'Add rate'}</DialogTitle>
            <DialogDescription>
              What one unit of this currency is worth in {base || 'the base currency'}.
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-3">
            <div>
              <Label htmlFor="fx-currency">Currency</Label>
              <Input
                id="fx-currency"
                value={draft?.currency ?? ''}
                placeholder="USD"
                maxLength={3}
                disabled={draft?.existing}
                onChange={(e) =>
                  setDraft((d) => (d ? { ...d, currency: e.target.value.toUpperCase() } : d))
                }
              />
            </div>
            <div>
              <Label htmlFor="fx-rate">Rate to {base || 'base'}</Label>
              <NumberInput
                id="fx-rate"
                min={0}
                step="0.0001"
                value={draft?.rate ?? ''}
                placeholder="4.4"
                error={draft?.showErrors ? rateMessage : undefined}
                onChange={(e) => setDraft((d) => (d ? { ...d, rate: e.target.value } : d))}
              />
            </div>
            <div>
              <Label htmlFor="fx-asof">As at</Label>
              <Input
                id="fx-asof"
                type="date"
                value={draft?.as_of ?? ''}
                onChange={(e) => setDraft((d) => (d ? { ...d, as_of: e.target.value } : d))}
              />
            </div>
            <div>
              <Label htmlFor="fx-note">Source</Label>
              <Input
                id="fx-note"
                value={draft?.note ?? ''}
                placeholder="Bank rate"
                onChange={(e) => setDraft((d) => (d ? { ...d, note: e.target.value } : d))}
              />
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDraft(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                if (!draft) return;
                if (rateMessage) {
                  setDraft({ ...draft, showErrors: true });
                  return;
                }
                save.mutate(draft);
              }}
              disabled={!hasCurrency || save.isPending}
            >
              Save currency rate
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </div>
  );
}

'use client';

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { AlertTriangle, ArrowLeft, Printer } from 'lucide-react';

import {
  Alert,
  AlertContent,
  AlertDescription,
  AlertIcon,
  AlertTitle,
} from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import { getQuote, getSelection } from '../../../services/selectionService';
import { boxesForSelection } from '@/lib/dealer-kit/roomBoxes';
import { RoomPlan } from '../../components/RoomPlan';
import { distinctName } from '@/lib/product-display';

/**
 * The design as a figure somebody can hand to a customer.
 *
 * Two rules shape this screen.
 *
 * First, **nothing here does arithmetic**. Untick a line and the subtotal is
 * re-asked of the server, because a frontend that adds up prices is a second
 * price list nobody knows they are maintaining - and it is the one the customer
 * sees. The round trip is the point, not an accident.
 *
 * Second, **unticking is not deleting**. The dealer is answering "what do I
 * quote today", not "what did we choose", so an excluded line stays on the page
 * and in the design, greyed rather than gone. A line that cannot be sold at all
 * is excluded for them and says why, because a total that changed for an
 * unexplained reason is worse than a line with a warning on it.
 */

/** Where the designer remembers the current design. */
const LAST_SELECTION_KEY = 'dealer-kit:last-selection';

export function DesignSummary() {
  const searchParams = useSearchParams();
  const fromParam = searchParams.get('selection');
  const [selectionId, setSelectionId] = useState<string | null>(
    fromParam && /^[0-9a-fA-F-]{36}$/.test(fromParam) ? fromParam : null,
  );
  const [excluded, setExcluded] = useState<string[]>([]);

  useEffect(() => {
    if (selectionId) return;
    setSelectionId(window.localStorage.getItem(LAST_SELECTION_KEY));
  }, [selectionId]);

  const {
    data: quote,
    isLoading,
    isError,
    error,
  } = useQuery({
    // Keyed on the exclusions so a tick re-asks the server rather than doing
    // the sum here.
    queryKey: ['dealer-kit', 'quote', selectionId, [...excluded].sort().join(',')],
    queryFn: () => getQuote(selectionId!, excluded),
    enabled: !!selectionId,
    retry: false,
  });

  const { data: selection } = useQuery({
    queryKey: ['dealer-kit', 'selection', selectionId],
    queryFn: () => getSelection(selectionId!),
    enabled: !!selectionId,
    retry: false,
  });

  const boxes = useMemo(
    () => (selection ? boxesForSelection(selection, []) : []),
    [selection],
  );

  if (!selectionId) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <p className="text-sm font-medium text-foreground">No design open</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Open the room designer, put something in a room, and the summary appears here.
          </p>
          <Button className="mt-4" variant="outline" asChild>
            <Link href="/dealer-kit/design">Go to the Room Designer</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (isError || !quote) {
    return (
      <Alert variant="destructive" appearance="light">
        <AlertIcon>
          <AlertTriangle />
        </AlertIcon>
        <AlertContent>
          <AlertTitle>Could not build this quote</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : 'The design could not be read.'}
          </AlertDescription>
        </AlertContent>
      </Alert>
    );
  }

  const includedCount = quote.lines.filter((line) => line.included).length;

  return (
    <div className="flex flex-col gap-4 lg:flex-row" data-dk-summary>
      <div className="min-w-0 flex-1">
        <Card>
          <CardHeader className="flex flex-col gap-3 pb-3 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle className="text-sm">{quote.name ?? 'This design'}</CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="outline" asChild>
                <Link href="/dealer-kit/design">
                  <ArrowLeft className="size-4" />
                  Back to the design
                </Link>
              </Button>
              <Button size="sm" variant="outline" onClick={() => window.print()}>
                <Printer className="size-4" />
                Print
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {quote.lines.length === 0 ? (
              <div className="rounded-md border border-dashed border-border py-10 text-center">
                <p className="text-sm font-medium text-foreground">Nothing chosen yet</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Add products in the designer and they appear here, priced.
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-xs text-muted-foreground">
                      <th className="w-10 py-2 text-start font-normal">On</th>
                      <th className="py-2 text-start font-normal">Product</th>
                      <th className="py-2 text-end font-normal">Qty</th>
                      <th className="py-2 text-end font-normal">Unit</th>
                      <th className="py-2 text-end font-normal">Line</th>
                    </tr>
                  </thead>
                  <tbody>
                    {quote.lines.map((line) => (
                      <tr
                        key={line.lineId}
                        className={`border-b border-border/60 ${
                          line.included ? '' : 'text-muted-foreground'
                        }`}
                        data-dk-quote-line={line.productId}
                      >
                        <td className="py-2">
                          <Checkbox
                            aria-label={`Include ${line.productCode ?? line.productName}`}
                            checked={line.included}
                            // A line that cannot be sold is not the dealer's to
                            // tick back on.
                            disabled={!line.isAvailable}
                            onCheckedChange={(checked) =>
                              setExcluded((current) =>
                                checked
                                  ? current.filter((id) => id !== line.productId)
                                  : [...new Set([...current, line.productId])],
                              )
                            }
                          />
                        </td>
                        <td className="py-2">
                          <span className="block font-mono text-xs">
                            {line.productCode ?? line.productName}
                          </span>
                          {distinctName(line.productCode, line.productName) ? (
                            <span className="block truncate text-xs text-muted-foreground">
                              {line.productName}
                            </span>
                          ) : null}
                          {!line.isAvailable && (
                            <Badge variant="destructive" className="mt-1 text-xs">
                              {line.unavailableReason ?? 'Cannot be ordered'}
                            </Badge>
                          )}
                        </td>
                        <td className="py-2 text-end tabular-nums">{line.quantity}</td>
                        <td className="py-2 text-end tabular-nums">
                          {line.price ? `${quote.currency} ${line.price}` : '-'}
                        </td>
                        <td className="py-2 text-end tabular-nums">
                          {line.lineTotal ? `${quote.currency} ${line.lineTotal}` : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="w-full lg:w-80">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">The room</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* The plan itself, not a description of it: the customer is being
                shown where the things go, and a picture is the whole argument. */}
            {selection?.room?.outline?.length ? (
              <RoomPlan
                outline={selection.room.outline}
                boxes={boxes}
                openings={selection.room.openings ?? []}
                finishes={selection.room.finishes ?? undefined}
                onOutlineChange={() => {}}
              />
            ) : (
              <p className="rounded-md border border-dashed border-border p-4 text-xs text-muted-foreground">
                This design has no room drawn yet.
              </p>
            )}

            <div className="space-y-1 border-t border-border pt-3 text-xs text-muted-foreground">
              <div className="flex justify-between">
                <span>On this quote</span>
                <span>{includedCount}</span>
              </div>
              <div className="flex justify-between">
                <span>Left off</span>
                <span>{quote.excludedCount}</span>
              </div>
              <div className="flex justify-between pt-1 text-sm font-medium text-foreground">
                <span>Subtotal</span>
                <span data-dk-quote-subtotal>
                  {quote.currency} {quote.subtotal}
                </span>
              </div>
            </div>

            {/* Said plainly rather than implied by a missing button: ordering
                from a design is a decision that has not been made yet. */}
            <p className="text-xs text-muted-foreground">
              This is a figure to quote from. Turning it into an order is not wired up yet.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

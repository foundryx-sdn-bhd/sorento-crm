import { formatBillingAmount } from '../lib/format';

/** Blank when not synced, "Paid" (muted) at zero, else the amount. Right aligned by the caller. */
export function OutstandingCell({
  outstanding,
  currencyCode,
}: {
  outstanding: number | null;
  currencyCode: string;
}) {
  if (outstanding === null) return null;
  if (outstanding === 0) return <span className="text-muted-foreground">Paid</span>;
  return <span className="tabular-nums">{formatBillingAmount(outstanding, currencyCode)}</span>;
}

import { formatDate, formatMoney2dp } from '@/lib/helpers';

/** A YYYY-MM-DD day, drawn as dd/MM/yyyy without a timezone shifting it. */
export function formatDay(value: string | null | undefined): string {
  if (!value) return '-';
  return formatDate(`${value.slice(0, 10)}T00:00:00`);
}

/** Money with the currency code in front when it is not MYR. */
export function formatBillingAmount(amount: number | null | undefined, currencyCode = 'MYR'): string {
  const n = formatMoney2dp(amount);
  return currencyCode && currencyCode !== 'MYR' ? `${currencyCode} ${n}` : n;
}

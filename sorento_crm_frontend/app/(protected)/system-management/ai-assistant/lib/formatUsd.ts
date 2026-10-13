const DASH = '—';

/** A call or total cost: 4 decimals with a dollar sign. A missing cost is a dash, never $0. */
export function formatUsd(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return DASH;
  return `$${value.toFixed(4)}`;
}

/** A per-1M-tokens price: up to 6 decimals, trailing zeros trimmed (0.750000 -> 0.75). */
export function formatPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return DASH;
  return String(Number(value.toFixed(6)));
}

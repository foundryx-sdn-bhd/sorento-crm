/**
 * The contacts list's Filters popover, summarised: how many filters are on and one removable chip per
 * filter. Tag (`tag`) and Tag settings (`tag_settings`) are primary toolbar controls, so they are
 * never counted and never a chip. Works on the list's URL filter map (backend param names).
 */

const YES_NO: Record<string, [string, string]> = {
  cost: ['Visible', 'Hidden'],
  ai: ['Allowed', 'Blocked'],
  escalation: ['Allowed', 'Blocked'],
  packing_list: ['Allowed', 'Not allowed'],
  stock: ['Allowed', 'Blocked'],
};
const YES_NO_TITLE: Record<string, string> = {
  cost: 'Cost',
  ai: 'AI replies',
  escalation: 'Escalation',
  packing_list: 'Packing list',
  stock: 'Stock checks',
};
const TIER: Record<string, string> = {
  dealer: 'Dealer',
  office: 'Office',
  end_user: 'End user',
  none: 'No tier',
};
const REGION: Record<string, string> = { west: 'West', east: 'East' };

/** Param -> chip text for its value, in the order the popover shows them. */
const CHIPS: [
  string,
  (value: string, labels: Partial<Record<string, string>>) => string | null,
][] = [
  ['access_type', (v, l) => `Access type: ${l.access_type ?? v}`],
  ['tier', (v) => `Tier: ${TIER[v] ?? v}`],
  ...Object.keys(YES_NO).map((k): [string, (v: string) => string | null] => [
    k,
    (v) => `${YES_NO_TITLE[k]}: ${YES_NO[k][v === 'yes' ? 0 : 1]}`,
  ]),
  ['region', (v) => `Region: ${REGION[v] ?? v}`],
  [
    'customer_id',
    (_v, l) => `Customer: ${l.customer_id ?? 'Selected customer'}`,
  ],
  [
    'access_differs_from',
    (_v, l) =>
      `Access differs from: ${l.access_differs_from ?? 'Selected contact'}`,
  ],
  ['customers', (v) => (v === 'none' ? 'No customers linked' : null)],
  ['tags', (v) => (v === 'none' ? 'No tags' : null)],
];

export const POPOVER_FILTER_PARAMS = CHIPS.map(([param]) => param);

export function popoverFilterCount(filters: Record<string, string>): number {
  return POPOVER_FILTER_PARAMS.filter((p) => !!filters[p]).length;
}

export function popoverFilterChips(
  filters: Record<string, string>,
  labels: Partial<Record<string, string>>,
  onChange: (next: Record<string, string>) => void,
): { label: string; onClear: () => void }[] {
  const chips: { label: string; onClear: () => void }[] = [];
  for (const [param, text] of CHIPS) {
    const value = filters[param];
    if (!value) continue;
    const label = text(value, labels);
    if (!label) continue;
    chips.push({
      label,
      onClear: () => {
        const next = { ...filters };
        delete next[param];
        onChange(next);
      },
    });
  }
  return chips;
}

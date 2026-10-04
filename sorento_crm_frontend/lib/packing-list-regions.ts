/** Packing list regions (REGION-PACKING-LIST): wire codes and the labels people read. */
export type PackingListRegion = 'west' | 'east';

export const PACKING_LIST_REGION_OPTIONS: { value: PackingListRegion; label: string }[] = [
  { value: 'west', label: 'West Malaysia' },
  { value: 'east', label: 'East Malaysia' },
];

export const DEFAULT_PACKING_LIST_REGIONS: PackingListRegion[] = ['west'];

export function regionLabel(code: string): string {
  return PACKING_LIST_REGION_OPTIONS.find((o) => o.value === code)?.label ?? code;
}

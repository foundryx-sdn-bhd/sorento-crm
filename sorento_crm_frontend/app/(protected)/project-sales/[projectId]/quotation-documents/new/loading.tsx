import { SectionSkeleton } from '@/components/common/SectionSkeleton';

/**
 * Held while this segment's chunk arrives (M5-01). `QuotationFormClient` embeds the
 * quotation lines DataGrid in its Lines tab, one part of a multi-tab form, not the
 * whole page - the full `ListPageSkeleton` table shape would misrepresent a form.
 * The form draws its own `PageHeader` directly (the purchase-request form precedent).
 */
export default function Loading() {
  return <SectionSkeleton rows={6} />;
}

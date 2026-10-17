/**
 * Filter / sort field descriptors for the portal landing toolbar (D-L4).
 *
 * One table drives both Filter and Sort so the two controls never drift:
 * every field the current kind's card carries is filterable AND sortable.
 * Everything here runs client-side over the rows already fetched for the
 * kind (max ~30 rows per contact per kind on the prod copy) - a contact
 * over 200 rows in one kind is the named trigger for server-side filtering.
 */
import { complaintStatusLabel } from '@/lib/complaint-status';
import { statusLabel, type PortalLandingKind, type PortalSubmissionSummary } from './portal-client';

export type LandingFieldType = 'status' | 'text' | 'date';

export interface LandingField {
  key: string;
  label: string;
  type: LandingFieldType;
}

// Every kind's card carries these three (D-L4 "all" row).
const COMMON_FIELDS: LandingField[] = [
  { key: 'status', label: 'Status', type: 'status' },
  { key: 'created_at', label: 'Created', type: 'date' },
  { key: 'document_number', label: 'Form Number', type: 'text' },
];

const KIND_FIELDS: Record<PortalLandingKind, LandingField[]> = {
  stock_inquiry: [
    { key: 'product_code', label: 'Product', type: 'text' },
    { key: 'project_name', label: 'Project', type: 'text' },
    { key: 'project_customer', label: 'Customer', type: 'text' },
  ],
  purchase_request: [
    { key: 'project_title', label: 'Project', type: 'text' },
    { key: 'customer_name', label: 'Customer', type: 'text' },
    { key: 'delivery_order_number', label: 'Delivery order', type: 'text' },
    { key: 'item_description', label: 'Item', type: 'text' },
  ],
  sponsorship_form: [
    { key: 'sponsor_subject', label: 'Subject', type: 'text' },
    { key: 'project_title', label: 'Project', type: 'text' },
    { key: 'customer_name', label: 'Customer', type: 'text' },
    { key: 'purpose', label: 'Purpose', type: 'text' },
  ],
  complaint: [
    { key: 'product_code', label: 'Product', type: 'text' },
    { key: 'project_title', label: 'Project', type: 'text' },
    { key: 'customer_name', label: 'Customer', type: 'text' },
  ],
  price_tag_request: [
    { key: 'customer_name', label: 'Customer', type: 'text' },
    { key: 'needed_by_date', label: 'Need by', type: 'date' },
  ],
  sample_request: [
    { key: 'customer_name', label: 'Customer', type: 'text' },
    { key: 'project_title', label: 'Project', type: 'text' },
  ],
  sales_opportunity: [
    { key: 'title', label: 'Title', type: 'text' },
    { key: 'customer_name', label: 'Customer or prospect', type: 'text' },
    { key: 'expected_close_date', label: 'Expected close', type: 'date' },
  ],
  // Never read: Customer asks renders `CustomerAsksList`, which filters server-side.
  customer_asks: [],
  // Never read either: the Conversation kind's `ConversationList` carries its own field table.
  conversation: [],
};

export function landingFieldsFor(kind: PortalLandingKind): LandingField[] {
  return [...COMMON_FIELDS, ...(KIND_FIELDS[kind] ?? [])];
}

/** Draft or the real status label - same rule the card badge already uses. */
export function submissionStatusLabel(row: PortalSubmissionSummary): string {
  if (row.is_draft) return 'Draft';
  if (row.status_label) return row.status_label;
  return row.kind === 'complaint'
    ? complaintStatusLabel(row.status)
    : statusLabel(row.status);
}

export function landingFieldValue(
  row: PortalSubmissionSummary,
  field: LandingField,
): string | null {
  if (field.key === 'status') return submissionStatusLabel(row) || null;
  const raw = (row as unknown as Record<string, unknown>)[field.key];
  if (raw === null || raw === undefined || raw === '') return null;
  return String(raw);
}

/** Distinct non-empty values for a field, present in the loaded rows. */
export function landingFilterOptions(
  items: PortalSubmissionSummary[],
  field: LandingField,
): string[] {
  const set = new Set<string>();
  for (const row of items) {
    const v = landingFieldValue(row, field);
    if (v) set.add(v);
  }
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}

export interface LandingDateRange {
  from?: string;
  to?: string;
}

export type LandingFilterValue = string | LandingDateRange;
export type LandingFilters = Record<string, LandingFilterValue>;

function isDateRange(v: LandingFilterValue): v is LandingDateRange {
  return typeof v === 'object' && v !== null;
}

export function activeLandingFilterCount(filters: LandingFilters): number {
  let n = 0;
  for (const v of Object.values(filters)) {
    if (isDateRange(v)) {
      if (v.from || v.to) n += 1;
    } else if (v) {
      n += 1;
    }
  }
  return n;
}

export function applyLandingFilters(
  items: PortalSubmissionSummary[],
  fields: LandingField[],
  filters: LandingFilters,
): PortalSubmissionSummary[] {
  return items.filter((row) =>
    fields.every((field) => {
      const value = filters[field.key];
      if (value === undefined) return true;
      if (isDateRange(value)) {
        if (!value.from && !value.to) return true;
        const raw = landingFieldValue(row, field);
        if (!raw) return false;
        // Review round 2: compare the date PART as a string, never
        // `new Date(...)`. A row timestamp like `created_at` is naive-local
        // (no timezone designator) while a bare `YYYY-MM-DD` bound parses as
        // UTC per the ECMA-262 spec - the two `new Date` calls disagreed on
        // what "midnight" meant, so a row just after local midnight on the
        // `from` day compared against an hours-ahead UTC boundary and was
        // wrongly excluded. `YYYY-MM-DD` sorts identically as a string.
        const rowDate = String(raw).slice(0, 10);
        if (value.from && rowDate < value.from) return false;
        if (value.to && rowDate > value.to) return false;
        return true;
      }
      if (!value) return true;
      return landingFieldValue(row, field) === value;
    }),
  );
}

export interface LandingSort {
  key: string;
  dir: 'asc' | 'desc';
}

export const DEFAULT_LANDING_SORT: LandingSort = {
  key: 'created_at',
  dir: 'desc',
};

export function sortLandingItems(
  items: PortalSubmissionSummary[],
  fields: LandingField[],
  sort: LandingSort,
): PortalSubmissionSummary[] {
  const field = fields.find((f) => f.key === sort.key) ?? fields[0];
  if (!field) return items;
  const sorted = [...items].sort((a, b) => {
    const av = landingFieldValue(a, field);
    const bv = landingFieldValue(b, field);
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    const cmp =
      field.type === 'date'
        ? new Date(av).getTime() - new Date(bv).getTime()
        : av.localeCompare(bv, undefined, { sensitivity: 'base' });
    return sort.dir === 'asc' ? cmp : -cmp;
  });
  return sorted;
}

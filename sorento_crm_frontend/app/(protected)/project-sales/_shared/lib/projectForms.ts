import { complaintStatusLabel } from '@/lib/complaint-status';
import { priceTagStatusLabel } from '@/lib/price-tag-status';
import { STOCK_INQUIRY_STATUS_LABELS } from '@/app/(protected)/procurement-management/stock-inquiries/types/stockInquiry.types';
import { getComplaints } from '@/app/(protected)/complaint-management/complaints/services/complaintService';
import { getPurchaseRequests } from '@/app/(protected)/procurement-management/purchase-requests/services/purchaseRequestService';
import { getStockInquiries } from '@/app/(protected)/procurement-management/stock-inquiries/services/stockInquiryService';
import { listPriceTagRequests } from '@/app/(protected)/dealer-kit/services/priceTagRequestService';
import { getSalesOpportunities } from '@/app/(protected)/sales/opportunities/services/salesOpportunityService';

/** One line in a project's Forms tab, whatever form it came from. */
export interface ProjectFormRow {
  id: string;
  href: string;
  number: string;
  date: string | null;
  /** The raw status, for the badge's colour; `statusLabel` is what it reads. */
  status: string;
  statusLabel: string;
  summary: string;
}

export interface ProjectFormList {
  rows: ProjectFormRow[];
  total: number;
}

export type ProjectFormKind =
  | 'complaints'
  | 'purchase-requests'
  | 'sponsorship-forms'
  | 'stock-inquiries'
  | 'price-tag-requests'
  | 'sales-opportunities';

export interface ProjectFormSectionDef {
  kind: ProjectFormKind;
  title: string;
  /** The form type's own view permission: a section renders only for a holder. */
  permission: string;
  emptyTitle: string;
  emptyHint: string;
}

export const PROJECT_FORM_SECTIONS: ProjectFormSectionDef[] = [
  {
    kind: 'complaints',
    title: 'Complaints',
    permission: 'complaint_management.complaints.view',
    emptyTitle: 'No complaints on this project',
    emptyHint: 'Complaints linked to this project appear here.',
  },
  {
    kind: 'purchase-requests',
    title: 'Purchase requests',
    permission: 'procurement.purchase_requests.view',
    emptyTitle: 'No purchase requests on this project',
    emptyHint: 'Purchase requests linked to this project appear here.',
  },
  {
    kind: 'sponsorship-forms',
    title: 'Sponsorship forms',
    permission: 'procurement.sponsorship_forms.view',
    emptyTitle: 'No sponsorship forms on this project',
    emptyHint: 'Sponsorship forms linked to this project appear here.',
  },
  {
    kind: 'stock-inquiries',
    title: 'Stock inquiries',
    permission: 'procurement.stock_inquiries.view',
    emptyTitle: 'No stock inquiries on this project',
    emptyHint: 'Stock inquiries linked to this project appear here.',
  },
  {
    kind: 'price-tag-requests',
    title: 'Price tag requests',
    permission: 'dealer_kit.price_tag_requests.view',
    emptyTitle: 'No price tag requests on this project',
    emptyHint: 'Price tag requests linked to this project appear here.',
  },
  {
    kind: 'sales-opportunities',
    title: 'Sales opportunities',
    permission: 'sales.opportunities.view',
    emptyTitle: 'No sales opportunities on this project',
    emptyHint: 'Sales opportunities linked to this project appear here.',
  },
];

/** The sections a user may see, in the tab's fixed order. */
export function visibleProjectFormSections(
  has: (permission: string) => boolean,
): ProjectFormSectionDef[] {
  return PROJECT_FORM_SECTIONS.filter((s) => has(s.permission));
}

/** Rows fetched per section; the grid pages them from there. */
const PAGE_SIZE = 200;

function titleCase(status: string | null | undefined): string {
  return (status ?? '')
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function join(parts: Array<string | null | undefined>): string {
  return parts.filter((p) => p && p.trim()).join(' - ');
}

const BASE = { pageIndex: 0, pageSize: PAGE_SIZE, sorting: [], searchQuery: '' };

export async function fetchProjectForms(
  kind: ProjectFormKind,
  projectId: string,
): Promise<ProjectFormList> {
  switch (kind) {
    case 'complaints': {
      const res = await getComplaints({ ...BASE, project_id: projectId });
      return {
        total: res.pagination?.total ?? res.data.length,
        rows: res.data.map((c) => ({
          id: c.id,
          href: `/complaint-management/complaints/${c.id}`,
          number: c.complaint_number ?? '-',
          date: c.complaint_date ? String(c.complaint_date) : null,
          status: c.status ?? '',
          statusLabel: complaintStatusLabel(c.status),
          summary: join([c.customer_name, c.project_title]),
        })),
      };
    }
    case 'purchase-requests':
    case 'sponsorship-forms': {
      const isSf = kind === 'sponsorship-forms';
      const res = await getPurchaseRequests({
        ...BASE,
        requestType: isSf ? 'sponsorship_form' : 'purchase_request',
        projectId,
      });
      const base = isSf
        ? '/procurement-management/sponsorship-forms'
        : '/procurement-management/purchase-requests';
      return {
        total: res.pagination?.total ?? res.data.length,
        rows: res.data.map((r) => ({
          id: r.id,
          href: `${base}/${r.id}`,
          number: r.request_number ?? '-',
          date: r.request_date ?? null,
          status: r.status ?? '',
          statusLabel: titleCase(r.status),
          summary: join([r.customer_name, r.project_title]),
        })),
      };
    }
    case 'stock-inquiries': {
      const res = await getStockInquiries({ ...BASE, project_id: projectId });
      return {
        total: res.pagination?.total ?? res.data.length,
        rows: res.data.map((i) => ({
          id: i.id,
          href: `/procurement-management/stock-inquiries/${i.id}`,
          number: i.inquiry_number ?? '-',
          date: i.created_at ? String(i.created_at) : null,
          status: i.status ?? '',
          statusLabel:
            STOCK_INQUIRY_STATUS_LABELS[i.status ?? ''] ?? titleCase(i.status),
          summary: join([i.product_code, i.project_customer]),
        })),
      };
    }
    case 'price-tag-requests': {
      const res = await listPriceTagRequests({
        page: 1,
        limit: PAGE_SIZE,
        project_id: projectId,
      });
      return {
        total: res.pagination.total,
        rows: res.data.map((r) => ({
          id: r.id,
          href: `/dealer-kit/price-tag-requests/${r.id}`,
          number: r.doc_number,
          date: r.created_at ?? null,
          status: r.status,
          statusLabel: priceTagStatusLabel(r.status),
          summary: join([r.debtor_name, r.contact_name]),
        })),
      };
    }
    case 'sales-opportunities': {
      const res = await getSalesOpportunities({ ...BASE, projectId });
      return {
        total: res.pagination?.total ?? res.data.length,
        rows: res.data.map((o) => ({
          id: o.id,
          href: `/sales/opportunities/${o.id}`,
          number: o.opportunity_no,
          date: o.created_at ?? null,
          status: o.stage_key,
          statusLabel: o.stage_label,
          summary: join([o.title, o.customer_name ?? o.prospect_name]),
        })),
      };
    }
  }
}

import { beforeEach, describe, expect, it, vi } from 'vitest';

const getComplaints = vi.hoisted(() => vi.fn());
const getPurchaseRequests = vi.hoisted(() => vi.fn());
const getStockInquiries = vi.hoisted(() => vi.fn());
const listPriceTagRequests = vi.hoisted(() => vi.fn());
const getSalesOpportunities = vi.hoisted(() => vi.fn());
vi.mock('@/app/(protected)/complaint-management/complaints/services/complaintService', () => ({ getComplaints }));
vi.mock('@/app/(protected)/procurement-management/purchase-requests/services/purchaseRequestService', () => ({ getPurchaseRequests }));
vi.mock('@/app/(protected)/procurement-management/stock-inquiries/services/stockInquiryService', () => ({ getStockInquiries }));
vi.mock('@/app/(protected)/dealer-kit/services/priceTagRequestService', () => ({ listPriceTagRequests }));
vi.mock('@/app/(protected)/sales/opportunities/services/salesOpportunityService', () => ({ getSalesOpportunities }));

import {
  fetchProjectForms,
  visibleProjectFormSections,
  PROJECT_FORM_SECTIONS,
} from './projectForms';

const page = (data: unknown[]) => ({ data, empty: !data.length, pagination: { total: data.length, page: 1, limit: 50 } });

beforeEach(() => vi.clearAllMocks());

describe('visibleProjectFormSections (AC-FP-32)', () => {
  it('shows only the sections whose view permission the user holds', () => {
    const held = new Set(['procurement.stock_inquiries.view', 'sales.opportunities.view']);
    const titles = visibleProjectFormSections((p) => held.has(p)).map((s) => s.title);
    expect(titles).toEqual(['Stock inquiries', 'Sales opportunities']);
  });
  it('shows nothing without permissions and all six with them', () => {
    expect(visibleProjectFormSections(() => false)).toEqual([]);
    expect(visibleProjectFormSections(() => true)).toHaveLength(6);
  });
  it('uses each form type\'s own view permission', () => {
    expect(PROJECT_FORM_SECTIONS.map((s) => s.permission)).toEqual([
      'complaint_management.complaints.view',
      'procurement.purchase_requests.view',
      'procurement.sponsorship_forms.view',
      'procurement.stock_inquiries.view',
      'dealer_kit.price_tag_requests.view',
      'sales.opportunities.view',
    ]);
  });
});

describe('fetchProjectForms sends project_id to each list service (AC-FP-31)', () => {
  it('complaints', async () => {
    getComplaints.mockResolvedValue(page([{ id: 'c1', complaint_number: 'C-1', status: 'new', customer_name: 'A' }]));
    const out = await fetchProjectForms('complaints', 'p-1');
    expect(getComplaints).toHaveBeenCalledWith(expect.objectContaining({ project_id: 'p-1' }));
    expect(out.rows[0].href).toBe('/complaint-management/complaints/c1');
  });
  it('purchase requests and sponsorship forms differ by request_type and route', async () => {
    getPurchaseRequests.mockResolvedValue(page([{ id: 'r1', request_number: 'PR-1', status: 'draft' }]));
    const pr = await fetchProjectForms('purchase-requests', 'p-1');
    expect(getPurchaseRequests).toHaveBeenLastCalledWith(
      expect.objectContaining({ projectId: 'p-1', requestType: 'purchase_request' }),
    );
    expect(pr.rows[0].href).toBe('/procurement-management/purchase-requests/r1');
    const sf = await fetchProjectForms('sponsorship-forms', 'p-1');
    expect(getPurchaseRequests).toHaveBeenLastCalledWith(
      expect.objectContaining({ projectId: 'p-1', requestType: 'sponsorship_form' }),
    );
    expect(sf.rows[0].href).toBe('/procurement-management/sponsorship-forms/r1');
  });
  it('stock inquiries', async () => {
    getStockInquiries.mockResolvedValue(page([{ id: 's1', inquiry_number: 'SI-1', status: 'new' }]));
    await fetchProjectForms('stock-inquiries', 'p-1');
    expect(getStockInquiries).toHaveBeenCalledWith(expect.objectContaining({ project_id: 'p-1' }));
  });
  it('price tag requests', async () => {
    listPriceTagRequests.mockResolvedValue({
      data: [{ id: 't1', doc_number: 'PT-1', status: 'new', created_at: '2026-10-01T00:00:00' }],
      pagination: { total: 1, page: 1, limit: 50 },
    });
    const out = await fetchProjectForms('price-tag-requests', 'p-1');
    expect(listPriceTagRequests).toHaveBeenCalledWith(expect.objectContaining({ project_id: 'p-1' }));
    expect(out.rows[0].href).toBe('/dealer-kit/price-tag-requests/t1');
  });
  it('sales opportunities', async () => {
    getSalesOpportunities.mockResolvedValue(
      page([{ id: 'o1', opportunity_no: 'OP-1', title: 'T', stage_key: 'new', stage_label: 'New' }]),
    );
    await fetchProjectForms('sales-opportunities', 'p-1');
    expect(getSalesOpportunities).toHaveBeenCalledWith(expect.objectContaining({ projectId: 'p-1' }));
  });
  it('lets a failing list reject so the section can show its error', async () => {
    getComplaints.mockRejectedValue(new Error('Failed to fetch complaints'));
    await expect(fetchProjectForms('complaints', 'p-1')).rejects.toThrow('Failed to fetch complaints');
  });
});

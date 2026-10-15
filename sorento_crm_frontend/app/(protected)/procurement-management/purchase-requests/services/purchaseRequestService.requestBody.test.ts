/**
 * RED tests - the internal PR / SF request body (bug found in the browser pass, 14 Oct).
 *
 * `toRequestBody` is a whitelist; it dropped customer_id (FORM-ROUTING-CONDITIONS PC-6) and
 * sales_type / pic / sponsor_subject_other, so a picked customer and the Sales
 * type it filled were never saved. The body must carry each with the value given, null when
 * given null, and null (not absent) when missing, so an edit can unlink.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    apiFetch: vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ id: 'pr-1' }) }) as unknown as Response),
  };
});

import { apiFetch } from '@/lib/api';
import {
  createPurchaseRequest,
  updatePurchaseRequest,
  updatePurchaseRequestAndReply,
} from './purchaseRequestService';

const asMock = apiFetch as unknown as ReturnType<typeof vi.fn>;
const sentBody = () => JSON.parse(asMock.mock.calls[asMock.mock.calls.length - 1][1].body as string);

const GIVEN = {
  request_type: 'purchase_request' as const,
  customer_name: 'CUSTOMER A SDN BHD',
  customer_id: 'cust-302',
  sales_type: 'cash_sales',
  pic: 'CS PIC X 0123456789',
  project_id: '11111111-1111-1111-1111-111111111111',
  sponsor_subject_other: 'Annual dinner',
  products: [],
};
const KEYS = ['customer_id', 'sales_type', 'pic', 'sponsor_subject_other'] as const;

beforeEach(() => asMock.mockClear());

describe.each([
  ['createPurchaseRequest', (d: object) => createPurchaseRequest(d as never)],
  ['updatePurchaseRequest', (d: object) => updatePurchaseRequest('pr-1', d as never)],
  [
    'updatePurchaseRequestAndReply',
    (d: object) => updatePurchaseRequestAndReply('pr-1', { formData: d, reply_message: 'ok' } as never),
  ],
])('%s body', (_name, send) => {
  it.each(KEYS)('carries %s with the value given', async (key) => {
    await send(GIVEN);
    expect(sentBody()[key]).toBe(GIVEN[key]);
  });

  it.each(KEYS)('keeps %s as null when given null', async (key) => {
    await send({ ...GIVEN, [key]: null });
    expect(sentBody()).toHaveProperty(key, null);
  });

  it.each(KEYS)('sends %s as null, not absent, when missing', async (key) => {
    const { [key]: _omit, ...rest } = GIVEN;
    void _omit;
    await send(rest);
    expect(sentBody()).toHaveProperty(key, null);
  });
});

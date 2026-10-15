import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { ParkedEmailPreview } from './orderInquiryReserveService';

const BASE = '/api/v1/project-sales/order-inquiries/handover-resend';

/**
 * Send to purchasing (OI-RESEND-PURCHASING): the order inquiry's open rows, rebuilt into the
 * handover email and sent again, single or bulk, after one preview. For ONE order inquiry the
 * caller may pick which open lines go (`rowIds`); omitted means every open line.
 */

export interface HandoverLastSent {
  at: string;
  kind: 'automatic' | 'resent';
  by: string | null;
}

export interface HandoverResendLine {
  row_id: string;
  item_code: string | null;
  qty: string;
  location: string | null;
  delivery_date: string | null;
  selected: boolean;
  /** The newest earlier handover email of this OI that names the line's item code. */
  sent_at: string | null;
}

export interface HandoverResendEmail extends ParkedEmailPreview {
  inquiry_id: string;
  inquiry_no: string | null;
  last_sent: HandoverLastSent | null;
  /** The OI Excel the send attaches (names only). */
  attachments: string[];
  /** Single-OI preview only: every open line, for the per-line ticks. */
  lines: HandoverResendLine[];
}

export type HandoverResendSkipReason = 'nothing_open' | 'no_recipient' | 'failed';

export interface HandoverResendSkip {
  inquiry_id: string;
  inquiry_no: string | null;
  reason: HandoverResendSkipReason;
}

export interface HandoverResendPreview {
  emails: HandoverResendEmail[];
  inquiry_total: number;
  unresolved: { inquiry_count: number; line_count: number }[];
  skipped: HandoverResendSkip[];
  automation_enabled: boolean;
}

export interface HandoverResendResult {
  emails_sent: number;
  items_sent: number;
  skipped: HandoverResendSkip[];
}

async function post<T>(
  path: string,
  inquiryIds: string[],
  rowIds: string[] | undefined,
  fallback: string,
): Promise<T> {
  const response = await apiFetch(`${BASE}/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(
      rowIds ? { inquiry_ids: inquiryIds, row_ids: rowIds } : { inquiry_ids: inquiryIds },
    ),
  });
  if (!response.ok) throw new Error(await extractApiError(response, fallback));
  return response.json();
}

export function previewHandoverResend(
  inquiryIds: string[],
  rowIds?: string[],
): Promise<HandoverResendPreview> {
  return post('preview', inquiryIds, rowIds, 'Failed to load the email to purchasing');
}

export function sendHandoverResend(
  inquiryIds: string[],
  rowIds?: string[],
): Promise<HandoverResendResult> {
  return post('send', inquiryIds, rowIds, 'Failed to send to purchasing');
}

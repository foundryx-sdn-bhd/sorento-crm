import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { ParkedEmailPreview } from './orderInquiryReserveService';

const BASE = '/api/v1/project-sales/order-inquiries/handover-resend';

/**
 * Send to purchasing (OI-RESEND-PURCHASING): the order inquiry's open rows, rebuilt into the
 * handover email and sent again, single or bulk, after one preview.
 */

export interface HandoverLastSent {
  at: string;
  kind: 'automatic' | 'resent';
  by: string | null;
}

export interface HandoverResendEmail extends ParkedEmailPreview {
  inquiry_id: string;
  inquiry_no: string | null;
  last_sent: HandoverLastSent | null;
  /** The OI Excel the send attaches (names only). */
  attachments: string[];
}

export type HandoverResendSkipReason =
  | 'nothing_open'
  | 'no_recipient'
  | 'failed';

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
  fallback: string,
): Promise<T> {
  const response = await apiFetch(`${BASE}/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ inquiry_ids: inquiryIds }),
  });
  if (!response.ok) throw new Error(await extractApiError(response, fallback));
  return response.json();
}

export function previewHandoverResend(
  inquiryIds: string[],
): Promise<HandoverResendPreview> {
  return post('preview', inquiryIds, 'Failed to load the email to purchasing');
}

export function sendHandoverResend(
  inquiryIds: string[],
): Promise<HandoverResendResult> {
  return post('send', inquiryIds, 'Failed to send to purchasing');
}

/* -------------------------------------------------------------------------------------
 * topicAuditService - chat analytics CA-2 topic audit.
 *
 *   GET   /api/v1/system/chatbot/topic-audit?version=&show=to_review|reviewed|all&subject=
 *   POST  /api/v1/system/chatbot/topic-audit/sample   body { version }  -> { drawn }  (409 when drawn)
 *   PATCH /api/v1/system/chatbot/topic-audit/{id}     body { verdict, corrected_topic?, products_verdict? }
 *
 * Both routes need `system.chat_history.view` AND `chatbot.analytics.view`. The message on
 * a row is read from the turn envelope at request time; the backend never stores it.
 * ----------------------------------------------------------------------------------- */

import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

export type AuditShow = 'to_review' | 'reviewed' | 'all';
export type AuditVerdict = 'right' | 'wrong_topic' | 'wrong_subject';
export type AuditProductsVerdict = 'right' | 'wrong';

export interface TopicAuditRow {
  id: string;
  asked_at: string;
  message: string;
  subject: string;
  subject_label: string;
  topic: string;
  topic_label: string;
  confidence: number | null;
  products: string[];
  verdict: AuditVerdict | null;
  corrected_topic: string | null;
  corrected_topic_label: string | null;
  products_verdict: AuditProductsVerdict | null;
  reviewed_by_name: string | null;
}

export interface TopicAuditTopic {
  code: string;
  label: string;
  parent_code: string | null;
}

export interface TopicAuditRates {
  subject: number | null;
  topic: number | null;
  product: number | null;
}

export interface TopicAudit {
  version: number;
  versions: number[];
  sample_size: number;
  reviewed: number;
  rates: TopicAuditRates;
  targets: { subject: number; topic: number; product: number };
  /** Active taxonomy rows; a leaf has a parent. Absent from an older payload. */
  topics?: TopicAuditTopic[];
  rows: TopicAuditRow[];
}

export interface TopicAuditQuery {
  version?: number;
  show?: AuditShow;
  subject?: string;
}

export interface TopicAuditVerdictBody {
  verdict?: AuditVerdict;
  corrected_topic?: string;
  products_verdict?: AuditProductsVerdict;
}

const BASE = '/api/v1/system/chatbot/topic-audit';

const jsonInit = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export async function getTopicAudit(
  query: TopicAuditQuery = {},
): Promise<TopicAudit> {
  const params = new URLSearchParams();
  if (query.version !== undefined) params.set('version', String(query.version));
  if (query.show) params.set('show', query.show);
  if (query.subject) params.set('subject', query.subject);
  const qs = params.toString();
  const response = await apiFetch(qs ? `${BASE}?${qs}` : BASE);
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to load the topic audit'),
    );
  return response.json();
}

export async function drawTopicAuditSample(
  version: number,
): Promise<{ drawn: number }> {
  const response = await apiFetch(
    `${BASE}/sample`,
    jsonInit('POST', { version }),
  );
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to draw the sample'),
    );
  return response.json();
}

export async function saveTopicAuditVerdict(
  id: string,
  body: TopicAuditVerdictBody,
): Promise<TopicAuditRow> {
  const response = await apiFetch(
    `${BASE}/${encodeURIComponent(id)}`,
    jsonInit('PATCH', body),
  );
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to save the verdict'),
    );
  return response.json();
}

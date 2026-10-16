import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { QuotationLineBulkItem } from '../types/project.types';
import type { StatusGraph } from '@/app/(protected)/system-management/status-graphs/types/statusGraph.types';

/**
 * The quotation DOCUMENT: one letterhead carrying several priced scopes.
 *
 * Contract, matching `app/api/v1/projects/quotation_documents.py` exactly:
 *
 *   GET    /projects/{projectId}/quotation-documents                       -> ListEnvelope<QuotationDocument>
 *   POST   /projects/{projectId}/quotation-documents                       -> QuotationDocument (201)
 *   GET    /projects/{projectId}/quotation-documents/{id}                  -> QuotationDocument
 *   PATCH  /projects/{projectId}/quotation-documents/{id}                  -> QuotationDocument
 *   DELETE /projects/{projectId}/quotation-documents/{id}                  -> 204, 422 once issued
 *   POST   /projects/{projectId}/quotation-documents/{id}/scopes           -> QuotationScope (201)
 *   PATCH  /projects/{projectId}/quotation-documents/{id}/scopes/{scopeId} -> QuotationScope
 *   POST   /projects/{projectId}/quotation-documents/{id}/issue            -> QuotationIssue (201)
 *   GET    /projects/{projectId}/quotation-documents/{id}/issues           -> ListEnvelope<QuotationIssue>
 *   POST   /projects/{projectId}/quotation-documents/{id}/sign             -> QuotationSignature (201)
 *   POST   /projects/{projectId}/quotation-documents/{id}/issues/{issueId}/sign-link
 *                                                                         -> QuotationSignLink
 *   POST   /projects/{projectId}/quotation-documents/{id}/issues/{issueId}/export/pdf
 *                                                                         -> QueuedDownload
 *   POST   /projects/{projectId}/quotation-documents/{id}/issues/{issueId}/export/xlsx
 *                                                                         -> QueuedDownload
 *
 * The price-floor approval gate (S14-S16) adds four more, and one read:
 *
 *   GET    /quotation-approval-graph                                       -> StatusGraph
 *   POST   /projects/{projectId}/quotation-documents/{id}/approval-status  -> QuotationDocument
 *   POST   /projects/{projectId}/quotation-documents/{id}/approve          -> QuotationDocument
 *   POST   /projects/{projectId}/quotation-documents/{id}/reject           -> QuotationDocument
 *
 * `/issue` gains one refusal: 422 `quotation_below_floor_pending_approval` when the document
 * carries a line priced below its floor and is not `approved`.
 *
 * The two exports are QUEUED, not rendered in the response: a 50-page quotation held the
 * browser long enough to read as a broken button. The route answers with a `user_downloads`
 * row in status `pending`; the file arrives in My Downloads and behind the printer chip on the
 * document header. The backend still exposes the inline GET `.../pdf` and `.../xlsx` renders
 * for API/automation callers, but this screen no longer uses them.
 *
 * Money arrives as a decimal STRING, never a JS number: `1805907.02` parsed as a float and added
 * back up is how a quotation ends up disagreeing with its own PDF by a cent. Format it, do not
 * arithmetic it - the backend already excluded rate-only lines from every total it sends.
 */

const BASE = '/api/v1/project-sales';

export type QuotationScope = {
  id: string;
  scope_label: string;
  sort_order: number;
  outcome: string;
  current_version_id: string | null;
  current_version_no: number | null;
  line_count: number;
  scope_total: string;
};

/** One stored signature, the shape `quotation_signatures` serializes on every signing route. */
export type QuotationSignatureRecord = {
  id: string;
  signer_name: string | null;
  mode: string;
  image_data_uri: string | null;
  signed_at: string | null;
  ip_address: string | null;
  /** Decimal strings, or null when the browser refused. Shown as `-`, never guessed. */
  gps_lat: string | null;
  gps_lng: string | null;
  /**
   * The nearest known town to those coordinates, e.g. "Kajang, Selangor". Resolved by the backend
   * from one offline table it shares with the PDF renderer, so this screen and the printed
   * document cannot disagree about where somebody signed. Null when nothing known is near enough
   * to name honestly, in which case the coordinates are shown alone.
   */
  gps_place?: string | null;
};

export type QuotationDocument = {
  id: string;
  project_id: string;
  document_no: string;
  /** The number plus the revision the customer holds, e.g. "SRT/Q/2026/0141 (R2)". */
  our_ref: string | null;
  your_ref: string | null;
  doc_date: string | null;
  recipient_party_id: string | null;
  recipient_name_snapshot: string | null;
  recipient_address_snapshot: string | null;
  recipient_phone_snapshot: string | null;
  attn_name: string | null;
  subject_title: string | null;
  cover_letter_html: string | null;
  terms_html: string | null;
  signatory_name: string | null;
  signatory_phone: string | null;
  scopes: QuotationScope[];
  grand_total: string;
  issue_count: number;
  current_issue_no: number | null;
  is_issued: boolean;
  created_at: string | null;
  updated_at: string | null;
  /**
   * The signature held on the DRAFT, which is what makes the document issuable (AC-H1).
   *
   * Serialized on every document read, so it survives a refresh. It has to: the Issue CTA is
   * gated on it, and the only alternative signal (`is_issued`) gives the wrong answer on a
   * document issued before the signature gate existed.
   */
  signatory_signature?: QuotationSignatureRecord | null;
  /** The same fact as a flag, for list rows that do not need the ink itself. */
  is_signed?: boolean;

  /**
   * Where this document stands on the `quotation` approval graph, or null.
   *
   * NULL is the normal answer and it means "this quotation has never needed a manager".
   * A document only enters the graph when a below-floor line makes it need one, so the
   * common case carries no graph position at all and its Issue flow is untouched.
   */
  approval_status_id?: string | null;
  /** draft | pending_approval | approved | rejected | issued, or null. */
  approval_status_key?: string | null;
  /** What the admin called that rung on the status graph. Rendered, never the key. */
  approval_status_label?: string | null;
  /** Why the manager sent it back, present only while it stands at `rejected`. */
  approval_rejected_reason?: string | null;
  /**
   * A line on a scope this document would issue is priced below its floor, so issuing needs
   * an approved status. Computed server-side from the stored per-line `is_below_floor`, never
   * re-derived here: the floor that applied is the one that applied when the line was priced.
   */
  requires_approval?: boolean;
  /** How many such lines, so the block can say how much there is to look at. */
  below_floor_line_count?: number;

  /**
   * What the customer did with the revision they are CURRENTLY holding (S17).
   *
   * Carried on the document, not only on the issue, because the two screens that have to say it
   * - the banner on this document and the Status column of the project's quotation list - do not
   * fetch the issue history. Read off the latest issue server-side, so revising and re-issuing
   * clears it rather than nagging about a request that has already been answered.
   */
  accepted_at?: string | null;
  changes_requested_at?: string | null;
  changes_requested_note?: string | null;
  changes_requested_by_name?: string | null;
  /**
   * The resolved answer, decided once in the serializer: `accepted` beats `changes_requested`
   * when both stamps are set. Never re-derive it from the two dates on a screen - that is how
   * the list ends up disagreeing with the document about a quotation that is already won.
   */
  customer_decision?: 'accepted' | 'changes_requested' | null;
};

export type QuotationIssue = {
  id: string;
  document_id: string;
  issue_no: number;
  our_ref_text: string | null;
  issued_at: string | null;
  issued_by: string | null;
  issued_by_name: string | null;
  grand_total: string;
  scope_count: number;
  /**
   * The customer's acceptance. It belongs to the ISSUE, because that is the thing they held and
   * signed, but the screen watching for it is the document, so it travels with the issue history.
   */
  customer_signature?: QuotationSignatureRecord | null;
  accepted_at?: string | null;
  is_accepted?: boolean;
  /**
   * The other answer the customer can give (S17): they will not sign it as it stands, and why.
   *
   * Travels on the same issue history for the same reason the acceptance does. This is what the
   * salesperson reads before pressing Revise - the notification is the nudge, this is the record.
   */
  changes_requested_at?: string | null;
  changes_requested_note?: string | null;
  changes_requested_by_name?: string | null;
  is_changes_requested?: boolean;
};

export type QuotationDocumentBody = Partial<{
  your_ref: string | null;
  doc_date: string | null;
  attn_name: string | null;
  subject_title: string | null;
  cover_letter_html: string | null;
  terms_html: string | null;
  signatory_name: string | null;
  signatory_phone: string | null;
  /**
   * The recipient block, PATCH only.
   *
   * Snapshotted from the project's developer party when the document is created and deliberately
   * never re-derived after that, so a correction here is a correction to THIS quotation's copy -
   * the finance department having a different mailing address to the party record, say. It does
   * not touch the party, and the party changing later does not touch it back.
   */
  recipient_name_snapshot: string | null;
  recipient_address_snapshot: string | null;
  recipient_phone_snapshot: string | null;
  /**
   * The quotation form page's scopes (#1341), on the POST and the PATCH alike. The create writes
   * the document, these scopes and their lines in ONE transaction; the edit applies them in one
   * too. On a PATCH a saved scope carries its `id`, and a saved scope left out is not touched.
   */
  scopes: QuotationFormScopeBody[];
  /**
   * Saved scopes Edit quotation deletes (#1341, owner on Q2: "yes can"), PATCH only. The server
   * refuses with 422 `quotation_scope_issued` for a scope any version of which was sent to the
   * customer, and nothing in that save lands.
   */
  remove_scope_ids: string[];
}>;

/** The company's letter and terms as written, for the create form's own tabs (#1341). */
export type QuotationLetterTemplates = {
  cover_letter_html: string | null;
  terms_html: string | null;
};

/**
 * One scope as the form sends it. `lines` is the FULL line set of the scope's current version
 * (whole-set: a stored line left out is deleted); leave it out to keep a scope's lines as they are,
 * which is what the form does for a version the customer holds.
 */
export type QuotationFormScopeBody = {
  id?: string;
  scope_label?: string;
  series_id?: string | null;
  notes?: string | null;
  lines?: QuotationLineBulkItem[];
};

type Envelope<T> = { data: T[]; pagination: { total: number }; empty: boolean };

export async function listQuotationDocuments(
  projectId: string,
): Promise<QuotationDocument[]> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/quotation-documents`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load quotations'));
  const body: Envelope<QuotationDocument> = await response.json();
  return body.data ?? [];
}

export async function getQuotationDocument(
  projectId: string,
  documentId: string,
): Promise<QuotationDocument> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}`,
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load this quotation'));
  return response.json();
}

export async function getQuotationLetterTemplates(
  projectId: string,
): Promise<QuotationLetterTemplates> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/letter-templates`,
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the letter templates'));
  return response.json();
}

export async function createQuotationDocument(
  projectId: string,
  body: QuotationDocumentBody = {},
): Promise<QuotationDocument> {
  const response = await apiFetch(`${BASE}/projects/${projectId}/quotation-documents`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to create the quotation'));
  return response.json();
}

export async function updateQuotationDocument(
  projectId: string,
  documentId: string,
  body: QuotationDocumentBody,
): Promise<QuotationDocument> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}`,
    { method: 'PATCH', body: JSON.stringify(body) },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to save the quotation'));
  return response.json();
}

export async function deleteQuotationDocument(
  projectId: string,
  documentId: string,
): Promise<void> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}`,
    { method: 'DELETE' },
  );
  // 422 once issued, and the server's message names withdrawal as the way out, so it is
  // surfaced rather than replaced with a generic failure.
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to delete the quotation'));
}

export async function addQuotationScope(
  projectId: string,
  documentId: string,
  body: { scope_label: string; series_id?: string | null; notes?: string | null },
): Promise<QuotationScope> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/scopes`,
    { method: 'POST', body: JSON.stringify(body) },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to add the scope'));
  return response.json();
}

export async function updateQuotationScope(
  projectId: string,
  documentId: string,
  scopeId: string,
  body: Partial<{ scope_label: string; sort_order: number; notes: string | null }>,
): Promise<QuotationScope> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/scopes/${scopeId}`,
    { method: 'PATCH', body: JSON.stringify(body) },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to save the scope'));
  return response.json();
}

export async function issueQuotationDocument(
  projectId: string,
  documentId: string,
): Promise<QuotationIssue> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/issue`,
    { method: 'POST' },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to send this quotation to the customer'));
  return response.json();
}

/**
 * The `quotation` approval graph, as the status engine resolves it.
 *
 * Its own route under project-sales rather than the admin `/statuses/graph/{entity}` one,
 * which is gated on `system.statuses.view` and held by administrators alone: a salesperson
 * has to be able to read the rung their own quotation stands on. Same response shape, so the
 * shared `availableStatusMoves` / `splitStatusMoves` helpers read it unchanged.
 */
export async function getQuotationApprovalGraph(): Promise<StatusGraph> {
  const response = await apiFetch(`${BASE}/quotation-approval-graph`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the approval steps'));
  return response.json();
}

/**
 * Move the document along the approval graph. Only the salesperson's own two moves go
 * through here - sending it for approval, and taking a rejected one back to draft.
 *
 * Approving, rejecting and issuing are each their own act with their own rules (a permission,
 * a required reason, a frozen revision), so the server refuses them on this route rather than
 * letting a generic move perform them without those rules.
 */
export async function moveQuotationApproval(
  projectId: string,
  documentId: string,
  toStatusId: string,
): Promise<QuotationDocument> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/approval-status`,
    { method: 'POST', body: JSON.stringify({ to_status_id: toStatusId }) },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to move this quotation'));
  return response.json();
}

/** The manager accepts the below-floor pricing. The next Issue press then proceeds. */
export async function approveQuotationDocument(
  projectId: string,
  documentId: string,
): Promise<QuotationDocument> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/approve`,
    { method: 'POST' },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to approve this quotation'));
  return response.json();
}

/**
 * The manager sends it back, and the reason is REQUIRED: "rejected" with no reason leaves the
 * salesperson guessing at which line to move, which is the whole thing this gate exists to stop.
 */
export async function rejectQuotationDocument(
  projectId: string,
  documentId: string,
  reason: string,
): Promise<QuotationDocument> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/reject`,
    { method: 'POST', body: JSON.stringify({ reason }) },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to reject this quotation'));
  return response.json();
}

/**
 * The creator reopens a pending or approved quotation to change it. The reason is required and
 * lands in the project's feed; the document comes back at draft.
 */
export async function reviseQuotationApproval(
  projectId: string,
  documentId: string,
  reason: string,
): Promise<QuotationDocument> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/approval-revise`,
    { method: 'POST', body: JSON.stringify({ reason }) },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to reopen this quotation'));
  return response.json();
}

export async function listQuotationIssues(
  projectId: string,
  documentId: string,
): Promise<QuotationIssue[]> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/issues`,
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the revisions'));
  const body: Envelope<QuotationIssue> = await response.json();
  return body.data ?? [];
}

/**
 * Sign the DRAFT. Separate from issuing on purpose: a person signs, reads the document, then
 * issues. `signer_name` may be omitted, in which case the backend falls back to the document's
 * signatory. IP and user agent are stamped server-side - the browser must not source its own
 * provenance.
 */
export type QuotationSignatureBody = {
  signer_name?: string | null;
  mode: string;
  image_data_uri: string;
  /** Strings, not numbers: the column is NUMERIC(10,7) and a float round-trip can move it. */
  gps_lat?: string | null;
  gps_lng?: string | null;
};

export async function signQuotationDocument(
  projectId: string,
  documentId: string,
  body: QuotationSignatureBody,
): Promise<QuotationSignatureRecord> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/sign`,
    { method: 'POST', body: JSON.stringify(body) },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to save the signature'));
  return response.json();
}

export type QuotationSignLink = {
  token: string;
  /** Relative on purpose: the origin belongs to whoever is sending the link. */
  path: string;
  expires_at: string | null;
};

export async function createQuotationSignLink(
  projectId: string,
  documentId: string,
  issueId: string,
): Promise<QuotationSignLink> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/issues/${issueId}/sign-link`,
    { method: 'POST' },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to create the counter-sign link'));
  return response.json();
}

/**
 * One queued export, the `user_downloads` row the trigger routes answer with.
 *
 * Deliberately the same shape the My Downloads drawer already reads, so the printer chip on the
 * document and the drawer in the top nav are looking at one thing rather than two.
 */
export type QueuedDownload = {
  id: string;
  kind: string;
  status: 'pending' | 'processing' | 'ready' | 'failed';
  filename: string | null;
  source_entity_type: string | null;
  source_entity_id: string | null;
};

/**
 * Queue the issued quotation's PDF, rendered from the issue snapshot by the worker.
 *
 * Nothing comes back but the receipt. The wait was the client's complaint: a long render held
 * the browser and read as a dead button, so the file is collected from My Downloads (or the
 * printer chip on this document) once it is ready.
 */
export async function queueQuotationIssuePdf(
  projectId: string,
  documentId: string,
  issueId: string,
): Promise<QueuedDownload> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/issues/${issueId}/export/pdf`,
    { method: 'POST' },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to queue the PDF'));
  return response.json();
}

/**
 * The same issue as a workbook, one sheet per scope, queued the same way.
 *
 * A separate call rather than a format flag: the two artifacts have different audiences (the PDF
 * is the document of record, this is what the customer's QS re-prices in) and each gets its own
 * download row so the drawer can say which format is ready.
 */
export async function queueQuotationIssueXlsx(
  projectId: string,
  documentId: string,
  issueId: string,
): Promise<QueuedDownload> {
  const response = await apiFetch(
    `${BASE}/projects/${projectId}/quotation-documents/${documentId}/issues/${issueId}/export/xlsx`,
    { method: 'POST' },
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to queue the Excel file'));
  return response.json();
}

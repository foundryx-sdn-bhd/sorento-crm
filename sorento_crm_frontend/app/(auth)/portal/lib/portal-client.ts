/**
 * Portal API client - token-scoped, no NextAuth session.
 *
 * The token is stored in localStorage (device trust - survives tab close;
 * the BE gives verified tokens a sliding 30-day TTL) and sent as
 * `X-Portal-Token` on every request. On 401 the caller is responsible for
 * redirecting to the verify page.
 *
 * Impersonation sessions are the exception: their token lives in
 * sessionStorage only, so an admin "view as contact" never leaves a 30-day
 * credential on the admin's machine. sessionStorage wins on read so an
 * active impersonation takes precedence in its tab.
 */
import { extractApiError } from '@/lib/api-client';
import {
  formCommentFormData,
  toFormComment,
  toFormCommentPostResult,
} from '@/services/formCommentsService';
import type {
  FormComment,
  FormCommentPostResult,
} from '@/types/formComments';
import type {
  PortalLandingKind,
  PortalSubmissionKind,
} from '@/lib/portal-form-kinds';
import {
  designPayloadFromResponse,
  type TagSheetDesignPayload,
  type TagSheetDesignResponse,
} from '@/lib/dealer-kit/design-payload';

const TOKEN_KEY = 'sorento.portalToken';

// The kind lists and their labels live in `lib/portal-form-kinds.ts` so the CRM's
// Contact Access Types screen can offer the same five kinds with the same labels
// (D61b) without importing this module's token storage. Re-exported here, so
// every existing `from '.../portal-client'` import keeps working.
export {
  SUBMISSION_KINDS,
  LANDING_KINDS,
  ADDITIONAL_LANDING_KINDS,
  SUBMISSION_LABELS,
  LANDING_LABELS,
  isSubmissionKind,
  isLandingKind,
  portalFormKindLabel,
} from '@/lib/portal-form-kinds';
export type {
  PortalSubmissionKind,
  PortalLandingKind,
} from '@/lib/portal-form-kinds';

export interface PortalImpersonationInfo {
  session_id: string;
  admin_user_id: string;
  admin_name: string | null;
  admin_email: string | null;
  started_at: string;
}

export interface PortalContact {
  contact_id: string;
  space_id: string;
  name: string | null;
  phone_number: string | null;
  expires_at: string;
  /** Stable slug behind the bookmarkable URL /portal/c/{slug}. */
  portal_slug?: string | null;
  /** Business WhatsApp number (digits) for the wa.me escape hatch. */
  whatsapp_number?: string | null;
  /**
   * AC-F4: when true, this contact's sponsorship form demands a registered project and
   * cannot be submitted without one. Per contact, so the requirement rolls out one team
   * at a time instead of breaking the form for everybody on the same day. The server
   * enforces it regardless of what the form does.
   */
  requires_registered_project?: boolean;
  impersonation?: PortalImpersonationInfo | null;
  /** Portal form types this contact may see (access-type union + overrides). */
  visible_form_types?: string[];
}

export interface PortalSubmissionSummary {
  id: string;
  /** Widened for D45: a gated kind is listed on the same landing, in the same
   *  card, and is adapted into this shape by its own feature service. */
  kind: PortalLandingKind;
  title: string;
  document_number?: string | null;
  reference: string | null;
  status: string;
  approval_status?: string | null;
  rejection_reason?: string | null;
  is_editable: boolean;
  is_draft: boolean;
  created_at: string | null;
  /** Denormalized revision counter (UAC N) - 0/null means never revised. */
  revision_no?: number | null;
  /** Set the moment a revision lands, so a list can sort/label by it. */
  last_revised_at?: string | null;
  /** True while an unsent revision draft is parked on this submission - the
   *  row's own `status` stays the real one; this is what tells the two apart. */
  has_revision_draft?: boolean;
  // Optional kind-specific summary fields surfaced for the mobile card layout.
  product_code?: string | null;
  project_title?: string | null;
  /** AC-F3: the registered project this sponsorship names, when it names one. */
  project_id?: string | null;
  /** Human-readable reference for that project, because a UUID is never the answer. */
  project_code?: string | null;
  project_name?: string | null;
  project_customer?: string | null;
  customer_name?: string | null;
  delivery_order_number?: string | null;
  item_description?: string | null;
  sponsor_subject?: string | null;
  purpose?: string | null;
  /** Price tag request: the date the tags are wanted for. The card shows it,
   *  because a deadline is the one thing a salesperson scans this list for. */
  needed_by_date?: string | null;
  /** Sales opportunity (fix lane round 2, F1): the stage's own label, so a card never shows
   *  a stage key, and the figures the card carries. */
  status_label?: string | null;
  expected_amount?: string | null;
  expected_close_date?: string | null;
}

export type PortalAttachmentUploaderKind = 'user' | 'contact' | 'system' | null;
export type PortalAttachmentUploaderRole = 'contact' | 'staff' | 'unknown';

export interface PortalAttachment {
  link_id: string;
  attachment_id: string;
  filename: string | null;
  size: number | null;
  url: string | null;
  content_type?: string | null;
  uploaded_at?: string | null;
  /** 'user' = uploaded by CRM staff, 'contact' = uploaded by this contact,
   *  'system' = worker-created. Absent/null on legacy rows. */
  uploader_kind?: PortalAttachmentUploaderKind;
  uploaded_by_name?: string | null;
  uploaded_by_role?: PortalAttachmentUploaderRole;
  /** false for staff ('user') uploads - the contact cannot unlink those. */
  can_unlink?: boolean;
}

/**
 * Everything the portal needs to render (or not render) the Revise action.
 * Comes back on the submission detail itself, so no extra round trip.
 * `used` is the current revision_no, echoed back as `expected_revision_no` so a
 * double tap cannot produce two revisions.
 */
export interface PortalRevisionPolicy {
  enabled: boolean;
  allowed: boolean;
  used: number;
  max: number;
  remaining: number;
  blocked_reason: string | null;
  /**
   * Where a revision sends this form back to, in words ("Project Sales",
   * "Customer Service"). Resolved from the type's revision config plus its SLA
   * chain, so the confirm dialog names the real destination instead of promising
   * one it does not know (UAC E1a). Null when there is nothing to name, and the
   * generic sentence stands.
   */
  restart_stage_label?: string | null;
}

/**
 * A revision the contact saved before Send revision. `stale` is true when the
 * submission changed (its revision_no moved) since the draft was started - the
 * draft is then not resumed automatically, only offered for discard.
 */
export interface PortalRevisionDraft {
  fields: Record<string, unknown>;
  reason: string | null;
  base_revision_no: number;
  updated_at: string;
  stale: boolean;
}

/**
 * One file as it stood at a given version.
 *
 * The snapshot itself stores no url - a signed one would be dead by the time
 * history is read - so the backend resolves `url` per entry at read time,
 * including for a file whose link a later revision removed. That is what keeps a
 * historical file previewable in place (UAC I2a / G6). Null when the attachment
 * row is gone or its signer failed.
 */
export interface PortalRevisionAttachment {
  attachment_id: string;
  link_id?: string | null;
  filename?: string | null;
  size?: number | null;
  mime?: string | null;
  url?: string | null;
}

export interface PortalRevisionChange {
  field: string;
  label: string;
  from: unknown;
  to: unknown;
}

/** One stage a revision voided, and who was working it. */
export interface PortalRevisionVoidedStage {
  stage_code?: string | null;
  assignee_name?: string | null;
}

/** One labeled field of a revision's snapshot, in form order - same shape the
 *  office timeline reads, rendered by the backend from its adapter field list. */
export interface PortalRevisionSnapshotField {
  field: string;
  label: string;
  value: unknown;
  /** Server-rendered presentation of `value` (a lookup option's label, a
   *  DD/MM/YYYY date). Null when the raw value already reads correctly. */
  display?: string | null;
}

export interface PortalRevisionEntry {
  id: string;
  version_no: number;
  revision_no: number;
  kind: string;
  /** Server-rendered: "Original", "Original (reconstructed)", "Revision 2", ... */
  label: string;
  reason: string | null;
  submitted_at: string | null;
  submitted_by: string | null;
  is_reconstructed: boolean;
  snapshot: Record<string, unknown>;
  attachments: PortalRevisionAttachment[];
  invalidated: Record<string, unknown> | null;
  voided_stage_code: string | null;
  voided_assignee_name: string | null;
  /** EVERY stage this revision voided, newest first. Optional: a revision row
   *  written before `voided_stages_json` existed carries only the scalar pair
   *  above, which the timeline falls back to. */
  voided_stages?: PortalRevisionVoidedStage[] | null;
  changes: PortalRevisionChange[];
  /** The whole form at this version, labeled and ordered. Optional: absent on a
   *  payload from a backend that predates the full-form viewer. */
  snapshot_fields?: PortalRevisionSnapshotField[] | null;
}

export interface PortalSubmissionDetail extends PortalSubmissionSummary {
  attachments?: PortalAttachment[];
  revision?: PortalRevisionPolicy;
  revision_draft?: PortalRevisionDraft | null;
  // Free-form fields per type, populated by the backend serializer.
  [key: string]: unknown;
}

export class PortalUnauthorizedError extends Error {
  constructor(message = 'Portal session expired.') {
    super(message);
    this.name = 'PortalUnauthorizedError';
  }
}

/**
 * The submission exists but belongs to a different contact than the current
 * session (403 OWNER_MISMATCH). The current token stays valid - the caller
 * should route to the owner's confirm-identity card (the deep-link slug), not
 * clear the session.
 */
export class PortalOwnerMismatchError extends Error {
  constructor(message = 'This submission belongs to another contact.') {
    super(message);
    this.name = 'PortalOwnerMismatchError';
  }
}

export function readPortalToken(): string | null {
  if (typeof window === 'undefined') return null;
  // Impersonation (sessionStorage) wins over device trust (localStorage).
  return (
    window.sessionStorage.getItem(TOKEN_KEY) ??
    window.localStorage.getItem(TOKEN_KEY)
  );
}

export function writePortalToken(
  token: string,
  opts: { impersonation?: boolean } = {},
): void {
  if (typeof window === 'undefined') return;
  if (opts.impersonation) {
    window.sessionStorage.setItem(TOKEN_KEY, token);
  } else {
    window.localStorage.setItem(TOKEN_KEY, token);
    // Drop any stale impersonation token so the new identity wins.
    window.sessionStorage.removeItem(TOKEN_KEY);
  }
}

export function clearPortalToken(): void {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(TOKEN_KEY);
}

/**
 * Clear ONLY the session-scoped (impersonation) token. Used on impersonation
 * exit so an admin's own device-trust token in localStorage survives.
 */
export function clearImpersonationToken(): void {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(TOKEN_KEY);
}

/** True when the active token is an impersonation (sessionStorage) token. */
export function isImpersonationToken(): boolean {
  if (typeof window === 'undefined') return false;
  return window.sessionStorage.getItem(TOKEN_KEY) != null;
}

/**
 * Migrate the active token into sessionStorage. Safety net for impersonation
 * links that lack the `?impersonation=1` marker: once /me reveals an
 * impersonation session, the token must not persist on the admin's machine.
 */
export function demoteTokenToSession(): void {
  if (typeof window === 'undefined') return;
  const t = window.localStorage.getItem(TOKEN_KEY);
  if (t) {
    window.sessionStorage.setItem(TOKEN_KEY, t);
    window.localStorage.removeItem(TOKEN_KEY);
  }
}

/**
 * Resolve the backend base URL.
 *
 * - In production (or when NEXT_PUBLIC_API_URL is set), this is the absolute
 *   API host.
 * - In development with no NEXT_PUBLIC_API_URL, the empty string keeps URLs
 *   relative so the Next.js rewrite proxies `/api/v1/*` to the FastAPI server.
 *
 * Multipart uploads use {@link absoluteApiUrl} to bypass the rewrite, since
 * Next.js dev rewrites can mangle streaming `multipart/form-data` bodies.
 */
function apiBase(): string {
  if (typeof process !== 'undefined') {
    const env = process.env?.NEXT_PUBLIC_API_URL;
    if (env) return env.replace(/\/$/, '');
  }
  return '';
}

function absoluteApiUrl(path: string): string {
  const base = apiBase();
  if (base) return `${base}${path.startsWith('/') ? path : `/${path}`}`;
  // Dev fallback: hit FastAPI directly to bypass the Next.js rewrite, which
  // can corrupt multipart bodies in some Next 15 builds.
  if (typeof window !== 'undefined') {
    const port = window.location.port;
    if (port === '3000' || port === '3001') {
      return `${window.location.protocol}//${window.location.hostname}:8000${path}`;
    }
  }
  return path;
}

export async function portalFetch(
  input: string,
  init: RequestInit = {},
): Promise<Response> {
  const token = readPortalToken();
  const headers = new Headers(init.headers || {});
  if (token) headers.set('X-Portal-Token', token);
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401) {
    clearPortalToken();
    throw new PortalUnauthorizedError();
  }
  return res;
}

async function portalMultipartFetch(
  path: string,
  form: FormData,
): Promise<Response> {
  const token = readPortalToken();
  const url = absoluteApiUrl(path);
  // IMPORTANT: do NOT pass a Content-Type header. fetch must auto-set
  // 'multipart/form-data; boundary=...' when body is FormData. Passing a
  // Headers object with only X-Portal-Token is fine; fetch fills in CT itself.
  const init: RequestInit = {
    method: 'POST',
    body: form,
    headers: token ? { 'X-Portal-Token': token } : undefined,
  };
  // Cross-origin in dev: we hit :8000 from :3000. CORS must allow.
  if (url.startsWith('http')) {
    init.mode = 'cors';
    init.credentials = 'omit';
  }
  const res = await fetch(url, init);
  if (res.status === 401) {
    clearPortalToken();
    throw new PortalUnauthorizedError();
  }
  return res;
}

export async function unwrap<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    const message = await extractApiError(res, fallback);
    throw new Error(message);
  }
  return (await res.json()) as T;
}

export async function fetchMe(): Promise<PortalContact> {
  const res = await portalFetch('/api/v1/public/portal/me');
  return unwrap<PortalContact>(res, 'Failed to load profile.');
}

/**
 * fetchMe with the fresh-token grace retry: a token written by /verify-otp in
 * the last 30s can transiently 401 (commit-visibility race between the verify
 * commit and a fast follow-up /me). portalFetch clears the token on 401, so
 * restore it and retry once before treating the session as dead.
 */
export async function fetchMeWithGrace(): Promise<PortalContact> {
  const existing = readPortalToken();
  const wasImpersonation = isImpersonationToken();
  const writtenAt =
    typeof window !== 'undefined'
      ? Number(
          window.sessionStorage.getItem('sorento.portalTokenWrittenAt') || '0',
        )
      : 0;
  const tokenIsFresh = writtenAt > 0 && Date.now() - writtenAt < 30_000;
  try {
    return await fetchMe();
  } catch (firstErr) {
    if (
      firstErr instanceof PortalUnauthorizedError &&
      tokenIsFresh &&
      existing
    ) {
      // Restore to the SAME store it came from - an impersonation token must
      // not be promoted into localStorage by the retry.
      writePortalToken(existing, { impersonation: wasImpersonation });
      await new Promise((r) => setTimeout(r, 500));
      return fetchMe();
    }
    throw firstErr;
  }
}

export async function stopPortalImpersonation(): Promise<void> {
  const res = await portalFetch('/api/v1/public/portal/impersonation/stop', {
    method: 'POST',
  });
  if (!res.ok) {
    throw new Error(
      await extractApiError(res, 'Failed to exit impersonation.'),
    );
  }
}

export async function fetchSubmissions(
  kind: PortalSubmissionKind,
  q?: string,
): Promise<PortalSubmissionSummary[]> {
  const usp = new URLSearchParams({ type: kind });
  if (q && q.trim()) usp.set('q', q.trim());
  const res = await portalFetch(
    `/api/v1/public/portal/submissions?${usp.toString()}`,
  );
  const data = await unwrap<{ items: PortalSubmissionSummary[] }>(
    res,
    'Failed to load submissions.',
  );
  return data.items ?? [];
}

export async function fetchSubmission(
  kind: PortalLandingKind,
  id: string,
): Promise<PortalSubmissionDetail> {
  const res = await portalFetch(
    `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(id)}`,
  );
  // 403 OWNER_MISMATCH: session is valid but the form belongs to another
  // contact - surface as a typed error so the caller offers owner login.
  if (res.status === 403) {
    // The global handler flattens AppException to {message, detail, code}.
    const body = await res
      .clone()
      .json()
      .catch(() => ({}));
    const code = body?.code ?? body?.detail?.code;
    if (code === 'OWNER_MISMATCH') {
      throw new PortalOwnerMismatchError(
        body?.message ?? body?.detail?.message,
      );
    }
  }
  return unwrap<PortalSubmissionDetail>(res, 'Failed to load submission.');
}

/**
 * Same shape `PriceTagRequestError` reads (`price-tag-request-service.ts`'s
 * `unwrapNamingFields`): the global handler flattens `AppException` to a FLAT
 * top-level `{message, detail, code}` - not `{detail: {message, detail, code}}` -
 * and `detail` on a line-scoped refusal (D48/#1227: a sponsorship line submitted
 * with no unit price) is a CSV of `line:<index>` tokens. Attached to the thrown
 * error as `.fields`/`.code` so a caller can name the offending line the same way
 * a price tag request line already does, without changing what every other
 * `saveDraft`/`submitDraft` caller already gets back.
 *
 * The body can only be consumed once, so it is cloned BEFORE `extractApiError`
 * (which owns and consumes the original) - not after, or the clone throws on an
 * already-consumed body and is silently swallowed. `message` reads the body's
 * own top-level `message` directly rather than `extractApiError`'s return value:
 * `extractApiError` returns the string `detail` whenever one is present (its own
 * contract for a plain string `detail`), and `detail` here is the `line:<index>`
 * token, not the human message - `extractApiError` is used only as the fallback
 * for a body this handler did not shape (network/HTML/plain-`detail` errors).
 */
async function throwSubmissionError(res: Response, fallback: string): Promise<never> {
  const spare = res.clone();
  let body: { message?: unknown; detail?: unknown; code?: unknown } | null = null;
  try {
    body = (await spare.json()) as { message?: unknown; detail?: unknown; code?: unknown };
  } catch {
    body = null;
  }
  const message =
    typeof body?.message === 'string' && body.message
      ? body.message
      : await extractApiError(res, fallback);
  const code = typeof body?.code === 'string' ? body.code : null;
  const fields =
    typeof body?.detail === 'string' && body.detail
      ? body.detail.split(',').map((f) => f.trim()).filter(Boolean)
      : [];
  throw Object.assign(new Error(message), { code, fields });
}

export async function saveDraft(
  kind: PortalSubmissionKind,
  fields: Record<string, unknown>,
  products?: Record<string, unknown>[],
  id?: string,
): Promise<PortalSubmissionDetail> {
  const url = id
    ? `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(id)}`
    : `/api/v1/public/portal/submissions/${kind}`;
  const res = await portalFetch(url, {
    method: id ? 'PUT' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fields, products }),
  });
  if (!res.ok) return throwSubmissionError(res, 'Failed to save draft.');
  return (await res.json()) as PortalSubmissionDetail;
}

export async function submitDraft(
  kind: PortalSubmissionKind,
  id: string,
  fields?: Record<string, unknown>,
  products?: Record<string, unknown>[],
): Promise<PortalSubmissionDetail> {
  const body =
    fields || products
      ? JSON.stringify({ fields: fields || {}, products })
      : undefined;
  const res = await portalFetch(
    `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(id)}/submit`,
    {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body,
    },
  );
  if (!res.ok) return throwSubmissionError(res, 'Failed to submit.');
  return (await res.json()) as PortalSubmissionDetail;
}

/** GET .../submissions/{kind}/{id}/revisions - the original plus every version
 *  since, oldest first, each carrying what changed vs the one before it. */
export async function fetchRevisions(
  kind: PortalLandingKind,
  id: string,
): Promise<PortalRevisionEntry[]> {
  const res = await portalFetch(
    `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(id)}/revisions`,
  );
  const data = await unwrap<{ items?: PortalRevisionEntry[] }>(
    res,
    'Failed to load revision history.',
  );
  return data.items ?? [];
}

export interface ReviseSubmissionInput {
  reason: string;
  /** The revision_no the contact was looking at. A mismatch is a 409. */
  expectedRevisionNo: number;
  fields: Record<string, unknown>;
  products?: Record<string, unknown>[];
}

export interface ReviseSubmissionResult {
  submission: PortalSubmissionDetail;
  revision: PortalRevisionPolicy;
  revision_no: number;
}

/**
 * POST .../submissions/{kind}/{id}/revise - send a revision.
 *
 * 409 (someone revised it first / double tap) and most 422s (policy refused it)
 * carry one human sentence. #1232 blocking 4: revise is a second submission path
 * with the same sponsorship unit-price gate `submitDraft` has, so a refusal from
 * it can carry the same `line:<index>` naming - routed through the same
 * `throwSubmissionError` `submitDraft`/`saveDraft` use rather than the plain
 * `unwrap` this used before (which would have read the flat body's `detail`
 * token, e.g. "line:0", as the message - the exact bug already fixed there).
 */
export async function reviseSubmission(
  kind: PortalLandingKind,
  id: string,
  input: ReviseSubmissionInput,
): Promise<ReviseSubmissionResult> {
  const res = await portalFetch(
    `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(id)}/revise`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        reason: input.reason,
        expected_revision_no: input.expectedRevisionNo,
        fields: input.fields,
        products: input.products,
      }),
    },
  );
  if (!res.ok) return throwSubmissionError(res, 'Failed to send revision.');
  return (await res.json()) as ReviseSubmissionResult;
}

export interface SaveRevisionDraftInput {
  reason?: string | null;
  baseRevisionNo: number;
  fields: Record<string, unknown>;
  products?: Record<string, unknown>[];
}

/** PUT .../submissions/{kind}/{id}/revision-draft - save (or update) an
 *  in-progress revision, without sending it. */
export async function saveRevisionDraft(
  kind: PortalSubmissionKind,
  id: string,
  input: SaveRevisionDraftInput,
): Promise<PortalRevisionDraft> {
  const res = await portalFetch(
    `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(id)}/revision-draft`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        reason: input.reason ?? null,
        base_revision_no: input.baseRevisionNo,
        fields: input.fields,
        products: input.products,
      }),
    },
  );
  return unwrap<PortalRevisionDraft>(res, 'Failed to save draft.');
}

/** DELETE .../submissions/{kind}/{id}/revision-draft - discard the in-progress
 *  revision, if any. Idempotent. */
export async function discardRevisionDraft(
  kind: PortalSubmissionKind,
  id: string,
): Promise<void> {
  const res = await portalFetch(
    `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(id)}/revision-draft`,
    { method: 'DELETE' },
  );
  if (!res.ok) {
    throw new Error(await extractApiError(res, 'Failed to discard draft.'));
  }
}

export async function deleteDraftSubmission(
  kind: PortalSubmissionKind,
  id: string,
): Promise<void> {
  const res = await portalFetch(
    `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  );
  if (!res.ok && res.status !== 204) {
    const message = await extractApiError(res, 'Failed to delete draft.');
    throw new Error(message);
  }
}

export async function uploadAttachment(
  kind: PortalLandingKind,
  submissionId: string,
  file: File,
): Promise<PortalAttachment> {
  const form = new FormData();
  form.set('kind', kind);
  form.set('submission_id', submissionId);
  form.set('file', file, file.name);
  const res = await portalMultipartFetch(
    '/api/v1/public/portal/attachments',
    form,
  );
  return unwrap<PortalAttachment>(res, 'Upload failed.');
}

/** The submission's comment thread, oldest first (FORM-COMMENTS). */
export async function fetchSubmissionComments(
  kind: PortalLandingKind,
  submissionId: string,
): Promise<FormComment[]> {
  const res = await portalFetch(
    `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(submissionId)}/comments`,
  );
  const data = await unwrap<{ items?: Record<string, unknown>[] }>(
    res,
    'Failed to load comments.',
  );
  return (data.items ?? []).map(toFormComment);
}

/** Post the contact's comment (multipart: body + 0..5 files). */
export async function postSubmissionComment(
  kind: PortalLandingKind,
  submissionId: string,
  body: string,
  files: File[] = [],
  parentId?: string | null,
): Promise<FormCommentPostResult> {
  const res = await portalMultipartFetch(
    `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(submissionId)}/comments`,
    formCommentFormData(body, files, parentId),
  );
  return toFormCommentPostResult(
    await unwrap<Record<string, unknown>>(res, 'Failed to post the comment.'),
  );
}

function submissionCommentUrl(
  kind: PortalLandingKind,
  submissionId: string,
  commentId: string,
): string {
  return `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(submissionId)}/comments/${encodeURIComponent(commentId)}`;
}

/** Edit the contact's own comment. No notification goes out for an edit. */
export async function editSubmissionComment(
  kind: PortalLandingKind,
  submissionId: string,
  commentId: string,
  body: string,
): Promise<void> {
  const res = await portalFetch(
    submissionCommentUrl(kind, submissionId, commentId),
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body }),
    },
  );
  if (!res.ok)
    throw new Error(await extractApiError(res, 'Failed to save the comment.'));
}

/** Delete the contact's own comment (a placeholder stays if it has replies). */
export async function deleteSubmissionComment(
  kind: PortalLandingKind,
  submissionId: string,
  commentId: string,
): Promise<void> {
  const res = await portalFetch(
    submissionCommentUrl(kind, submissionId, commentId),
    { method: 'DELETE' },
  );
  if (!res.ok && res.status !== 204)
    throw new Error(
      await extractApiError(res, 'Failed to delete the comment.'),
    );
}

/**
 * Same-origin bytes route for a portal attachment, keyed on `attachment_id`
 * (NOT `link_id`): an attachment removed during a revision is unlinked but
 * stays visible in that revision's history, so a link-keyed URL would 404 on
 * exactly the historical files. Backed by the portal token, so it works on a
 * surface with no NextAuth session.
 */
export function portalAttachmentDownloadUrl(attachmentId: string): string {
  return `/api/v1/public/portal/attachments/${encodeURIComponent(attachmentId)}/download`;
}

/**
 * Read attachment bytes with the portal token. Feeds the shared
 * AttachmentPreviewModal's `fetchBytes` escape hatch (Download button + inline
 * Excel), which otherwise calls `apiFetch` and 401s here.
 *
 * Goes to the API host directly ({@link absoluteApiUrl}), NOT through the
 * same-origin path every other portal call uses: in production those land on
 * the Next.js catch-all proxy (`app/api/v1/public/[...path]/route.ts`), which
 * re-serializes every backend response as JSON and would hand back `{}`
 * instead of the file. Same reason `portalMultipartFetch` bypasses it.
 */
export async function fetchPortalAttachmentBytes(
  downloadUrl: string,
): Promise<Response> {
  const token = readPortalToken();
  const url = absoluteApiUrl(downloadUrl);
  const init: RequestInit = {
    method: 'GET',
    headers: token ? { 'X-Portal-Token': token } : undefined,
  };
  if (url.startsWith('http')) {
    init.mode = 'cors';
    init.credentials = 'omit';
  }
  const res = await fetch(url, init);
  if (res.status === 401) {
    clearPortalToken();
    throw new PortalUnauthorizedError();
  }
  return res;
}

export async function deleteAttachment(linkId: string): Promise<void> {
  const res = await portalFetch(
    `/api/v1/public/portal/attachments/${encodeURIComponent(linkId)}`,
    {
      method: 'DELETE',
    },
  );
  if (!res.ok) {
    const message = await extractApiError(res, 'Failed to remove attachment.');
    throw new Error(message);
  }
}

export async function requestOtp(
  contactId: string,
  spaceId: string,
): Promise<{ sent_to: string | null; expires_at: string }> {
  const res = await fetch('/api/v1/public/portal/request-otp', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contact_id: contactId, space_id: spaceId }),
  });
  return unwrap(res, 'Failed to send verification code.');
}

export async function verifyOtp(
  contactId: string,
  spaceId: string,
  code: string,
): Promise<{ token: string; expires_at: string }> {
  const res = await fetch('/api/v1/public/portal/verify-otp', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contact_id: contactId, space_id: spaceId, code }),
  });
  return unwrap(res, 'Failed to verify code.');
}

export interface PortalTokenInfo {
  contact_id: string;
  space_id: string;
  expires_at: string;
  expired: boolean;
  revoked: boolean;
  portal_slug?: string | null;
  name?: string | null;
  masked_phone?: string | null;
  whatsapp_number?: string | null;
}

// ---------------------------------------------------------------------------
// Bookmarkable slug links (see documentation/plans/PLAN-portal-bookmarkable-links.md)
// ---------------------------------------------------------------------------

export interface PortalSlugInfo {
  contact_id: string;
  space_id: string;
  name: string | null;
  masked_phone: string | null;
  whatsapp_number: string | null;
}

/** GET /api/v1/public/portal/slug-info/{slug} - null mirrors the 404. */
export async function fetchSlugInfo(
  slug: string,
): Promise<PortalSlugInfo | null> {
  const res = await fetch(
    `/api/v1/public/portal/slug-info/${encodeURIComponent(slug)}`,
    { method: 'GET' },
  );
  if (res.status === 404) return null;
  return unwrap<PortalSlugInfo>(res, 'Could not look up portal link.');
}

/**
 * POST /api/v1/public/portal/logout - revokes the active token server-side
 * (clearing client storage alone would leave a copied token valid).
 */
export async function portalLogout(): Promise<void> {
  const token = readPortalToken();
  if (!token) return;
  await fetch('/api/v1/public/portal/logout', {
    method: 'POST',
    headers: { 'X-Portal-Token': token },
  });
}

export async function fetchTokenInfo(token: string): Promise<PortalTokenInfo> {
  const res = await fetch(
    `/api/v1/public/portal/token-info?token=${encodeURIComponent(token)}`,
    { method: 'GET' },
  );
  return unwrap(res, 'Could not look up portal token.');
}

// Friendly labels for backend status / approval_status values surfaced in the
// portal. Falls back to the raw value when not in this map.
export const SUBMISSION_STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  new: 'New',
  pending: 'Pending',
  pending_approval: 'Pending approval',
  pending_project_sales: 'Pending project sales',
  pending_purchasing: 'Pending purchasing',
  approved: 'Approved',
  rejected: 'Rejected',
  responded: 'Responded',
  submitted: 'Submitted',
  processed_by_cs: 'Processed by CS',
  closed: 'Closed',
  completed: 'Completed',
  updated: 'Updated',
  // Price tag request statuses
  designing: 'Designing',
  proof_ready: 'Design Ready',
  changes_requested: 'Changes Requested',
  ready_for_collection: 'Ready for collection',
  collected: 'Collected',
  void: 'Void',
};

export function statusLabel(status: string | null | undefined): string {
  const s = (status ?? '').trim();
  if (!s) return '';
  return SUBMISSION_STATUS_LABELS[s] ?? s;
}

// ---------------------------------------------------------------------------
// Lookup helpers for portal forms (searchable comboboxes / select sets)
// ---------------------------------------------------------------------------

export interface ProductLookupItem {
  /** Additive (plan section 16, S2-16): the sales opportunity form's own line FK. */
  product_id?: string | null;
  product_code: string;
  product_name: string | null;
  category_id: string | null;
  category_code?: string | null;
  category_name?: string | null;
}

export interface DebtorLookupItem {
  customer_id: string;
  debtor_name: string;
  customer_code: string;
  // The server's mapping of debtor_type to the PR Sales type; null = leave Sales type alone.
  sales_type: string | null;
}

export interface DOProductLine {
  product_code: string;
  product_name?: string | null;
  quantity?: number | null;
}

export interface DOLookupItem {
  order_number: string;
  debtor_name: string | null;
  customer_name: string | null;
  products: string[];
  product_lines?: DOProductLine[];
  order_date?: string | null;
}

export interface DOLookupFilters {
  start_date?: string;
  end_date?: string;
  product_code?: string;
  debtor_name?: string;
}

export interface LookupSetOption {
  value: string;
  label: string;
}

export async function lookupProducts(
  q: string,
  limit = 20,
): Promise<ProductLookupItem[]> {
  const url = `/api/v1/public/portal/lookups/products?q=${encodeURIComponent(q)}&limit=${limit}`;
  const res = await portalFetch(url);
  return unwrap<ProductLookupItem[]>(res, 'Failed to load products.');
}

export async function lookupDebtors(
  q: string,
  limit = 20,
): Promise<DebtorLookupItem[]> {
  const url = `/api/v1/public/portal/lookups/debtors?q=${encodeURIComponent(q)}&limit=${limit}`;
  const res = await portalFetch(url);
  return unwrap<DebtorLookupItem[]>(res, 'Failed to load debtors.');
}

export interface ProjectLookupItem {
  id: string;
  project_code: string;
  title: string;
  company_name?: string | null;
}

/**
 * Projects this contact may attach a sponsorship to (AC-F4a).
 *
 * The backend scopes the list to the companies the contact is linked to, so an empty
 * result means "your company is not linked yet", not "no projects exist".
 */
export async function lookupProjects(
  q: string,
  limit = 20,
): Promise<ProjectLookupItem[]> {
  const url = `/api/v1/public/portal/lookups/projects?q=${encodeURIComponent(q)}&limit=${limit}`;
  const res = await portalFetch(url);
  return unwrap<ProjectLookupItem[]>(res, 'Failed to load projects.');
}

export async function lookupDeliveryOrders(
  q: string,
  limit = 20,
  filters: DOLookupFilters = {},
): Promise<DOLookupItem[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  if (filters.start_date) params.set('start_date', filters.start_date);
  if (filters.end_date) params.set('end_date', filters.end_date);
  if (filters.product_code) params.set('product_code', filters.product_code);
  if (filters.debtor_name) params.set('debtor_name', filters.debtor_name);
  const url = `/api/v1/public/portal/lookups/delivery-orders?${params.toString()}`;
  const res = await portalFetch(url);
  return unwrap<DOLookupItem[]>(res, 'Failed to load delivery orders.');
}

export interface LookupSetResult {
  options: LookupSetOption[];
  defaultValue: string | null;
}

// ---------------------------------------------------------------------------
// Requestor picker ("Requested by" / "Salesperson") - segment-gated contacts
// ---------------------------------------------------------------------------

export interface RequestorOption {
  id: string;
  name: string;
}

export interface RequestorOptionsResult {
  items: RequestorOption[];
  hasMore: boolean;
}

/** GET /api/v1/public/portal/requestor-options?q= - names only, segment-gated
 *  (plus the submitting contact, always included server-side). */
export async function fetchRequestorOptions(
  q?: string,
): Promise<RequestorOptionsResult> {
  const usp = new URLSearchParams();
  if (q && q.trim()) usp.set('q', q.trim());
  const qs = usp.toString();
  const res = await portalFetch(
    `/api/v1/public/portal/requestor-options${qs ? `?${qs}` : ''}`,
  );
  const data = await unwrap<{ items?: RequestorOption[]; has_more?: boolean }>(
    res,
    'Failed to load requestor options.',
  );
  return { items: data.items ?? [], hasMore: Boolean(data.has_more) };
}

// ---------------------------------------------------------------------------
// Record navigation (previous/next submission of the same kind)
// ---------------------------------------------------------------------------

export interface PortalSubmissionNeighbours {
  prev_id: string | null;
  next_id: string | null;
  position: number;
  total: number;
}

/** GET .../submissions/{kind}/{id}/neighbours - token-scoped to the contact's
 *  own submissions of the same kind, newest first. `PortalLandingKind`
 *  (not `PortalSubmissionKind`): review round 3 widens the route the same
 *  way the revision routes already were for `price_tag_request`. */
export async function fetchSubmissionNeighbours(
  kind: PortalLandingKind,
  id: string,
): Promise<PortalSubmissionNeighbours> {
  const res = await portalFetch(
    `/api/v1/public/portal/submissions/${kind}/${encodeURIComponent(id)}/neighbours`,
  );
  return unwrap<PortalSubmissionNeighbours>(res, 'Failed to load navigation.');
}

const _lookupSetCache: Record<string, LookupSetResult> = {};
export async function lookupSet(setKey: string): Promise<LookupSetResult> {
  if (_lookupSetCache[setKey]) return _lookupSetCache[setKey];
  const url = `/api/v1/public/portal/lookups/sets/${encodeURIComponent(setKey)}`;
  const res = await portalFetch(url);
  const data = await unwrap<{
    options: LookupSetOption[];
    default_value: string | null;
  }>(res, 'Failed to load lookup options.');
  const result: LookupSetResult = {
    options: data?.options ?? [],
    defaultValue: data?.default_value ?? null,
  };
  _lookupSetCache[setKey] = result;
  return result;
}

// ---------------------------------------------------------------------------
// AI Extract - generic form prefill from attachments (images + PDFs)
// ---------------------------------------------------------------------------

export interface AIExtractFieldMeta {
  raw?: unknown;
  canonical?: unknown;
  source?: string | null;
}

export interface AIExtractedProductLine {
  product_code?: string | null;
  product_name?: string | null;
  quantity?: number | null;
  unit_price?: number | null;
  total?: number | null;
  notes?: string | null;
  // D2 (PLAN-price-tag-ai-extract-resolver.md): already resolved through the
  // entity resolver server-side - exactly one match, or none/ambiguous. When
  // present, the form reads these instead of looking the code up itself.
  match?: 'product' | 'product_set' | null;
  product_id?: string | null;
  product_set_id?: string | null;
  // AI-EXTRACT-DYM: on a row with no match only - the resolver's "did you
  // mean" candidates, best first. Never applied until the user picks one.
  suggestions?: AIExtractedProductSuggestion[];
}

export interface AIExtractedProductSuggestion {
  product_id: string;
  product_code: string;
  product_name?: string | null;
}

export interface AIExtractTokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface AIExtractResult {
  values: Record<string, unknown>;
  products: AIExtractedProductLine[];
  per_field: Record<string, AIExtractFieldMeta>;
  usage: AIExtractTokenUsage;
  model?: string | null;
  provider?: string | null;
}

export const AI_EXTRACT_FORM_KEYS: Record<PortalLandingKind, string> = {
  complaint: 'portal.complaint',
  stock_inquiry: 'portal.stock_inquiry',
  purchase_request: 'portal.purchase_request',
  sponsorship_form: 'portal.sponsorship_form',
  // D7: registered in the backend's FORM_SCHEMAS with the same `portal.`
  // prefix as every other portal form key (Phase 1 briefly had this as a
  // bare `price_tag_request` - wrong; fixed to match convention).
  price_tag_request: 'portal.price_tag_request',
  // Never read: the sample request form offers no AI extract.
  sample_request: 'portal.sample_request',
  // Never read: the application form offers no AI extract.
  new_product_application: 'portal.new_product_application',
  // Never read: the opportunity form offers no AI extract. Present because the map is keyed
  // by every landing kind.
  sales_opportunity: 'portal.sales_opportunity',
  // Never read either: Customer asks has no form at all.
  customer_asks: 'portal.customer_asks',
  // Nor Conversation: read-only threads, no form.
  conversation: 'portal.conversation',
};

export async function aiExtractFromFiles(
  formKey: string,
  files: File[],
): Promise<AIExtractResult> {
  if (!files.length)
    throw new Error('Drop at least one file before extracting.');
  const form = new FormData();
  form.set('form_key', formKey);
  for (const file of files) {
    form.append('files', file, file.name);
  }
  const res = await portalMultipartFetch(
    '/api/v1/public/portal/ai-extract',
    form,
  );
  return unwrap<AIExtractResult>(res, 'AI extract failed.');
}

// ---------------------------------------------------------------------------
// Price tag request design preview (D11/S4)
// ---------------------------------------------------------------------------

/**
 * The tag sheet doc plus its resolved line data, in one answer - the CRM's
 * equivalent design route (`GET .../price-tag-requests/{id}/design`) returns
 * only the doc and resolves prices through a second, staff-only route; the
 * portal has no such second call, so this one carries both.
 */
export type PriceTagDesignResponse = TagSheetDesignResponse;

/**
 * The salesperson's real design preview (D11), status-gated server-side to
 * `proof_ready | changes_requested | approved | ready` (404 otherwise, no
 * doc leak while a request is still being designed). Returns `null` on 404
 * so the caller can show the section's own empty state rather than throw.
 *
 * ## Expected response (r9 S1/D1 - the print payload, minus its
 * download-scoped fields). Full contract: `lib/dealer-kit/design-payload.ts`.
 *
 * ```
 * GET /api/v1/public/portal/submissions/price_tag_request/{id}/design
 *   200 { page_id, version, source: "version", doc, lines[],
 *         assets:  { [assetId]: signedUrl },
 *         images:  { [attachmentId]: signedUrl },
 *         fonts:   [{ name, family, url }] }
 *   404 no design yet, or a status that does not expose one.
 * ```
 *
 * The three media maps are what turn an image layer from a grey box into the
 * artwork it prints as. Until the route carries them,
 * `designPayloadFromResponse` rebuilds `images` off the lines (which already
 * carry their photos' signed URLs) and leaves `assets` / `fonts` empty - the
 * mock boundary for Phase 1 sits here, at the service, and nothing above it
 * changes when the backend catches up.
 */
export async function getPriceTagDesign(
  id: string,
): Promise<TagSheetDesignPayload | null> {
  const res = await portalFetch(
    `/api/v1/public/portal/submissions/price_tag_request/${encodeURIComponent(id)}/design`,
  );
  if (res.status === 404) return null;
  const body = await unwrap<TagSheetDesignResponse>(
    res,
    'Failed to load the design.',
  );
  return designPayloadFromResponse(body);
}

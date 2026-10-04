/**
 * Shared API client utilities: error extraction, DataGrid params, and request helpers.
 * See docs/ADR-PRODUCT-STANDARDS.md for usage guidelines.
 */

import type { SortingState } from '@tanstack/react-table';

export type DataGridParamsInput = {
  pageIndex: number;
  pageSize: number;
  sorting?: SortingState;
  searchQuery?: string;
};

/** Nginx and other proxies often return HTML for 502/503; never show that in toasts. */
function responseBodyLooksLikeHtml(text: string): boolean {
  const t = text.trim().toLowerCase();
  return (
    t.startsWith('<!doctype') ||
    t.startsWith('<html') ||
    (t.includes('<title>') && (t.includes('502') || t.includes('503') || t.includes('504') || t.includes('bad gateway')))
  );
}

/**
 * Extract user-facing error message from API error response.
 * Handles FastAPI detail (string | array) and common message shapes.
 */
export async function extractApiError(
  response: Response,
  fallbackMessage = 'An error occurred'
): Promise<string> {
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    const text = await response.text().catch(() => '');
    if (text && responseBodyLooksLikeHtml(text)) {
      if (response.status === 502) {
        return 'Bad gateway (502). The API server is not responding - check that the backend is running and nginx proxy settings.';
      }
      if (response.status === 503) {
        return 'Service unavailable (503). The API may be starting or overloaded.';
      }
      if (response.status === 504) {
        return 'Gateway timeout (504). The API took too long to respond.';
      }
      return `Server error (${response.status}). The proxy returned an HTML error page; check API logs.`;
    }
    if (text) return text.slice(0, 300);
    if (response.status === 401) return 'Not signed in or session expired. Please sign in again.';
    if (response.status === 413) return 'File too large. Try a smaller file or ask your admin to increase upload limits.';
    if (response.status >= 500) return 'Server error. Try again or contact support.';
    return fallbackMessage;
  }
  // This IS `extractApiError`. The rule below exists to send callers here rather than let
  // them each parse an error body their own way; the one place that has to do the parsing
  // is this line.
  // eslint-disable-next-line no-restricted-syntax
  const error = await response.json().catch(() => ({}));
  const detail = error.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (typeof first === 'string') return first;
    const msg = first?.msg ?? first?.message;
    if (msg) {
      // A pydantic error's `msg` never names the field on its own ("Field required"
      // could be any one of thirty) - `loc` does (`["body", "shipment_date"]`), so a
      // 422 toasts "shipment_date: Field required" rather than an anonymous complaint.
      const loc: unknown[] = Array.isArray(first?.loc) ? first.loc : [];
      const field = loc
        .filter((seg) => typeof seg === 'string' && !['body', 'query', 'path', 'header'].includes(seg))
        .pop();
      return typeof field === 'string' ? `${field}: ${msg}` : String(msg);
    }
    return JSON.stringify(first);
  }
  if (detail && typeof detail === 'object' && detail.message) return String(detail.message);
  if (error.message) return String(error.message);
  if (response.status === 401) return 'Not signed in or session expired. Please sign in again.';
  if (response.status >= 500) return 'Server error. Try again or contact support.';
  return fallbackMessage;
}

/** An API refusal the caller has to BRANCH on, not just show. */
export interface CodedError extends Error {
  /** `AppException`'s own `code` - e.g. `over_capacity`, `no_recipients`. Null when the body
   *  carries none. */
  code: string | null;
}

/**
 * The same message `extractApiError` produces, carrying the backend's machine-readable
 * `code` alongside it.
 *
 * Used only where the caller must tell one refusal from another (an over-capacity convert
 * asks a question; a send refused for want of a WeChat channel says something different from
 * one refused for want of an address). The response is cloned so the shared extractor still
 * gets an unread body - this reads the code, it does not re-implement the message.
 */
export async function codedError(response: Response, fallback: string): Promise<CodedError> {
  const clone = response.clone();
  const message = await extractApiError(response, fallback);
  const error = new Error(message) as CodedError;
  error.code = null;
  try {
    const body = (await clone.json()) as { code?: unknown };
    if (typeof body?.code === 'string') error.code = body.code;
  } catch {
    // A refusal with no JSON body carries no code. The message above still stands.
  }
  return error;
}

/** An API failure carrying its HTTP status, so a consumer can tell a refusal or a missing
 *  record from a fault (NEVER-STUCK-UI S3). */
export interface ApiError extends Error {
  status: number;
}

/** The same message `extractApiError` produces, carrying the response status alongside it. */
export async function apiError(response: Response, fallback: string): Promise<ApiError> {
  const error = new Error(await extractApiError(response, fallback)) as ApiError;
  error.status = response.status;
  return error;
}

function statusOf(error: unknown): number | undefined {
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === 'number' ? status : undefined;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '';
}

/** What `apiFetch` throws when the server has not answered within the request's budget. */
export const REQUEST_TIMED_OUT_MESSAGE = 'The server took too long to answer.';

/** The backend's 403 `detail` prefixes (`dependencies.py`, `modules/runtime/guards.py`,
 *  `scm/import_mapping.py`), for errors built by plain `extractApiError`, which carry no
 *  status. */
const ACCESS_DENIED_PREFIXES = [
  'Permission required:',
  'One of these permissions required', // covers the "(module may be disabled)" variant
  'Module not enabled', // `Module not enabled: x` and `Module not enabled for <doc type>`
  'One of these modules must be enabled:',
];

/** `get_current_user` 401 details (`dependencies.py`, `user_session_service.py`) and the
 *  message `extractApiError` gives a bodiless 401. */
const SIGNED_OUT_MESSAGES = [
  'Not signed in or session expired',
  'Authentication required',
  'Session expired',
  'Session was revoked',
  'Session not found',
  'Account is not active',
  'Invalid token',
];

/** The backend refused this read: render `AccessDenied`, never an error card or "not found". */
export function isAccessDenied(error: unknown): boolean {
  if (statusOf(error) === 403) return true;
  const msg = errorMessage(error);
  return ACCESS_DENIED_PREFIXES.some((p) => msg.startsWith(p));
}

/** A 401 from the session check. */
export function isSignedOut(error: unknown): boolean {
  if (statusOf(error) === 401) return true;
  const msg = errorMessage(error);
  return SIGNED_OUT_MESSAGES.some((p) => msg.startsWith(p));
}

/** The record does not exist. Only a 404 says so; a refusal or a 500 does not. */
export function isNotFound(error: unknown): boolean {
  return statusOf(error) === 404;
}

/** The request outlived its `apiFetch` budget. */
export function isTimedOut(error: unknown): boolean {
  return errorMessage(error) === REQUEST_TIMED_OUT_MESSAGE;
}

/** React Query `retry` for a record read: one retry for a fault, none for an answer
 *  that will not change (a refusal or a missing record). */
export function retryUnlessRefused(failureCount: number, error: unknown): boolean {
  return failureCount < 1 && !isAccessDenied(error) && !isNotFound(error);
}

/**
 * A failure that asking again will not fix (401 / 403 / 404), or one that already cost the
 * user a full timeout. Never retried automatically: the screen shows its final state and
 * the user's own Retry is the next step. A status-less error that reads "<thing> not found"
 * counts as a 404 here (retrying it is pointless) even though `isNotFound`, which decides
 * what a detail page SAYS, only trusts a real status.
 */
export function isRefused(error: unknown): boolean {
  return (
    isAccessDenied(error) ||
    isSignedOut(error) ||
    isNotFound(error) ||
    /\bnot found\b/i.test(errorMessage(error)) ||
    isTimedOut(error)
  );
}

/**
 * Build URLSearchParams for DataGrid-backed list endpoints.
 * Uses page (1-based), limit, sort, dir, query, plus any extra params.
 */
export function buildDataGridParams(
  params: DataGridParamsInput,
  extra?: Record<string, string | number | boolean | undefined | null>
): URLSearchParams {
  const { pageIndex, pageSize, sorting, searchQuery } = params;
  const sortField = sorting?.[0]?.id || '';
  const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';
  const sp = new URLSearchParams({
    page: String(pageIndex + 1),
    limit: String(pageSize),
    ...(sortField ? { sort: sortField, dir: sortDirection } : {}),
    ...(searchQuery ? { query: searchQuery } : {}),
  });
  if (extra) {
    for (const [k, v] of Object.entries(extra)) {
      if (v !== undefined && v !== null && v !== '') {
        sp.set(k, String(v));
      }
    }
  }
  return sp;
}

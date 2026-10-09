/**
 * Form submission comment thread, staff side.
 *
 *   listFormComments  GET  /api/v1/form-comments/{kind}/{id}          {items}
 *   addFormComment    POST /api/v1/form-comments/{kind}/{id}  {body}  {comment, delivery}
 *
 * The contact portal has its own pair in `app/(auth)/portal/lib/portal-client.ts`; both map the
 * same snake_case row through `toFormComment`.
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type {
  FormComment,
  FormCommentKind,
  FormCommentPostResult,
} from '@/types/formComments';

const BASE = '/api/v1/form-comments';

type Raw = Record<string, unknown>;

export function toFormComment(raw: Raw): FormComment {
  return {
    id: String(raw.id),
    body: String(raw.body ?? ''),
    authorName: String(raw.author_name ?? ''),
    authorKind: raw.author_kind === 'submitter' ? 'submitter' : 'staff',
    createdAt: String(raw.created_at ?? ''),
  };
}

export function toFormCommentPostResult(raw: Raw): FormCommentPostResult {
  return {
    comment: toFormComment((raw.comment ?? {}) as Raw),
    delivery: (raw.delivery as FormCommentPostResult['delivery']) ?? 'queued',
  };
}

export async function listFormComments(
  kind: FormCommentKind,
  id: string,
): Promise<FormComment[]> {
  const res = await apiFetch(`${BASE}/${kind}/${encodeURIComponent(id)}`);
  if (!res.ok)
    throw new Error(await extractApiError(res, 'Failed to load comments'));
  const data = (await res.json()) as { items?: Raw[] };
  return (data.items ?? []).map(toFormComment);
}

export async function addFormComment(
  kind: FormCommentKind,
  id: string,
  body: string,
): Promise<FormCommentPostResult> {
  const res = await apiFetch(`${BASE}/${kind}/${encodeURIComponent(id)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  });
  if (!res.ok)
    throw new Error(await extractApiError(res, 'Failed to post the comment'));
  return toFormCommentPostResult((await res.json()) as Raw);
}

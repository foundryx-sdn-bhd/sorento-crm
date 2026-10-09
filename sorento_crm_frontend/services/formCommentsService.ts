/**
 * Form submission comment thread, staff side.
 *
 *   listFormComments  GET  /api/v1/form-comments/{kind}/{id}          {items}
 *   addFormComment    POST /api/v1/form-comments/{kind}/{id}  multipart body + parent_id? + files  {comment, delivery}
 *   editFormComment   PATCH /api/v1/form-comments/{kind}/{id}/comments/{cid}  {body}
 *   (delete is the server-deferred pending action `form_comment.delete`, via useDeferredAction)
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
    kind: raw.kind === 'response' ? 'response' : 'comment',
    parentId: (raw.parent_id as string | null | undefined) ?? null,
    authorKind: raw.author_kind === 'submitter' ? 'submitter' : 'staff',
    postedByName: (raw.posted_by_name as string | null | undefined) || null,
    createdAt: raw.created_at ? String(raw.created_at) : null,
    editedAt: (raw.edited_at as string | null | undefined) ?? null,
    deleted: Boolean(raw.deleted),
    canEdit: Boolean(raw.can_edit),
    canDelete: Boolean(raw.can_delete),
    canReply: Boolean(raw.can_reply),
    attachments: ((raw.attachments as Raw[] | undefined) ?? []).map((a) => ({
      attachmentId: String(a.attachment_id),
      filename: String(a.filename ?? 'Attachment'),
      size: typeof a.size === 'number' ? a.size : null,
      contentType: (a.content_type as string | null | undefined) ?? null,
    })),
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

/** Multipart: `body`, optional `parent_id` (a reply) and 0..5 `files`. The portal posts the same shape. */
export function formCommentFormData(
  body: string,
  files: File[],
  parentId?: string | null,
): FormData {
  const form = new FormData();
  form.append('body', body);
  if (parentId) form.append('parent_id', parentId);
  for (const file of files) form.append('files', file);
  return form;
}

export async function addFormComment(
  kind: FormCommentKind,
  id: string,
  body: string,
  files: File[] = [],
  parentId?: string | null,
): Promise<FormCommentPostResult> {
  const res = await apiFetch(`${BASE}/${kind}/${encodeURIComponent(id)}`, {
    method: 'POST',
    body: formCommentFormData(body, files, parentId),
  });
  if (!res.ok)
    throw new Error(await extractApiError(res, 'Failed to post the comment'));
  return toFormCommentPostResult((await res.json()) as Raw);
}

/** Edit the signed-in user's own comment (or any, for an admin). Never notifies anyone. */
export async function editFormComment(
  kind: FormCommentKind,
  id: string,
  commentId: string,
  body: string,
): Promise<void> {
  const res = await apiFetch(
    `${BASE}/${kind}/${encodeURIComponent(id)}/comments/${encodeURIComponent(commentId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body }),
    },
  );
  if (!res.ok)
    throw new Error(await extractApiError(res, 'Failed to save the comment'));
}

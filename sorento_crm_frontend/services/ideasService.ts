/**
 * Ideas service: the CRM-native Ideas pages' only door to the backend.
 *
 * Layering: components -> hooks (`useIdeas`) -> THIS service -> lib/api -> the gateway
 * `/api/v1/ideation/*`, which calls the ss embed API as the signed-in user (PLAN-ideation-in-crm
 * section 10). ss's camelCase shapes pass through unchanged; the few mappings are noted below.
 *
 *   listIdeas         GET    /ideation/ideas?filter=<archived|all>&search=&mine=true   view
 *   getBoard          GET    /ideation/ideas/board                                    view
 *   getIdea           GET    /ideation/ideas/{id}                                     view
 *   getMergedChildren GET    /ideation/ideas/{id}/merged                              view
 *   createIdea        POST   /ideation/ideas, then POST .../{id}/attachments per file view
 *   updateIdea        PATCH  /ideation/ideas/{id}                                     manage
 *   voteIdea          POST   /ideation/ideas/{id}/vote  {dir: 'up'}                   view
 *   moveIdeaToStatus  POST   /ideation/ideas/{id}/status  {toStatusId}                manage
 *   restoreIdea       POST   /ideation/ideas/{id}/status  {toStatusId}                manage
 *                     (one of the idea's own outgoing transitions; no status key is hardcoded)
 *   reorderIdeas      PUT    /ideation/ideas/reorder  {orderedIds}                    manage
 *   mergeIdeas        POST   /ideation/ideas/merge  {survivorId, ideaIds}             manage
 *   unmergeIdea       POST   /ideation/ideas/{id}/unmerge                             manage
 *   promoteIdea(s)    POST   /ideation/ideas/promote  {ideaIds: [..], title}          manage
 *   uploadAttachment  POST   /ideation/ideas/{id}/attachments (multipart)             view
 *   fetchAttachment   GET    /ideation/ideas/{id}/attachments/{aid}/content           view
 *   listComments      GET    /ideation/ideas/{id}/comments    (ss `isDeleted` -> `deleted`)
 *   addComment        POST   /ideation/ideas/{id}/comments  {body, parentId?}
 *   editComment       PATCH  /ideation/ideas/{id}/comments/{cid}  {body}
 *
 * Archive, Delete and comment Delete are NOT here: they are server-deferred pending actions
 * (`idea.archive`, `idea.delete`, `idea_comment.delete`) started through `useDeferredAction`.
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type {
  Idea,
  IdeaAttachment,
  IdeaBoard,
  IdeaComment,
  IdeaCreateInput,
  IdeaListParams,
  IdeaMergeInput,
  IdeaPromoteInput,
  IdeaUpdateInput,
} from '@/types/ideas';

const BASE = '/api/v1/ideation';

/** Beyond the default (active ideas), which is "no filter". */
export const IDEA_STATUS_FILTER_OPTIONS = [
  { value: 'archived', label: 'Archived' },
  { value: 'all', label: 'All' },
];

type Raw = Record<string, unknown>;

/** A gateway refusal: the server's message, plus the HTTP status for callers that branch on 404. */
export class IdeasApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'IdeasApiError';
    this.status = status;
  }
}

async function read<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) throw new IdeasApiError(await extractApiError(response, fallback), response.status);
  return response.json();
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  };
}

function toAttachment(raw: Raw): IdeaAttachment {
  return {
    id: String(raw.id),
    kind: (raw.kind as IdeaAttachment['kind']) ?? 'file',
    name: String(raw.name ?? ''),
    sizeBytes: (raw.sizeBytes as number | null | undefined) ?? null,
    durationSec: (raw.durationSec as number | null | undefined) ?? null,
    hasContent: !!raw.contentPath,
    url: typeof raw.url === 'string' ? raw.url : '',
  };
}

function toIdea(raw: Raw): Idea {
  return {
    ...(raw as unknown as Idea),
    transitions: (raw.transitions as Idea['transitions'] | undefined) ?? [],
    attachments: ((raw.attachments as Raw[] | undefined) ?? []).map(toAttachment),
  };
}

function toComment(raw: Raw): IdeaComment {
  const deleted = !!raw.isDeleted;
  return {
    id: String(raw.id),
    parentId: (raw.parentId as string | null | undefined) ?? null,
    authorName: String(raw.authorName ?? ''),
    body: deleted ? '' : String(raw.body ?? ''),
    createdAt: String(raw.createdAt),
    editedAt: (raw.editedAt as string | null | undefined) ?? null,
    deleted,
    canEdit: !!raw.canEdit,
    canDelete: !!raw.canDelete,
  };
}

export async function listIdeas(params: IdeaListParams): Promise<Idea[]> {
  const search = new URLSearchParams();
  if (params.status) search.set('filter', params.status);
  if (params.mine === true) search.set('mine', 'true');
  if (params.query?.trim()) search.set('search', params.query.trim());
  const qs = search.toString();
  const rows = await read<Raw[]>(await apiFetch(`${BASE}/ideas${qs ? `?${qs}` : ''}`), 'Failed to load ideas');
  return rows.map(toIdea);
}

export async function getIdea(id: string): Promise<Idea> {
  return toIdea(await read<Raw>(await apiFetch(`${BASE}/ideas/${id}`), 'Failed to load the idea'));
}

export async function getBoard(): Promise<IdeaBoard> {
  const board = await read<{ columns: Array<Raw & { ideas?: Raw[] }> }>(
    await apiFetch(`${BASE}/ideas/board`),
    'Failed to load the board',
  );
  return {
    columns: (board.columns ?? []).map((c) => ({
      ...(c as unknown as IdeaBoard['columns'][number]),
      ideas: (c.ideas ?? []).map(toIdea),
    })),
  };
}

export async function getMergedChildren(id: string): Promise<Idea[]> {
  const rows = await read<Raw[]>(await apiFetch(`${BASE}/ideas/${id}/merged`), 'Failed to load merged ideas');
  return rows.map(toIdea);
}

export async function uploadAttachment(id: string, file: File): Promise<IdeaAttachment> {
  const form = new FormData();
  form.append('file', file);
  const raw = await read<Raw>(
    await apiFetch(`${BASE}/ideas/${id}/attachments`, { method: 'POST', body: form }),
    'Failed to upload the file',
  );
  return toAttachment(raw);
}

/** The bytes of an uploaded attachment, streamed through the gateway. */
export async function fetchAttachment(ideaId: string, attachmentId: string): Promise<Blob> {
  const response = await apiFetch(`${BASE}/ideas/${ideaId}/attachments/${attachmentId}/content`);
  if (!response.ok) throw new IdeasApiError(await extractApiError(response, 'Failed to download the file'), response.status);
  return response.blob();
}

export async function createIdea(input: IdeaCreateInput): Promise<Idea> {
  const { files, ...fields } = input;
  const body: Record<string, string> = {};
  for (const [key, value] of Object.entries(fields)) {
    if (typeof value === 'string' && value.trim()) body[key] = value.trim();
  }
  const created = toIdea(
    await read<Raw>(await apiFetch(`${BASE}/ideas`, jsonInit('POST', body)), 'Failed to capture the idea'),
  );
  const failed: string[] = [];
  for (const file of files ?? []) {
    try {
      await uploadAttachment(created.id, file);
    } catch {
      failed.push(file.name);
    }
  }
  if (failed.length > 0) {
    throw new Error(`The idea was captured, but these files could not be attached: ${failed.join(', ')}`);
  }
  return created;
}

export async function updateIdea(id: string, input: IdeaUpdateInput): Promise<Idea> {
  return toIdea(
    await read<Raw>(await apiFetch(`${BASE}/ideas/${id}`, jsonInit('PATCH', input)), 'Failed to save the idea'),
  );
}

export async function voteIdea(id: string): Promise<Idea> {
  return toIdea(
    await read<Raw>(await apiFetch(`${BASE}/ideas/${id}/vote`, jsonInit('POST', { dir: 'up' })), 'Failed to vote'),
  );
}

export async function moveIdeaToStatus(id: string, toStatusId: string): Promise<Idea> {
  return toIdea(
    await read<Raw>(
      await apiFetch(`${BASE}/ideas/${id}/status`, jsonInit('POST', { toStatusId })),
      'Failed to move the idea',
    ),
  );
}

export async function restoreIdea(id: string, toStatusId: string): Promise<Idea> {
  return toIdea(
    await read<Raw>(
      await apiFetch(`${BASE}/ideas/${id}/status`, jsonInit('POST', { toStatusId })),
      'Failed to restore the idea',
    ),
  );
}

export async function reorderIdeas(orderedIds: string[]): Promise<void> {
  await read(await apiFetch(`${BASE}/ideas/reorder`, jsonInit('PUT', { orderedIds })), 'Failed to reorder ideas');
}

export async function mergeIdeas(input: IdeaMergeInput): Promise<Idea> {
  return toIdea(
    await read<Raw>(await apiFetch(`${BASE}/ideas/merge`, jsonInit('POST', input)), 'Failed to merge the ideas'),
  );
}

export async function unmergeIdea(id: string): Promise<void> {
  await read(await apiFetch(`${BASE}/ideas/${id}/unmerge`, jsonInit('POST')), 'Failed to unmerge the idea');
}

/** One BR from one or more ideas (ss takes the list; the first idea's title is the caller's call). */
export async function promoteIdeas(input: {
  ideaIds: string[];
  title: string;
}): Promise<{ id: string; title?: string | null }> {
  return read(
    await apiFetch(
      `${BASE}/ideas/promote`,
      jsonInit('POST', { ideaIds: input.ideaIds, title: input.title }),
    ),
    'Failed to promote the ideas',
  );
}

export async function promoteIdea(
  id: string,
  input: IdeaPromoteInput,
): Promise<{ id: string; title?: string | null }> {
  return promoteIdeas({ ideaIds: [id], title: input.title });
}

export async function listComments(ideaId: string): Promise<IdeaComment[]> {
  const rows = await read<Raw[]>(await apiFetch(`${BASE}/ideas/${ideaId}/comments`), 'Failed to load comments');
  return rows.map(toComment);
}

export async function addComment(
  ideaId: string,
  input: { body: string; parentId?: string | null },
): Promise<IdeaComment> {
  const body: Record<string, string> = { body: input.body };
  if (input.parentId) body.parentId = input.parentId;
  return toComment(
    await read<Raw>(
      await apiFetch(`${BASE}/ideas/${ideaId}/comments`, jsonInit('POST', body)),
      'Failed to post the comment',
    ),
  );
}

export async function editComment(ideaId: string, commentId: string, body: string): Promise<IdeaComment> {
  return toComment(
    await read<Raw>(
      await apiFetch(`${BASE}/ideas/${ideaId}/comments/${commentId}`, jsonInit('PATCH', { body })),
      'Failed to save the comment',
    ),
  );
}

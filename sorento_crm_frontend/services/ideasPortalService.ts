/**
 * Ideas portal service: the Roadmap screens' only door to the backend.
 *
 * Layering: components -> hooks (`useIdeasPortal`) -> THIS service -> lib/api -> the gateway
 * `/api/v1/ideation/portal/*`, which calls the ss portal API as the signed-in user
 * (PLAN-idea-crm-side-13oct section 2).
 *
 *   getRoadmap          GET  /ideation/portal/roadmap?mine=                         view
 *   listFeedback        GET  /ideation/portal/ideas?q=&sort=&mine=&page=&pageSize=  view
 *   getChangelog        GET  /ideation/portal/changelog                             view
 *   getPortalIdea       GET  /ideation/portal/ideas/{id}                            view
 *   getVoters           GET  /ideation/portal/ideas/{id}/voters                     view
 *   getActivity         GET  /ideation/portal/ideas/{id}/activity                   view
 *   listPortalComments  GET  /ideation/portal/ideas/{id}/comments  (`isDeleted` -> `deleted`)
 *   togglePortalVote    POST /ideation/portal/ideas/{id}/vote                       view
 *   postPortalComment   POST /ideation/portal/ideas/{id}/comments  {body, parentId?}
 *   likePortalComment   POST /ideation/portal/ideas/{id}/comments/{cid}/like
 *   setPortalHidden     PUT  /ideation/portal/ideas/{id}/hidden  {hidden}           manage
 *   setPortalNote       PUT  /ideation/portal/ideas/{id}/note  {note}               manage
 *
 * Comment Delete is NOT here: it is the server-deferred `idea_comment.delete` pending action.
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import { normalizeUseCases } from '@/lib/ideaUseCases';
import { IdeasApiError } from '@/services/ideasService';
import type {
  PortalActivityRow,
  PortalChangelog,
  PortalComment,
  PortalFeedbackPage,
  PortalFeedbackParams,
  PortalIdeaDetail,
  PortalLikeResult,
  PortalRoadmap,
  PortalVoteResult,
  PortalVoter,
} from '@/types/ideasPortal';

const BASE = '/api/v1/ideation/portal';

type Raw = Record<string, unknown>;

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

function toComment(raw: Raw): PortalComment {
  const deleted = !!raw.isDeleted || !!raw.deleted;
  return {
    id: String(raw.id),
    parentId: (raw.parentId as string | null | undefined) ?? null,
    authorName: String(raw.authorName ?? ''),
    authorRole: (raw.authorRole as string | null | undefined) ?? null,
    body: deleted ? '' : String(raw.body ?? ''),
    createdAt: String(raw.createdAt),
    editedAt: (raw.editedAt as string | null | undefined) ?? null,
    deleted,
    canEdit: !!raw.canEdit,
    canDelete: !!raw.canDelete,
    likeCount: Number(raw.likeCount ?? 0),
    likedByMe: !!raw.likedByMe,
  };
}

export async function getRoadmap(params: { mine?: boolean } = {}): Promise<PortalRoadmap> {
  const qs = params.mine === true ? '?mine=true' : '';
  return read(await apiFetch(`${BASE}/roadmap${qs}`), 'Failed to load the roadmap');
}

export async function listFeedback(params: PortalFeedbackParams): Promise<PortalFeedbackPage> {
  const search = new URLSearchParams();
  if (params.q?.trim()) search.set('q', params.q.trim());
  if (params.sort) search.set('sort', params.sort);
  if (params.mine === true) search.set('mine', 'true');
  if (params.page !== undefined) search.set('page', String(params.page));
  if (params.pageSize !== undefined) search.set('pageSize', String(params.pageSize));
  const qs = search.toString();
  return read(await apiFetch(`${BASE}/ideas${qs ? `?${qs}` : ''}`), 'Failed to load feedback');
}

export async function getChangelog(): Promise<PortalChangelog> {
  return read(await apiFetch(`${BASE}/changelog`), 'Failed to load the changelog');
}

export async function getPortalIdea(id: string): Promise<PortalIdeaDetail> {
  const raw = await read<Raw>(await apiFetch(`${BASE}/ideas/${id}`), 'Failed to load the idea');
  return {
    ...(raw as unknown as PortalIdeaDetail),
    useCases: normalizeUseCases(raw.useCases),
    timeline: (raw.timeline as PortalIdeaDetail['timeline'] | undefined) ?? [],
  };
}

export async function getVoters(id: string): Promise<PortalVoter[]> {
  return read(await apiFetch(`${BASE}/ideas/${id}/voters`), 'Failed to load voters');
}

export async function getActivity(id: string): Promise<PortalActivityRow[]> {
  return read(await apiFetch(`${BASE}/ideas/${id}/activity`), 'Failed to load activity');
}

export async function listPortalComments(id: string): Promise<PortalComment[]> {
  const rows = await read<Raw[]>(await apiFetch(`${BASE}/ideas/${id}/comments`), 'Failed to load comments');
  return rows.map(toComment);
}

export async function togglePortalVote(id: string): Promise<PortalVoteResult> {
  return read(await apiFetch(`${BASE}/ideas/${id}/vote`, jsonInit('POST')), 'Failed to vote');
}

export async function postPortalComment(
  id: string,
  input: { body: string; parentId?: string | null },
): Promise<PortalComment> {
  const body: Record<string, string> = { body: input.body };
  if (input.parentId) body.parentId = input.parentId;
  return toComment(
    await read<Raw>(
      await apiFetch(`${BASE}/ideas/${id}/comments`, jsonInit('POST', body)),
      'Failed to post the comment',
    ),
  );
}

export async function likePortalComment(id: string, commentId: string): Promise<PortalLikeResult> {
  return read(
    await apiFetch(`${BASE}/ideas/${id}/comments/${commentId}/like`, jsonInit('POST')),
    'Failed to like the comment',
  );
}

export async function setPortalHidden(id: string, hidden: boolean): Promise<{ portalHidden: boolean }> {
  return read(
    await apiFetch(`${BASE}/ideas/${id}/hidden`, jsonInit('PUT', { hidden })),
    'Failed to update the portal visibility',
  );
}

export async function setPortalNote(id: string, note: string): Promise<{ note: string | null }> {
  return read(await apiFetch(`${BASE}/ideas/${id}/note`, jsonInit('PUT', { note })), 'Failed to save the note');
}

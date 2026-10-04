/**
 * Public idea track page service (the customer's own idea, reached by its status token).
 *
 * The token in the link is the credential, so these are plain `fetch` calls: no Authorization
 * header and nothing from the CRM session (the portal pattern of `/portal/ticket-draft/[token]`).
 *
 *   getPortalIdea      GET  /api/v1/public/portal/ideas/{token}
 *       ss `PublicIdeaStatusOut` through the CRM allow-list; its `status` is the status LABEL.
 *       404 (unknown or malformed token alike) is a `PortalIdeaNotFoundError`.
 *   listPortalComments GET  /api/v1/public/portal/ideas/{token}/comments  (ss `isDeleted` -> `deleted`)
 *   postPortalComment  POST /api/v1/public/portal/ideas/{token}/comments  {body}
 *       The author is the idea's submitter, set by ss; no name or email is ever sent. A rate-limit
 *       429 reads as an Error with the server's message.
 */
import { extractApiError } from '@/lib/api-client';
import type { PortalIdea, PortalIdeaComment } from '@/types/ideas';

const BASE = '/api/v1/public/portal/ideas';

export class PortalIdeaNotFoundError extends Error {
  constructor() {
    super('This link is not valid.');
    this.name = 'PortalIdeaNotFoundError';
  }
}

type Raw = Record<string, unknown>;

async function read<T>(response: Response, fallback: string): Promise<T> {
  if (response.status === 404) throw new PortalIdeaNotFoundError();
  if (!response.ok) throw new Error(await extractApiError(response, fallback));
  return response.json();
}

function toComment(raw: Raw): PortalIdeaComment {
  const deleted = !!raw.isDeleted;
  return {
    id: String(raw.id),
    parentId: (raw.parentId as string | null | undefined) ?? null,
    authorName: String(raw.authorName ?? ''),
    isSubmitter: !!raw.isSubmitter,
    body: deleted ? '' : String(raw.body ?? ''),
    createdAt: String(raw.createdAt),
    deleted,
  };
}

export async function getPortalIdea(token: string): Promise<PortalIdea> {
  const raw = await read<Raw>(await fetch(`${BASE}/${encodeURIComponent(token)}`), 'Could not load this page.');
  return {
    title: (raw.title as string | null | undefined) ?? null,
    ideaNumber: (raw.ideaNumber as string | null | undefined) ?? null,
    statusLabel: String(raw.status ?? ''),
    statusColor: String(raw.statusColor ?? ''),
    submittedAt: (raw.submittedAt as string | null | undefined) ?? null,
    submitterFirstName: (raw.submitterFirstName as string | null | undefined) ?? null,
    upvotes: Number(raw.upvotes ?? 0),
    problem: (raw.problem as string | null | undefined) ?? null,
    proposedSolution: (raw.proposedSolution as string | null | undefined) ?? null,
    impact: (raw.impact as string | null | undefined) ?? null,
    department: (raw.department as string | null | undefined) ?? null,
  };
}

export async function listPortalComments(token: string): Promise<PortalIdeaComment[]> {
  const rows = await read<Raw[]>(
    await fetch(`${BASE}/${encodeURIComponent(token)}/comments`),
    'Could not load the comments.',
  );
  return rows.map(toComment);
}

export async function postPortalComment(token: string, body: string): Promise<PortalIdeaComment> {
  return toComment(
    await read<Raw>(
      await fetch(`${BASE}/${encodeURIComponent(token)}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body }),
      }),
      'Could not post your comment.',
    ),
  );
}

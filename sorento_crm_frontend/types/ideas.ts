/**
 * Ideas as the CRM sees them: the ss embed API's camelCase shapes relayed by the
 * CRM gateway (`/api/v1/ideation/*`, PLAN-ideation-in-crm section 10). Field names
 * follow ss `IdeaOut`, `TransitionOut`, `IdeaAttachmentOut`, `BoardColumnOut`.
 */

export type IdeaSource = 'whatsapp' | 'manual' | 'email' | 'web';

/** ss `statusColor`: a tenant-editable colour key (`gray`, `blue`, `amber`, ...) that maps onto a CRM Badge variant. */
export type IdeaStatusColor = string;

export interface IdeaTransition {
  id: string;
  label: string;
  toStatusId: string;
  /** The tenant's display label of the target status ("Triaged"). */
  toStatusLabel: string;
}

export interface IdeaAttachment {
  id: string;
  kind: 'image' | 'audio' | 'video' | 'file';
  name: string;
  sizeBytes: number | null;
  durationSec: number | null;
  /** An uploaded file the gateway streams (`contentPath`), as opposed to a link ss holds. */
  hasContent: boolean;
  /** A direct link for a URL-backed WhatsApp capture; empty when the file is uploaded. */
  url: string;
}

export interface IdeaMergedInto {
  id: string;
  ideaNumber: string | null;
  title: string | null;
}

export interface Idea {
  id: string;
  productName: string;
  /** The lifecycle key ss uses (`captured`, `triaged`, ...). */
  status: string;
  statusId: string;
  statusLabel: string;
  statusColor: IdeaStatusColor;
  statusIsArchived: boolean;
  transitions: IdeaTransition[];
  advanceTransitionId: string | null;
  title: string | null;
  problem: string;
  proposedSolution: string | null;
  impact: string | null;
  department: string | null;
  rawText: string;
  source: IdeaSource;
  submitterName: string;
  upvotes: number;
  /** Upvote only: the viewer has voted or has not. */
  myVote: 'up' | null;
  priority: number;
  rank: number | null;
  attachments: IdeaAttachment[];
  createdAt: string;
  ideaNumber: string | null;
  mergedIntoId: string | null;
  mergedInto: IdeaMergedInto | null;
  mergedCount: number;
  /** ss decides whether the viewer submitted this idea (sent after SS-IDEATION-OWN); the CRM never guesses. */
  isMine?: boolean;
}

export interface IdeaListParams {
  query?: string;
  /** A status key, or empty for every live idea. `archived` shows archived ones. */
  status?: string;
  /** Only the viewer's own ideas. */
  mine?: boolean;
}

export interface IdeaStatusOption {
  value: string;
  label: string;
}

export interface IdeaBoardColumn {
  statusId: string;
  key: string;
  title: string;
  color: IdeaStatusColor;
  ideas: Idea[];
}

export interface IdeaBoard {
  columns: IdeaBoardColumn[];
}

export interface IdeaCreateInput {
  problem: string;
  proposedSolution?: string;
  impact?: string;
  department?: string;
  rawText?: string;
  /** Uploaded after create through the attachments route. */
  files?: File[];
}

export interface IdeaUpdateInput {
  problem?: string;
  proposedSolution?: string | null;
  impact?: string | null;
  department?: string | null;
  rawText?: string;
}

export interface IdeaMergeInput {
  survivorId: string;
  ideaIds: string[];
}

export interface IdeaPromoteInput {
  title: string;
}

export interface IdeaComment {
  id: string;
  /** Null for a top-level comment; replies attach to the top-level one (one level). */
  parentId: string | null;
  authorName: string;
  /** Empty when `deleted`. Plain text. */
  body: string;
  createdAt: string;
  editedAt: string | null;
  /** Kept only when it still has replies; the body is then withheld. */
  deleted: boolean;
  /** ss decides: the browser never guesses ownership or moderation rights. */
  canEdit: boolean;
  canDelete: boolean;
}

/** The public track page (ss `PublicIdeaStatusOut`, widened with the comments' author). */
export interface PortalIdea {
  title: string | null;
  ideaNumber: string | null;
  statusLabel: string;
  statusColor: IdeaStatusColor;
  submittedAt: string | null;
  submitterFirstName: string | null;
  upvotes: number;
  problem: string | null;
  proposedSolution: string | null;
  impact: string | null;
  department: string | null;
}

export interface PortalIdeaComment {
  id: string;
  parentId: string | null;
  /** The idea's submitter for a public post, a staff first name for a staff one. */
  authorName: string;
  isSubmitter: boolean;
  body: string;
  createdAt: string;
  deleted: boolean;
}

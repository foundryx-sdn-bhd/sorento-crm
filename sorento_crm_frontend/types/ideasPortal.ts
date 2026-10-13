/**
 * The Roadmap portal as the CRM sees it: the ss portal API's camelCase shapes relayed by the
 * gateway (`/api/v1/ideation/portal/*`, PLAN-idea-crm-side-13oct section 2).
 */
import type { IdeaStatusColor } from '@/types/ideas';

/** One card on the roadmap, the feedback list and the changelog. */
export interface PortalCard {
  id: string;
  ideaNumber: string | null;
  title: string | null;
  statusLabel: string;
  statusColor: IdeaStatusColor;
  upvotes: number;
  myVote: boolean;
  commentCount: number;
}

export interface PortalRoadmapColumn {
  key: string;
  title: string;
  color: IdeaStatusColor;
  /** The column's full size; `ideas` may hold fewer. */
  count: number;
  ideas: PortalCard[];
}

export interface PortalRoadmap {
  columns: PortalRoadmapColumn[];
}

export interface PortalFeedbackParams {
  q?: string;
  sort?: 'top' | 'new';
  mine?: boolean;
  page?: number;
  pageSize?: number;
}

export interface PortalFeedbackPage {
  items: PortalCard[];
  total: number;
  page: number;
  pageSize: number;
}

export interface PortalChangelogItem extends PortalCard {
  deliveredAt: string | null;
  note: string | null;
}

export interface PortalChangelog {
  items: PortalChangelogItem[];
}

export interface PortalTimelineStep {
  key: string;
  label: string;
  color: IdeaStatusColor;
  state: 'done' | 'current' | 'upcoming';
}

export interface PortalIdeaDetail extends PortalCard {
  problem: string | null;
  desiredOutcome: string | null;
  /** Always a list: ss #123 sends a string, the service splits it. */
  useCases: string[];
  currentWorkaround: string | null;
  submitterFirstName: string | null;
  timeline: PortalTimelineStep[];
  /** Managers only; absent for everyone else. */
  portalHidden?: boolean;
  note?: string | null;
}

export interface PortalVoter {
  name: string;
  role: string | null;
  isMe: boolean;
}

export interface PortalActivityRow {
  id: string;
  kind: string;
  label: string;
  at: string;
  actorName: string | null;
}

export interface PortalComment {
  id: string;
  parentId: string | null;
  authorName: string;
  authorRole: string | null;
  /** Empty when `deleted`. Plain text. */
  body: string;
  createdAt: string;
  editedAt: string | null;
  deleted: boolean;
  canEdit: boolean;
  canDelete: boolean;
  likeCount: number;
  likedByMe: boolean;
}

export interface PortalVoteResult {
  myVote: boolean;
  upvotes: number;
}

export interface PortalLikeResult {
  likeCount: number;
  likedByMe: boolean;
}

/**
 * Who handled this conversation, on the DETAIL page header (feedback
 * 2026-08-16, item 1; superseded by owner ruling S4, 23 Sep 2026,
 * PLAN-keep-assignee-on-resolve-22sep).
 *
 * The captain landed here from "Recently resolved" and could not see who had
 * answered: the assignee was only inside a collapsed section, and on a resolved
 * row it used to read "-" (resolve cleared it). Resolve keeps the assignee for
 * audit now, so the header names the assignee on every row, open or resolved,
 * and additionally names the resolver alongside it once the row is resolved.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { ConversationSLATracking } from '../types/conversationSLATracking.types';

const useConversationSLATrackingDetail = vi.fn();

vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

vi.mock('../hooks/useConversationSLATracking', () => ({
  // The pager reads the list page through the entity's shared key + fetch (S3-03).
  conversationSlaPagerQuery: {
    listQueryKey: () => ['conversation-sla-tracking'],
    fetchPage: async () => ({ data: [], pagination: { total: 0 } }),
  },
  useConversationSLATrackingDetail: (...a: unknown[]) =>
    useConversationSLATrackingDetail(...a),
  useDeleteConversationSLATracking: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncAssigneeFromRespond: () => ({ mutate: vi.fn(), isPending: false }),
  useConversationSLATestOverrides: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: [] }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  // Back, and the delete that lands where Back lands, read the list state the
  // row click wrote into this URL.
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => false }));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('./EventLogTable', () => ({ default: () => <div data-testid="event-log" /> }));
vi.mock('./SlaTrackingChatRecords', () => ({ default: () => <div data-testid="chat-records" /> }));
vi.mock('./ConversationSLATrackingNavigation', () => ({ default: () => <div /> }));
vi.mock('@/components/contacts/PortalLinkButton', () => ({ default: () => <div /> }));

import ConversationSLATrackingDetail from './ConversationSLATrackingDetail';

function tracking(over: Partial<ConversationSLATracking> = {}): ConversationSLATracking {
  return {
    id: 'tr-1',
    policy_id: 'pol-1',
    current_tier: 1,
    assigned_to: null,
    assigned_to_id: 'u-1',
    assigned_user_name: 'Aisyah Rahman',
    initiated_at: '2026-08-12T02:00:00',
    current_tier_started_at: '2026-08-12T02:00:00',
    due_at: '2026-08-12T03:00:00',
    is_responded: false,
    is_resolved: false,
    contact_phone: '+60 12-000 0553',
    contact_name: 'Aisyah Rahman',
    policy_name: 'Conversation SLA - Standard',
    ...over,
  } as ConversationSLATracking;
}

beforeEach(() => {
  useConversationSLATrackingDetail.mockReset();
});

describe('ConversationSLATrackingDetail handler line', () => {
  it('an open row names its assignee in the header', () => {
    useConversationSLATrackingDetail.mockReturnValue({
      data: tracking(),
      isLoading: false,
    });
    render(<ConversationSLATrackingDetail trackingId="tr-1" />);

    expect(screen.getByTestId('tracking-handler')).toHaveTextContent(
      'Assigned to: Aisyah Rahman',
    );
  });

  it('a resolved row names both its assignee and its resolver, even when they differ', () => {
    useConversationSLATrackingDetail.mockReturnValue({
      data: tracking({
        is_resolved: true,
        assigned_to_id: 'u-1',
        assigned_user_name: 'Aisyah Rahman',
        resolved_at: new Date('2026-08-12T04:00:00Z'),
        resolved_by_user_name: 'Charissa Tan',
      }),
      isLoading: false,
    });
    render(<ConversationSLATrackingDetail trackingId="tr-1" />);

    expect(screen.getByTestId('tracking-handler')).toHaveTextContent(
      'Assigned to: Aisyah Rahman',
    );
    expect(screen.getByTestId('tracking-resolver')).toHaveTextContent(
      'Resolved by: Charissa Tan',
    );
  });

  it('an open row shows no resolver line at all', () => {
    useConversationSLATrackingDetail.mockReturnValue({
      data: tracking(),
      isLoading: false,
    });
    render(<ConversationSLATrackingDetail trackingId="tr-1" />);

    expect(screen.queryByTestId('tracking-resolver')).not.toBeInTheDocument();
  });

  it('names nobody rather than printing an id, for either role', () => {
    useConversationSLATrackingDetail.mockReturnValue({
      data: tracking({
        is_resolved: true,
        assigned_to_id: null,
        assigned_user_name: null,
        assigned_to: '8f14e45f-ceea-467a-9c8b-0f3f6a1d5c22',
        resolved_by_user_name: '8f14e45f-ceea-467a-9c8b-0f3f6a1d5c22',
      }),
      isLoading: false,
    });
    render(<ConversationSLATrackingDetail trackingId="tr-1" />);

    expect(screen.getByTestId('tracking-handler')).toHaveTextContent('Assigned to: -');
    expect(screen.getByTestId('tracking-resolver')).toHaveTextContent('Resolved by: -');
  });
});

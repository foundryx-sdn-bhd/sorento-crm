/**
 * The conversation SLA gear obeys D6 like every other gear.
 *
 * Its secondary actions are a workflow (escalate, the test overrides, mark
 * responded), so it passes menu items rather than a `RecordAction[]` - but it was
 * passing them as a RAW `DropdownMenu` into `DetailActions`, which renders that
 * node verbatim. Nothing then ordered the items, so "Delete tracking" sat fourth,
 * in the middle of the list, with no separator marking it off. Routed through
 * `DetailActionsMenu` the rule that the other workflow gears already obey applies
 * here too: destructive last, behind a separator, in red.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

import type { ConversationSLATracking } from '../types/conversationSLATracking.types';

const useConversationSLATrackingDetail = vi.fn();

vi.mock('@/components/common/ListPager', () => ({ __esModule: true, default: () => null }));

vi.mock('../hooks/useConversationSLATracking', () => ({
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
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => false }));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('./EventLogTable', () => ({ default: () => <div data-testid="event-log" /> }));
vi.mock('./SlaTrackingChatRecords', () => ({
  default: () => <div data-testid="chat-records" />,
}));
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

/** Radix opens on pointerdown, not click. */
function openGear() {
  const trigger = screen.getByRole('button', { name: 'Conversation SLA actions' });
  fireEvent.pointerDown(
    trigger,
    new MouseEvent('pointerdown', { bubbles: true, button: 0 }),
  );
}

beforeEach(() => {
  cleanup();
  useConversationSLATrackingDetail.mockReset();
  useConversationSLATrackingDetail.mockReturnValue({
    data: tracking(),
    isLoading: false,
  });
});

describe('ConversationSLATrackingDetail gear (D6, S3-02)', () => {
  it('ends with Delete tracking, in red, behind a separator', () => {
    render(<ConversationSLATrackingDetail trackingId="tr-1" />);

    openGear();

    const labels = screen
      .getAllByRole('menuitem')
      .map((item) => (item.textContent || '').trim());
    expect(labels[labels.length - 1]).toBe('Delete tracking');
    expect(labels).toContain('Refresh');
    expect(labels).toContain('Sync assignee');
    expect(labels).toContain('Escalate');

    const destructive = screen.getByRole('menuitem', { name: 'Delete tracking' });
    expect(destructive.className).toContain('text-destructive');

    // Not `Array.from(menu.children)` - the menu's scale/opacity spring
    // (S8-01) animates an inner div rather than `[role="menu"]` itself (so it
    // never fights Radix Popper's own positioning transform on that node),
    // which makes every row a grandchild now. `querySelectorAll` still
    // returns them in document order regardless of nesting depth.
    const menu = destructive.closest('[role="menu"]') as HTMLElement;
    const rows = Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitem"], [role="separator"]'));
    const separatorIndex = rows.findIndex(
      (el) => el.getAttribute('role') === 'separator',
    );
    expect(separatorIndex).toBe(rows.indexOf(destructive) - 1);
  });
});

/**
 * Fix round 4: the Chat Records `Sheet`'s own `SheetContent` must not be
 * independently scrollable - `SheetBody` is the ONE intended scroll
 * container, and a second one around the same flex-fill thread is what
 * produced the 375px overlap defect (the ticket drawer's identical Sheet).
 * Pinned here because reverting ONLY this file's change left the whole
 * suite green - nothing else exercises this specific Sheet mount's classes.
 */
describe('ConversationSLATrackingDetail Chat Records sheet layout (fix round 4)', () => {
  it('the sheet content does not double-scroll against SheetBody', async () => {
    useConversationSLATrackingDetail.mockReturnValue({
      data: tracking({ respond_io_id: '10025531' }),
      isLoading: false,
    });
    render(<ConversationSLATrackingDetail trackingId="tr-1" />);

    fireEvent.click(screen.getByRole('button', { name: /Chat Records/i }));
    await screen.findByTestId('chat-records');

    const sheetContent = document.querySelector('[data-slot="sheet-content"]');
    expect(sheetContent).toBeTruthy();
    expect(sheetContent?.className).not.toContain('overflow-y-auto');
    expect(sheetContent?.className).toContain('overflow-hidden');
  });
});

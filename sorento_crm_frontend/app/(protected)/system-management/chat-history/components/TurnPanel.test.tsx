/**
 * S2b Phase 2 test-first (AC-251 to AC-254, AC-258). Written against the Phase 1
 * component that already ships (`TurnPanel.tsx` + `turnPresentation.ts`), so this file
 * is a REGRESSION LOCK for that Phase 1 shape rather than a red-then-green pair by
 * itself - the actual Phase 2 gap this program has is the backend endpoints
 * (`tests/chatbot/test_turns_admin_api.py`, backend repo) and the list-level failed
 * contacts filter (`ChatbotTurnFilters.test.tsx` in this same directory), both of which
 * fail today for the right reason. Confirmed here: no test file existed for this
 * component before this one (there is nothing to have silently been green against).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { TurnPanel } from './TurnPanel';
import type { ChatbotTurn, TurnTraceRecord } from '../types/chatbotTurn.types';

const retryMutate = vi.fn();
let retryState: { isPending: boolean } = { isPending: false };

vi.mock('../hooks/useChatbotTurns', () => ({
  useRetryChatbotTurn: () => ({ mutate: retryMutate, isPending: retryState.isPending }),
  // TurnPanel renders TurnDetailDrawer for its "open full trace" button (Slice D2);
  // the drawer is closed in every test here (`turnId` is null), but the hook still
  // runs (with `enabled: false`), so it needs a shape to return.
  useChatbotTurn: () => ({ data: undefined, isLoading: false, isError: false }),
}));

// A stub, not the real Sheet: this file's job is proving TurnPanel PASSES the right
// turnId to the drawer when its row's trigger is clicked (Slice D2, AC-972), not
// re-testing TurnDetailDrawer's own rendering (covered in its own test file).
vi.mock('./TurnDetailDrawer', () => ({
  TurnDetailDrawer: ({ turnId }: { turnId: string | null }) =>
    turnId ? <div data-testid="turn-detail-drawer-stub">{turnId}</div> : null,
}));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  retryMutate.mockReset();
  retryState = { isPending: false };
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

afterEach(() => cleanup());

function record(over: Partial<TurnTraceRecord> = {}): TurnTraceRecord {
  return {
    stage: 'received',
    status: 'ok',
    started_at: '2026-09-05T06:00:00.000Z',
    ms: 100,
    summary: 'Received a text message.',
    why: 'Every turn starts here.',
    facts: {},
    error: null,
    raw: { note: 'raw payload' },
    ...over,
  };
}

function turn(over: Partial<ChatbotTurn> = {}): ChatbotTurn {
  return {
    id: 'ZZT-turn-0001',
    contact_respond_id: 'ZZT-contact-0001',
    message_id: 'wamid.zzt.0001',
    status: 'done',
    stage: 'sent',
    branch_kind: 'business_query',
    attempt: 1,
    is_test: false,
    created_at: '2026-09-05T06:00:00.000Z',
    finished_at: '2026-09-05T06:00:04.000Z',
    trace: [record({ stage: 'received' }), record({ stage: 'understood' })],
    response: { reply: { text: 'SRTWC8517: 12 pcs on hand.' } },
    ...over,
  };
}

describe('TurnPanel - AC-251 status line', () => {
  it('shows a destructive-toned Failed badge naming the stage it failed at', () => {
    renderWithClient(
      <TurnPanel
        turn={turn({
          status: 'failed',
          stage: 'understood',
          branch_kind: null,
          trace: [
            record({ stage: 'received' }),
            record({ stage: 'understood', status: 'failed', error: 'Provider timed out' }),
          ],
        })}
      />,
    );
    expect(screen.getByText('Failed at Understood')).toBeInTheDocument();
  });

  it('renders a skipped stage with the muted dot, not the green one', () => {
    const { container } = renderWithClient(
      <TurnPanel
        turn={turn({
          branch_kind: 'ai_not_allowed',
          trace: [record({ stage: 'received', status: 'skipped', summary: 'AI not allowed' })],
        })}
      />,
    );
    fireEvent.click(screen.getByText('details'));
    expect(container.querySelector('.bg-muted-foreground\\/40')).not.toBeNull();
    expect(container.querySelector('.bg-destructive')).toBeNull();
  });

  it('shows the lane in words for an answered turn, distinct from the status word', () => {
    renderWithClient(<TurnPanel turn={turn({ branch_kind: 'business_query' })} />);
    expect(screen.getByText('Answered')).toBeInTheDocument();
    expect(screen.getByText('Business query')).toBeInTheDocument();
  });

  it('shows the attempt badge only when attempt is greater than 1', () => {
    renderWithClient(<TurnPanel turn={turn({ attempt: 1 })} />);
    expect(screen.queryByText(/attempt/i)).not.toBeInTheDocument();
    cleanup();
    renderWithClient(<TurnPanel turn={turn({ attempt: 2 })} />);
    expect(screen.getByText(/attempt 2/i)).toBeInTheDocument();
  });
});

describe('TurnPanel - AC-252 stage timeline', () => {
  it('renders one row per present stage and collapses the rest into one not-reached row', () => {
    renderWithClient(
      <TurnPanel
        turn={turn({
          status: 'failed',
          stage: 'access',
          branch_kind: null,
          trace: [
            record({ stage: 'received' }),
            record({ stage: 'understood' }),
            record({ stage: 'access', status: 'failed', error: 'Access service unavailable' }),
          ],
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /details/i }));

    expect(screen.getByText('Received')).toBeInTheDocument();
    expect(screen.getByText('Understood')).toBeInTheDocument();
    expect(screen.getByText('Access')).toBeInTheDocument();
    // Routed, Looked up, Replied, Remembered, Sent never ran - one collapsed row,
    // not five greyed placeholders.
    expect(screen.getByText('not reached')).toBeInTheDocument();
    expect(screen.queryByText('Routed')).not.toBeInTheDocument();
    expect(screen.queryByText('Sent')).not.toBeInTheDocument();
  });

  it('omits a stage that never runs for the lane without a not-reached row when the turn did not fail', () => {
    renderWithClient(
      <TurnPanel
        turn={turn({
          status: 'done',
          branch_kind: 'clarify_menu',
          trace: [record({ stage: 'received' }), record({ stage: 'understood' })],
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /details/i }));
    expect(screen.queryByText('not reached')).not.toBeInTheDocument();
  });
});

describe('TurnPanel - AC-253 failed stage: reason, retry, technical details', () => {
  const failedTurn = () =>
    turn({
      status: 'failed',
      stage: 'understood',
      branch_kind: null,
      trace: [
        record({
          stage: 'understood',
          status: 'failed',
          error: 'The parser call timed out after 8 seconds.',
        }),
      ],
    });

  it('shows the reason sentence on the failed row', () => {
    renderWithClient(<TurnPanel turn={failedTurn()} />);
    fireEvent.click(screen.getByRole('button', { name: /details/i }));
    expect(screen.getByText('The parser call timed out after 8 seconds.')).toBeInTheDocument();
  });

  it('enables Retry only when the turn is failed', () => {
    renderWithClient(<TurnPanel turn={failedTurn()} />);
    fireEvent.click(screen.getByRole('button', { name: /details/i }));
    const retryButton = screen.getByRole('button', { name: /retry turn/i });
    expect(retryButton).toBeEnabled();

    fireEvent.click(retryButton);
    expect(retryMutate).toHaveBeenCalledWith(failedTurn().id);
  });

  it('does not render a Retry button when the turn did not fail', () => {
    renderWithClient(<TurnPanel turn={turn({ status: 'done' })} />);
    fireEvent.click(screen.getByRole('button', { name: /details/i }));
    expect(screen.queryByRole('button', { name: /retry turn/i })).not.toBeInTheDocument();
  });

  it('opens Technical details behind its own toggle, using the raw payload', () => {
    renderWithClient(<TurnPanel turn={failedTurn()} />);
    fireEvent.click(screen.getByRole('button', { name: /details/i }));
    expect(screen.queryByTestId('turn-raw')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /technical details/i }));
    expect(screen.getByTestId('turn-raw')).toBeInTheDocument();
    expect(screen.getByTestId('turn-raw').textContent).toContain('understood');
  });
});

describe('TurnPanel - AC-254 Remembered: Kept / New / Cleared', () => {
  it('renders memory chips in words with the raw key in the tooltip', () => {
    renderWithClient(
      <TurnPanel
        turn={turn({
          trace: [
            record({
              stage: 'remembered',
              raw: {
                before: { domain_hint: 'inventory' },
                after: { domain_hint: 'inventory', entities: [{ raw: 'SRTWC8517' }] },
              },
            }),
          ],
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /details/i }));

    const kept = screen.getByText(/kept · topic/i);
    expect(kept).toBeInTheDocument();
    expect(kept.closest('[title]')?.getAttribute('title')).toBe('domain_hint');

    const added = screen.getByText(/new · things being asked about/i);
    expect(added).toBeInTheDocument();
    expect(added.closest('[title]')?.getAttribute('title')).toBe('entities');
  });
});

describe('TurnPanel - AC-972 opens the turn detail drawer from a row', () => {
  it('the row is closed until its "Open full trace" button is clicked', () => {
    renderWithClient(<TurnPanel turn={turn({ id: 'ZZT-turn-open-detail' })} />);

    expect(screen.queryByTestId('turn-detail-drawer-stub')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /open full trace/i }));

    expect(screen.getByTestId('turn-detail-drawer-stub')).toHaveTextContent(
      'ZZT-turn-open-detail',
    );
  });
});

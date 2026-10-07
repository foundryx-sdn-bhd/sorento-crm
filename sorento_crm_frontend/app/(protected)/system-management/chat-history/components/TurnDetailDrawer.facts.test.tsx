/**
 * QUOTED-REPLY browser-pass defect: the trace drawer printed a NESTED stage fact
 * (the `received` stage's `quoted_turn` object) as the literal "[object Object]".
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import { TurnDetailDrawer } from './TurnDetailDrawer';
import type { ChatbotTurnDetail, TurnDetail } from '../types/chatbotTurn.types';

let turnState: { data: ChatbotTurnDetail | undefined; isLoading: boolean; isError: boolean } = {
  data: undefined,
  isLoading: false,
  isError: false,
};

vi.mock('../hooks/useChatbotTurns', () => ({
  useChatbotTurn: () => turnState,
}));

afterEach(() => {
  cleanup();
  turnState = { data: undefined, isLoading: false, isError: false };
});

function detailTurn(): ChatbotTurnDetail {
  const trace_detail = {
    stages: [
      {
        name: 'received',
        status: 'ok',
        ms: 3,
        summary: null,
        error: null,
        facts: {
          quoted_turn: {
            found: true,
            via: 'own',
            turn_id: '0b4c0000-0000-4000-8000-000000000000',
            same_as_live: false,
          },
          modality: 'text',
          entities: ['alpha', 'beta'],
        },
      },
    ],
    parse: null,
    decay: [],
    open_question: null,
    focus: [],
    tool: null,
    crossdomain: [],
    reveals: { restricted_fields_seen: [], granted: [], dropped: [] },
    session: { before: {}, after: {}, diff: [] },
  } as unknown as TurnDetail;
  return {
    id: 'ZZT-turn-facts-1',
    contact_respond_id: 'ZZT-contact-1',
    message_id: 'wamid.zzt.facts.1',
    status: 'done',
    stage: 'sent',
    branch_kind: 'business_query',
    attempt: 1,
    is_test: false,
    created_at: '2026-09-27T00:00:00Z',
    finished_at: '2026-09-27T00:00:02Z',
    trace: [],
    response: null,
    trace_detail,
  };
}

describe('TurnDetailDrawer stage facts', () => {
  it('renders a nested fact as readable key: value text, never [object Object]', () => {
    turnState = { data: detailTurn(), isLoading: false, isError: false };
    const { container } = render(
      <TurnDetailDrawer turnId="ZZT-turn-facts-1" onOpenChange={vi.fn()} />,
    );
    const text = document.body.textContent ?? '';

    expect(text).not.toContain('[object Object]');
    expect(text).toContain('found: true');
    expect(text).toContain('via: own');

    const dd = Array.from(document.querySelectorAll('dd')).find((el) =>
      (el.textContent ?? '').includes('via: own'),
    );
    expect(dd).toBeTruthy();
    expect(dd!.getAttribute('title')).toContain('found: true');
    expect(dd!.getAttribute('title')).toContain('via: own');
    // No UUIDs in the UI: the nested turn_id reads shortened, in the text and the title.
    const full = '0b4c0000-0000-4000-8000-000000000000';
    expect(text).not.toContain(full);
    expect(dd!.getAttribute('title')).not.toContain(full);
    expect(dd!.getAttribute('title')).toContain('turn_id: 0b4c');
    expect(container).toBeTruthy();
  });

  it('still renders scalar and list facts as before', () => {
    turnState = { data: detailTurn(), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-facts-1" onOpenChange={vi.fn()} />);
    expect(screen.getByText('text')).toBeInTheDocument();
    expect(screen.getByText('alpha,beta')).toBeInTheDocument();
  });
});

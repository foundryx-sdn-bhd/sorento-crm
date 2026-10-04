/**
 * Chatbot growth r1, Slice D2 (AC-972, AC-973). Phase 2 test-first.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';

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

function fullDetail(): TurnDetail {
  return {
    stages: [
      {
        name: 'received',
        started_at: '2026-09-07T00:00:00Z',
        ms: 3,
        status: 'ok',
        summary: 'Received the message.',
        error: null,
      },
      {
        name: 'understood',
        started_at: '2026-09-07T00:00:01Z',
        ms: 5,
        status: 'ok',
        summary: 'Understood as a business query.',
        error: null,
      },
      {
        name: 'sent',
        started_at: '2026-09-07T00:00:02Z',
        ms: 1,
        status: 'ok',
        summary: 'Handed the reply to the caller to send.',
        error: null,
      },
    ],
    parse: {
      raw: { domain_hint: 'master_products' },
      post_processed: { domain_hint: 'master_products', entities: [] },
      prompt_version: 3,
      model: 'gpt-test',
    },
    decay: [
      {
        slot: 'products',
        value: 'SRTWC8517',
        set_at_turn: 1,
        age_turns: 4,
        age_minutes: 12,
        reason: 'ttl_exceeded',
      },
    ],
    open_question: {
      before: { kind: 'product_pick' },
      answer: { picks: [1] },
      after: null,
      handler: 'product_pick',
      outcome: 'focus.products set',
    },
    focus: [
      {
        slot: 'domain',
        before: 'master_products',
        after: 'incoming',
        rule: 'domain_from_switch_word',
        source: 'current_message',
      },
    ],
    tool: {
      name: 'crm_master_products_list',
      args: { product_ids: ['p-1'] },
      envelope: { items: [] },
      ms: 42,
    },
    crossdomain: [
      { rung: 1, tool: 'crm_incoming_stock_list', args: {}, rows: 2, rendered: true },
    ],
    reveals: {
      restricted_fields_seen: ['inventory.sellable'],
      granted: [],
      dropped: ['inventory.sellable'],
    },
    customer_scope: [
      {
        at: '2026-09-30T12:00:00Z',
        refused: 'customer_not_permitted',
        reason: 'typed_word_matched_only_other_customers',
        typed: ['water tap'],
        dropped: ['ffffffff-0000-0000-0000-00000000000f'],
        self_reference: true,
      },
    ],
    session: {
      before: { domain_hint: 'master_products' },
      after: { domain_hint: 'master_products', new_key: 'x' },
      diff: [{ key: 'new_key', change: 'gained' }],
    },
  };
}

function emptyDetail(): TurnDetail {
  return {
    stages: [
      {
        name: 'received',
        started_at: '2026-09-07T00:00:00Z',
        ms: 3,
        status: 'ok',
        summary: 'Received the message.',
        error: null,
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
  };
}

function detailTurn(trace_detail: TurnDetail): ChatbotTurnDetail {
  return {
    id: 'ZZT-turn-detail-1',
    contact_respond_id: 'ZZT-contact-1',
    message_id: 'wamid.zzt.1',
    status: 'done',
    stage: 'sent',
    branch_kind: 'business_query',
    attempt: 1,
    is_test: false,
    created_at: '2026-09-07T00:00:00Z',
    finished_at: '2026-09-07T00:00:02Z',
    trace: [],
    response: null,
    trace_detail,
  };
}

function openAllSections() {
  for (const testId of [
    'section-parse-trigger',
    'section-decay-trigger',
    'section-open-question-trigger',
    'section-focus-trigger',
    'section-tool-trigger',
    'section-crossdomain-trigger',
    'section-reveals-trigger',
    'section-customer-scope-trigger',
    'section-session-trigger',
  ]) {
    fireEvent.click(screen.getByTestId(testId));
  }
}

describe('TurnDetailDrawer', () => {
  it('renders every section from a fixture carrying all kinds', () => {
    turnState = { data: detailTurn(fullDetail()), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    openAllSections();

    expect(screen.getByText('received')).toBeInTheDocument();
    expect(screen.getByText('understood')).toBeInTheDocument();
    expect(screen.getByText('gpt-test')).toBeInTheDocument();
    expect(screen.getByText('ttl_exceeded')).toBeInTheDocument();
    expect(screen.getByText('product_pick')).toBeInTheDocument();
    expect(screen.getByText('domain_from_switch_word')).toBeInTheDocument();
    expect(screen.getByText('crm_master_products_list')).toBeInTheDocument();
    expect(screen.getByText('crm_incoming_stock_list')).toBeInTheDocument();
    expect(screen.getAllByText('inventory.sellable').length).toBeGreaterThan(0);
    expect(screen.getByText('new_key')).toBeInTheDocument();
  });

  it('CHATBOT-SELFREF-SCOPE R4: a customer scope refusal explains itself in its own panel', () => {
    turnState = { data: detailTurn(fullDetail()), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    fireEvent.click(screen.getByTestId('section-customer-scope-trigger'));

    const panel = screen.getByTestId('section-customer-scope');
    expect(within(panel).getByText('refused')).toBeInTheDocument();
    expect(within(panel).getByText('typed_word_matched_only_other_customers')).toBeInTheDocument();
    expect(within(panel).getByText('water tap')).toBeInTheDocument();
    expect(within(panel).getByText('self-reference')).toBeInTheDocument();
    expect(within(panel).getByText('dropped: 1')).toBeInTheDocument();
  });

  it('a trace with no customer scope decision shows the panel empty, not absent', () => {
    turnState = { data: detailTurn(emptyDetail()), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    fireEvent.click(screen.getByTestId('section-customer-scope-trigger'));

    expect(screen.getByText('No customer scope decision this turn.')).toBeInTheDocument();
  });

  it('browser pass finding (16 Sep 2026): a trace with a sent stage renders a dedicated Sent panel with its summary, not just a row buried in Stages', () => {
    turnState = { data: detailTurn(fullDetail()), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    fireEvent.click(screen.getByTestId('section-sent-trigger'));

    const sentPanel = screen.getByTestId('section-sent');
    expect(within(sentPanel).getByText('Handed the reply to the caller to send.')).toBeInTheDocument();
  });

  it('a turn recorded with no sent stage shows the Sent panel empty, not absent', () => {
    turnState = { data: detailTurn(emptyDetail()), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    fireEvent.click(screen.getByTestId('section-sent-trigger'));

    const sentPanel = screen.getByTestId('section-sent');
    expect(within(sentPanel).getByText(/no sent stage/i)).toBeInTheDocument();
  });

  it('renders an empty section for every kind absent from the trace', () => {
    turnState = { data: detailTurn(emptyDetail()), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    openAllSections();

    expect(screen.getByText('No parse recorded.')).toBeInTheDocument();
    expect(screen.getByText('Nothing decayed this turn.')).toBeInTheDocument();
    expect(screen.getByText('No open question this turn.')).toBeInTheDocument();
    expect(screen.getByText('No focus rule fired this turn.')).toBeInTheDocument();
    expect(screen.getByText('No tool call recorded.')).toBeInTheDocument();
    expect(screen.getByText('No cross-domain probe this turn.')).toBeInTheDocument();
    expect(screen.getByText('No restricted field was on this answer.')).toBeInTheDocument();
  });

  it('a failed turn shows the failing stage first', () => {
    const detail = emptyDetail();
    detail.stages = [
      { name: 'understood', started_at: null, ms: 5, status: 'failed', summary: null, error: 'boom' },
      { name: 'received', started_at: null, ms: 3, status: 'ok', summary: 'ok', error: null },
    ];
    turnState = { data: detailTurn(detail), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    const badges = screen.getAllByText(/received|understood/);
    expect(badges[0]).toHaveTextContent('understood');
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('browser pass follow-up: the Stages tab shows a media_intake stage\'s facts, not just its badge and summary', () => {
    // Copied VERBATIM from a real chatbot.turns.trace row on 0921 (turn
    // 767e0472-2388-47f8-9b40-efef429b45a8), projected through the SAME
    // `trace_detail.py::_stages()` shape the backend actually sends - `facts`
    // was dropped from that projection entirely before this fix, so the
    // Stages tab showed the badge and "Read the photo." with nothing else,
    // even though `TurnPanel`'s own inline row (a different component reading
    // the raw trace directly) already rendered the same facts correctly.
    const detail = emptyDetail();
    detail.stages = [
      {
        name: 'media_intake',
        started_at: '2026-09-22T19:44:03.203312Z',
        ms: 2632,
        status: 'ok',
        summary: 'Read the photo.',
        error: null,
        facts: {
          notes: 'Simple list of product codes with no quantities or caption.',
          status: 'completed',
          decision: 'accepted',
          entities: 'BRBC22293W-1, SRTWT1506, SRTWT1805',
          modality: 'image',
          elapsed_ms: 72,
        },
      },
      ...detail.stages,
    ];
    turnState = { data: detailTurn(detail), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    expect(screen.getByText('media_intake')).toBeInTheDocument();
    expect(screen.getByText('Read the photo.')).toBeInTheDocument();
    expect(screen.getByText(/BRBC22293W-1, SRTWT1506, SRTWT1805/)).toBeInTheDocument();
    expect(screen.getByText('accepted')).toBeInTheDocument();
    expect(screen.getByText(/Simple list of product codes/)).toBeInTheDocument();
  });

  it('renders nothing when no turn is picked', () => {
    turnState = { data: undefined, isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId={null} onOpenChange={vi.fn()} />);
    expect(screen.queryByTestId('section-stages')).not.toBeInTheDocument();
  });

  it('browser pass finding (26 Sep 2026): Current subject renders the REAL focus.after shape - domains are bare strings, customers/products are entity dicts', () => {
    const detail = emptyDetail();
    detail.memory = {
      level: { own: 'full', effective: 'full' },
      focus: {
        before: null,
        after: {
          domains: ['order'],
          customers: [{ raw: 'CC001', canonical_code: 'CC001', name: 'Chin Chun Trading' }],
          products: [{ raw: 'SRTWB1455', canonical_code: 'SRTWB1455' }],
          document: [],
          status: null,
        },
        writer: 'apply',
      },
      profile: { before: {}, after: {}, writer: null },
      episodes: { read: [], written: null, writer: 'none' },
      facts_saved: [],
    };
    turnState = { data: detailTurn(detail), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    fireEvent.click(screen.getByTestId('section-memory-trigger'));

    const panel = screen.getByTestId('section-memory');
    expect(
      within(panel).getByText('domain order; customer Chin Chun Trading; product SRTWB1455'),
    ).toBeInTheDocument();
  });

  it('Current subject falls back through raw then canonical_code when an entity has no name', () => {
    const detail = emptyDetail();
    detail.memory = {
      level: { own: null, effective: 'full' },
      focus: {
        before: null,
        after: { products: [{ canonical_code: 'M483-BL' }], status: 'outstanding' },
        writer: 'apply',
      },
      profile: { before: {}, after: {}, writer: null },
      episodes: { read: [], written: null, writer: 'none' },
      facts_saved: [],
    };
    turnState = { data: detailTurn(detail), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    fireEvent.click(screen.getByTestId('section-memory-trigger'));

    const panel = screen.getByTestId('section-memory');
    expect(within(panel).getByText('product M483-BL; status outstanding')).toBeInTheDocument();
  });

  it('Current subject stays "Not recorded on this turn." for an empty focus', () => {
    const detail = emptyDetail();
    detail.memory = {
      level: { own: null, effective: 'off' },
      focus: { before: null, after: {}, writer: null },
      profile: { before: {}, after: {}, writer: null },
      episodes: { read: [], written: null, writer: 'none' },
      facts_saved: [],
    };
    turnState = { data: detailTurn(detail), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    fireEvent.click(screen.getByTestId('section-memory-trigger'));

    const panel = screen.getByTestId('section-memory');
    expect(within(panel).getByText('Not recorded on this turn.')).toBeInTheDocument();
  });

  it('browser pass finding (26 Sep 2026): Context sent to the AI names L1/L2 with friendly labels, not the raw code', () => {
    const detail = emptyDetail();
    detail.context = {
      level: 'full',
      layers: [
        { layer: 'L5', est_tokens: 33, cap: 150 },
        { layer: 'L4', est_tokens: 0, cap: 250 },
        { layer: 'L3', est_tokens: 100, cap: 450 },
        { layer: 'L2', est_tokens: 46, cap: 350 },
        { layer: 'L1', est_tokens: 24, cap: 600 },
      ],
      total_est_tokens: 203,
      cap: 1800,
    };
    turnState = { data: detailTurn(detail), isLoading: false, isError: false };
    render(<TurnDetailDrawer turnId="ZZT-turn-detail-1" onOpenChange={vi.fn()} />);

    fireEvent.click(screen.getByTestId('section-context-trigger'));

    const panel = screen.getByTestId('section-context');
    expect(within(panel).getByText('Current message')).toBeInTheDocument();
    expect(within(panel).getByText('Current subject and open question')).toBeInTheDocument();
    expect(within(panel).queryByText('L1')).not.toBeInTheDocument();
    expect(within(panel).queryByText('L2')).not.toBeInTheDocument();
  });
});

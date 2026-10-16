/**
 * AC-CONSIGN-CRM UAC-06: the fulfilment board list shows "Consignment" beside the SO number
 * when the contribution's `so_doc_type` is consignment. Mocking copied from
 * `FulfilmentBoardListView.test.tsx`.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  BoardContribution,
  BoardDecision,
  BoardDraft,
} from '../../_shared/types/fulfilmentPlanning.types';
import type { BoardChangeAnnotation } from '../../_shared/lib/boardChangeAnnotations';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

import { toast } from '@/lib/toast';
import { FulfilmentBoardListView } from './FulfilmentBoardListView';

vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), warning: vi.fn(), error: vi.fn() }),
}));
import { suggestedDecisionFor } from '../../_shared/lib/boardAmend';

function contribution(overrides: Partial<BoardContribution> = {}): BoardContribution {
  return {
    key: 'so-1:line-10',
    sales_order_id: 'so-1',
    line_id: 'core-line-10',
    product_id: 'prod-1',
    so_number: 'SO397450',
    customer_name: 'Tuju Residences Sdn Bhd',
    agent_code: 'JEREMY',
    agent_label: 'Jeremy Lee',
    project_label: 'Tuju Residences',
    line_no: 10,
    // The live payload carries AutoCount's own number beside the address (#1362 item 5).
    so_line_no: overrides.line_no ?? 10,
    item_code: 'B2155-NL-BLUE',
    qty: '43',
    qty_outstanding: '43',
    required_date: '2026-09-04',
    unplannable: false,
    rank_score: 0.82,
    rank_factors: [],
    sources: [{ kind: 'buy', qty: '43', reason: 'Nothing free at any location.' }],
    trail: [],
    item_flags: null,
    contested: false,
    covered: false,
    decision: null,
    ...overrides,
  };
}

function renderView(
  overrides: {
    contributions?: BoardContribution[];
    draft?: BoardDraft;
    onDecide?: (key: string, decision: BoardDecision | null) => void;
    onDecideMany?: (keys: string[]) => Promise<{ saved: number; failed: number }>;
    onDecideBatch?: (
      entries: { key: string; decision: BoardDecision }[],
    ) => Promise<{ savedKeys: string[]; failed: { key: string; why: string }[] }>;
    annotations?: Map<string, BoardChangeAnnotation[]>;
    externalSearch?: string;
    focusKey?: string | null;
    onFocusHandled?: () => void;
  } = {},
) {
  const rows = overrides.contributions ?? [contribution()];
  const onDecide = overrides.onDecide ?? vi.fn();
  // D15: the same quiet-bulk path the panel wires up for real, standing in here for it - each
  // key's own suggestion, posted through the same `onDecide` a test already reads, so the
  // existing "bulk quick save" assertions on `onDecide`'s own calls still hold.
  const onDecideMany =
    overrides.onDecideMany ??
    vi.fn(async (keys: string[]) => {
      let saved = 0;
      for (const key of keys) {
        const target = rows.find((entry) => entry.key === key);
        if (!target) continue;
        onDecide(key, suggestedDecisionFor(target));
        saved += 1;
      }
      return { saved, failed: keys.length - saved };
    });
  // S3 (D1): the Decide strip's own save path - same shape `FulfilmentBoardPanel.decideBatch`
  // returns, standing in here so the existing `onDecide` assertions keep reading every write.
  const onDecideBatch =
    overrides.onDecideBatch ??
    vi.fn(async (entries: { key: string; decision: BoardDecision }[]) => {
      const savedKeys: string[] = [];
      for (const entry of entries) {
        onDecide(entry.key, entry.decision);
        savedKeys.push(entry.key);
      }
      return { savedKeys, failed: [] };
    });
  const utils = render(
    <FulfilmentBoardListView
      contributions={rows}
      draft={overrides.draft ?? {}}
      onDecide={onDecide}
      onDecideMany={onDecideMany}
      onDecideBatch={onDecideBatch}
      annotations={overrides.annotations}
      externalSearch={overrides.externalSearch}
      focusKey={overrides.focusKey}
      onFocusHandled={overrides.onFocusHandled}
    />,
  );
  return { ...utils, onDecide, onDecideMany, onDecideBatch };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('UAC-06: board list SO cell carries the Consignment label', () => {
  it('a consignment contribution shows Consignment', async () => {
    renderView({
      contributions: [contribution({ so_number: 'CG000123', so_doc_type: 'consignment' } as Partial<BoardContribution>)],
    });
    const row = (await screen.findByText('CG000123')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Consignment')).toBeInTheDocument();
  });

  it('a sales_order contribution shows no Consignment label', async () => {
    renderView({
      contributions: [contribution({ so_number: 'SO000123', so_doc_type: 'sales_order' } as Partial<BoardContribution>)],
    });
    const row = (await screen.findByText('SO000123')).closest('tr') as HTMLElement;
    expect(within(row).queryByText('Consignment')).toBeNull();
  });
});

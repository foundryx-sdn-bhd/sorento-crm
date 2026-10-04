/**
 * useAutocountPullAction - Goods Receipt Notes (lane GRN-PULL-CRM, AC-GP-60).
 *
 * The GRN list calls the SAME shared hook and DocDate dialog the Delivery Orders list uses,
 * with the fourth entity and its own slug. Same harness as the DO test.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const useHasPermission = vi.fn();
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (...a: unknown[]) => useHasPermission(...a),
}));

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
}));

const getCurrentPull = vi.fn();
const startPull = vi.fn();
vi.mock('../services/autocountPullService', () => ({
  getCurrentPull: (...a: unknown[]) => getCurrentPull(...a),
  startPull: (...a: unknown[]) => startPull(...a),
  startPullErrorMessage: (e: unknown) => (e instanceof Error ? e.message : 'x'),
  getPull: vi.fn(),
  getPullRows: vi.fn(),
  downloadPullXlsx: vi.fn(),
  comparePull: vi.fn(),
  confirmPull: vi.fn(),
  discardPull: vi.fn(),
}));

import { useAutocountPullAction } from './useAutocountPull';
import { AUTOCOUNT_PULL_PERMISSION } from '../types/autocountPull.types';

const SLUG = 'procurement.grn.autocount_pull';

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return React.createElement(QueryClientProvider, { client }, children);
}

beforeEach(() => {
  useHasPermission.mockReset();
  getCurrentPull.mockReset();
  startPull.mockReset();
  push.mockClear();
});

describe('useAutocountPullAction - Goods Receipt Notes (AC-GP-60)', () => {
  it("gates on the GRN entity's own slug", () => {
    expect(AUTOCOUNT_PULL_PERMISSION.goods_receive_notes).toBe(SLUG);
    useHasPermission.mockReturnValue(false);
    getCurrentPull.mockResolvedValue(null);

    const { result } = renderHook(() => useAutocountPullAction('goods_receive_notes'), { wrapper });

    expect(useHasPermission).toHaveBeenCalledWith(SLUG);
    expect(result.current.visible).toBe(false);
  });

  it("passes the dialog's DocDate window to startPull and navigates to the new pull", async () => {
    useHasPermission.mockReturnValue(true);
    getCurrentPull.mockResolvedValue(null);
    startPull.mockResolvedValue({ job_id: 'grn-job', entity: 'goods_receive_notes', phase: 'building' });

    const { result } = renderHook(() => useAutocountPullAction('goods_receive_notes'), { wrapper });
    await waitFor(() => expect(result.current.visible).toBe(true));
    expect(result.current.label).toBe('Pull from AutoCount');

    await act(async () => {
      await result.current.onSelect({ fromDay: '2026-10-01', toDay: '2026-10-01' });
    });

    expect(startPull).toHaveBeenCalledWith('goods_receive_notes', { fromDay: '2026-10-01', toDay: '2026-10-01' });
    expect(push).toHaveBeenCalledWith('/system-management/import-jobs/grn-job');
  });
});

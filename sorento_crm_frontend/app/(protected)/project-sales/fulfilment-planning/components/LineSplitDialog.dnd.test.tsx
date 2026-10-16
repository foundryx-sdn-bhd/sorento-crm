/**
 * OI-LINE-SPLIT (real-browser finding): a drop near the bottom rows of the scrolling dialog
 * landed one row lower. The split grid's DndContext must match the POINTER (`pointerWithin`,
 * falling back to `rectIntersection`) and measure droppables while dragging
 * (`MeasuringStrategy.Always`). Pinned by capturing the props handed to DndContext.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}
if (!(globalThis as { ResizeObserver?: unknown }).ResizeObserver) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

const dnd = vi.hoisted(() => ({ props: [] as Array<Record<string, unknown>> }));
vi.mock('@dnd-kit/core', async () => {
  const actual = await vi.importActual<typeof import('@dnd-kit/core')>('@dnd-kit/core');
  return {
    ...actual,
    DndContext: (props: Record<string, unknown> & { children?: React.ReactNode }) => {
      dnd.props.push(props);
      return React.createElement(
        actual.DndContext as unknown as React.ComponentType<Record<string, unknown>>,
        props,
      );
    },
  };
});

const { getLineSplits, saveLineSplits } = vi.hoisted(() => ({
  getLineSplits: vi.fn(),
  saveLineSplits: vi.fn(),
}));
vi.mock('../../_shared/services/lineSplitService', () => ({
  getLineSplits: (...a: unknown[]) => getLineSplits(...a),
  saveLineSplits: (...a: unknown[]) => saveLineSplits(...a),
}));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

const rect = (left: number, top: number, w = 90, h = 60) => ({
  left, top, right: left + w, bottom: top + h, width: w, height: h,
});

const PAYLOAD = {
  pso_id: 'pso-1', so_number: 'SO1', customer: 'C', project: 'P',
  lines: [
    { line_id: 'l1', line_no: 1, item_code: 'ITEM', location: 'BRW-BB', base_qty: 10,
      base_date: '2026-11-01', now_qty: 10, now_date: '2026-11-01', pending_change: false,
      delivered_qty: 0, split_state: 'acting', acting: true, splits: [] },
    { line_id: 'l2', line_no: 2, item_code: 'ITEM', location: 'BRW-BB', base_qty: 10,
      base_date: '2026-11-01', now_qty: 10, now_date: '2026-11-01', pending_change: false,
      delivered_qty: 0, split_state: 'acting', acting: true, splits: [] },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  dnd.props.length = 0;
  getLineSplits.mockResolvedValue(structuredClone(PAYLOAD));
});

describe('split grid drag configuration', () => {
  it('uses pointer-based collision detection and measures droppables while dragging', async () => {
    const path = './LineSplitDialog';
    const mod = (await import(/* @vite-ignore */ path)) as {
      LineSplitDialog: React.ComponentType<{
        open: boolean; psoId: string; focusLineId?: string; onOpenChange: (o: boolean) => void;
      }>;
    };
    const { MeasuringStrategy } = await import('@dnd-kit/core');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <mod.LineSplitDialog open psoId="pso-1" focusLineId="l1" onOpenChange={vi.fn()} />
      </QueryClientProvider>,
    );
    await screen.findByTestId('line-split-row-l1');
    const ctx = dnd.props.find((p) => p.collisionDetection || p.measuring) ?? dnd.props[0];
    expect(ctx, 'a DndContext rendered').toBeDefined();

    const measuring = ctx.measuring as { droppable?: { strategy?: unknown } } | undefined;
    expect(measuring?.droppable?.strategy).toBe(MeasuringStrategy.Always);

    const detect = ctx.collisionDetection as ((args: Record<string, unknown>) => Array<{ id: unknown }>) | undefined;
    expect(typeof detect).toBe('function');
    const target = { id: 'cell-target', rect: { current: rect(300, 400) }, data: { current: {} }, disabled: false, node: { current: null } };
    const other = { id: 'cell-other', rect: { current: rect(300, 100) }, data: { current: {} }, disabled: false, node: { current: null } };
    const droppableRects = new Map([['cell-target', rect(300, 400)], ['cell-other', rect(300, 100)]]);
    // the dragged card (collisionRect) overlaps the OTHER cell, the pointer is inside the TARGET
    const hit = detect!({
      active: { id: 'card', data: { current: {} }, rect: { current: { initial: null, translated: null } } },
      collisionRect: rect(300, 90, 80, 40),
      droppableRects,
      droppableContainers: [other, target],
      pointerCoordinates: { x: 340, y: 430 },
    });
    expect(hit[0]?.id).toBe('cell-target');
    // no pointer inside any cell: falls back to rectIntersection (the dragged rect over the other cell)
    const fallback = detect!({
      active: { id: 'card', data: { current: {} }, rect: { current: { initial: null, translated: null } } },
      collisionRect: rect(300, 100, 80, 40),
      droppableRects,
      droppableContainers: [other, target],
      pointerCoordinates: { x: 10, y: 10 },
    });
    expect(fallback[0]?.id).toBe('cell-other');
  });
});

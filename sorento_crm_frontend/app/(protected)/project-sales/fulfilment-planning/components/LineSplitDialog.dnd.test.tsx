/**
 * OI-LINE-SPLIT (real-browser findings, kept for v4): a drop near the bottom rows of the
 * scrolling sheet must not land one row lower, and the autoscroll edge band must be thin and
 * slow. The sheet's DndContext matches the POINTER (`pointerWithin`, falling back to
 * `rectIntersection`), measures droppables while dragging (`MeasuringStrategy.Always`), and
 * wires mouse (5 px), touch (long-press) and keyboard sensors (UAC A8). Pinned by capturing
 * the props handed to DndContext.
 */
import React from 'react';
import { screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ownerCase, renderDialog, sheet } from '@/test-mocks/lineSplitSheetKit';

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
vi.mock('@/app/(protected)/project-sales/_shared/services/lineSplitService', () => ({
  getLineSplits: (...a: unknown[]) => getLineSplits(...a),
  saveLineSplits: (...a: unknown[]) => saveLineSplits(...a),
}));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
}));

const rect = (left: number, top: number, w = 90, h = 60) => ({
  left, top, right: left + w, bottom: top + h, width: w, height: h,
});

beforeEach(() => {
  vi.clearAllMocks();
  dnd.props.length = 0;
  getLineSplits.mockResolvedValue(structuredClone(ownerCase()));
});

async function context() {
  await renderDialog();
  await sheet();
  expect(screen.getByRole('table', { name: 'Split delivery sheet' })).toBeInTheDocument();
  return dnd.props.find((p) => 'autoScroll' in p || p.collisionDetection || p.measuring) ?? dnd.props[0];
}

describe('split sheet drag configuration', () => {
  it('has a thin, slow autoscroll edge zone so a drop near an edge does not drift', async () => {
    const ctx = await context();
    const auto = ctx.autoScroll as
      | { threshold?: { x?: number; y?: number }; acceleration?: number }
      | boolean
      | undefined;
    expect(typeof auto === 'object' && auto !== null, 'autoScroll is configured').toBe(true);
    const cfg = auto as { threshold?: { x?: number; y?: number }; acceleration?: number };
    expect(cfg.threshold?.x ?? 0.2).toBeLessThanOrEqual(0.1);
    expect(cfg.threshold?.y ?? 0.2).toBeLessThanOrEqual(0.1);
    expect(cfg.acceleration ?? 10).toBeLessThanOrEqual(5);
  });

  it('wires a mouse sensor after 5 px, a touch long-press and the keyboard', async () => {
    const ctx = await context();
    const sensors = (ctx.sensors ?? []) as Array<{
      sensor: { name?: string };
      options?: { activationConstraint?: { distance?: number; delay?: number } };
    }>;
    expect(sensors.length).toBeGreaterThanOrEqual(3);
    expect(
      sensors.some((s) => s.options?.activationConstraint?.distance === 5),
      'a sensor activates after 5 px',
    ).toBe(true);
    expect(
      sensors.some((s) => (s.options?.activationConstraint?.delay ?? 0) > 0),
      'a sensor activates on a long-press',
    ).toBe(true);
    expect(
      sensors.some((s) => /keyboard/i.test(String(s.sensor?.name ?? ''))),
      'the keyboard sensor is wired',
    ).toBe(true);
  });

  it('uses pointer-based collision detection and measures droppables while dragging', async () => {
    const { MeasuringStrategy } = await import('@dnd-kit/core');
    const ctx = await context();
    const measuring = ctx.measuring as { droppable?: { strategy?: unknown } } | undefined;
    expect(measuring?.droppable?.strategy).toBe(MeasuringStrategy.Always);

    const detect = ctx.collisionDetection as
      | ((args: Record<string, unknown>) => Array<{ id: unknown }>)
      | undefined;
    expect(typeof detect).toBe('function');
    const target = { id: 'cell-target', rect: { current: rect(300, 400) }, data: { current: {} }, disabled: false, node: { current: null } };
    const other = { id: 'cell-other', rect: { current: rect(300, 100) }, data: { current: {} }, disabled: false, node: { current: null } };
    const droppableRects = new Map([['cell-target', rect(300, 400)], ['cell-other', rect(300, 100)]]);
    // the dragged handle (collisionRect) overlaps the OTHER block, the pointer is inside the TARGET
    const hit = detect!({
      active: { id: 'card', data: { current: {} }, rect: { current: { initial: null, translated: null } } },
      collisionRect: rect(300, 90, 80, 40),
      droppableRects,
      droppableContainers: [other, target],
      pointerCoordinates: { x: 340, y: 430 },
    });
    expect(hit[0]?.id).toBe('cell-target');
    // no pointer inside any block: falls back to rectIntersection
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

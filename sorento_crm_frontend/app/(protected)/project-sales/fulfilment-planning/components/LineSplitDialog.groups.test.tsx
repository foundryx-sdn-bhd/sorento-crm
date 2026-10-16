/**
 * OI-LINE-SPLIT review round 1: a big order (about 40 lines in 4 item groups) renders the
 * focused line's group first and expanded, the other groups collapsed behind a header
 * (item, location, line count, Split / Gap badges), expanding on click. A card only moves
 * inside its own group.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

const { getLineSplits, saveLineSplits } = vi.hoisted(() => ({
  getLineSplits: vi.fn(),
  saveLineSplits: vi.fn(),
}));
vi.mock('../../_shared/services/lineSplitService', () => ({
  getLineSplits: (...args: unknown[]) => getLineSplits(...args),
  saveLineSplits: (...args: unknown[]) => saveLineSplits(...args),
}));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }),
}));

const PSO = 'pso-big';
const ITEMS = ['AAA', 'BBB', 'CCC', 'DDD'];
const key = (item: string) => `line-split-group-${item}-BRW-BB`;

function line(item: string, n: number, splits: unknown[] = []) {
  const id = `${item.toLowerCase()}-${n}`;
  return {
    line_id: id,
    line_no: n,
    item_code: item,
    location: 'BRW-BB',
    base_qty: 10,
    base_date: '2026-11-01',
    now_qty: 10,
    now_date: '2026-11-01',
    pending_change: false,
    delivered_qty: 0,
    splits,
  };
}

function payload(withGap = true) {
  const lines = ITEMS.flatMap((item) =>
    Array.from({ length: 10 }, (_, i) => {
      const n = i + 1;
      if (item === 'BBB' && n === 1) {
        return line(item, n, [
          { id: 'sp-b1-a', qty: 4, delivery_date: '2026-11-01', is_cancel: false, sits_on_line_id: 'bbb-1', remark: '' },
          { id: 'sp-b1-b', qty: 6, delivery_date: '2026-11-08', is_cancel: false, sits_on_line_id: 'bbb-1', remark: '' },
        ]);
      }
      if (withGap && item === 'DDD' && n === 1) {
        return line(item, n, [
          { id: 'sp-d1', qty: 4, delivery_date: '2026-11-01', is_cancel: false, sits_on_line_id: 'ddd-1', remark: '' },
        ]);
      }
      return line(item, n);
    }),
  );
  return { pso_id: PSO, so_number: 'SOBIG', customer: 'C', project: 'P', lines };
}

async function renderDialog(focusLineId: string) {
  const path = './LineSplitDialog';
  const mod = (await import(/* @vite-ignore */ path)) as {
    LineSplitDialog: React.ComponentType<{
      open: boolean;
      psoId: string;
      focusLineId?: string;
      onOpenChange: (o: boolean) => void;
    }>;
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <mod.LineSplitDialog open psoId={PSO} focusLineId={focusLineId} onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getLineSplits.mockResolvedValue(payload());
  saveLineSplits.mockResolvedValue(payload());
});

describe('LineSplitDialog groups', () => {
  it('renders the focused line group first and expanded, the others collapsed', async () => {
    await renderDialog('ccc-3');
    await screen.findByTestId(key('CCC'));
    const order = screen
      .getAllByTestId(/^line-split-group-/)
      .map((el) => el.getAttribute('data-testid'));
    expect(order).toEqual([key('CCC'), key('AAA'), key('BBB'), key('DDD')]);
    expect(screen.getByTestId(key('CCC'))).toHaveAttribute('aria-expanded', 'true');
    for (const item of ['AAA', 'BBB', 'DDD']) {
      expect(screen.getByTestId(key(item))).toHaveAttribute('aria-expanded', 'false');
    }
    expect(screen.getByTestId('line-split-row-ccc-3')).toBeInTheDocument();
    expect(screen.queryByTestId('line-split-row-aaa-1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('line-split-row-bbb-1')).not.toBeInTheDocument();
  });

  it('a collapsed header names the item, location and line count, with Split and Gap badges', async () => {
    await renderDialog('ccc-3');
    await screen.findByTestId(key('CCC'));
    const aaa = screen.getByTestId(key('AAA')).textContent ?? '';
    expect(aaa).toMatch(/AAA/);
    expect(aaa).toMatch(/BRW-BB/);
    expect(aaa).toMatch(/10 lines/);
    expect(aaa).not.toMatch(/Split|Gap/);
    expect(screen.getByTestId(key('BBB')).textContent ?? '').toMatch(/Split/);
    expect(screen.getByTestId(key('DDD')).textContent ?? '').toMatch(/Gap/);
  });

  it('clicking a collapsed header expands it', async () => {
    await renderDialog('ccc-3');
    await screen.findByTestId(key('CCC'));
    fireEvent.click(screen.getByTestId(key('AAA')));
    await waitFor(() => expect(screen.getByTestId(key('AAA'))).toHaveAttribute('aria-expanded', 'true'));
    expect(screen.getByTestId('line-split-row-aaa-1')).toBeInTheDocument();
  });

  it('a keyboard move stays inside the card group', async () => {
    // no gap anywhere, so Save is available
    getLineSplits.mockResolvedValue(payload(false));
    await renderDialog('ccc-3');
    const first = await screen.findByTestId('line-split-card-own:ccc-1');
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    const last = await screen.findByTestId('line-split-card-own:ccc-10');
    last.focus();
    fireEvent.keyDown(last, { key: 'ArrowDown' }); // past the last line: the New line row, never another group
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(saveLineSplits).toHaveBeenCalledTimes(1));
    const body = saveLineSplits.mock.calls[0][1];
    const ccc1 = body.lines.find((l: { line_id: string }) => l.line_id === 'ccc-1');
    expect(ccc1.splits[0].sits_on_line_id).toBe('ccc-2');
    for (const l of body.lines) {
      for (const s of l.splits) {
        if (l.line_id.startsWith('ccc') && s.sits_on_line_id !== null) {
          expect(String(s.sits_on_line_id)).toMatch(/^ccc-/);
        }
      }
    }
  });
});

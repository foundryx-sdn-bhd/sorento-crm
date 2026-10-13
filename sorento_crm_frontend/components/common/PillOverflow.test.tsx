/**
 * S3b (R-J), AC-3b.1/AC-3b.2: "the cell shows as many as its width fits and folds the rest
 * into a '+N' pill; clicking any pill, '+N' included, opens the composition."
 *
 * jsdom lays everything out at width 0, which is why the adopting suites
 * (`BoardCellBreakdownDialog.test.tsx`, `CellStockTable.test.tsx`) all read "only the first
 * pill shows; the rest folds behind '+N'" - they never exercise the FIT math itself. This
 * file does: `offsetWidth` and `clientWidth` are stubbed per element so the measuring row's
 * numbers are real, and `ResizeObserver` is replaced with one that hands back its own
 * callback so a live column-resize can be simulated without a second render.
 *
 * Every pill query below is scoped to the VISIBLE row (`within(screen.getByTestId(testId))`)
 * - the hidden measuring row repeats the same label text off-screen so it can be measured,
 * and a bare `screen.getByText` finds that copy too (the same trap the adopting suites'
 * comments already document).
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PillOverflow, type PillItem } from './PillOverflow';

const ITEMS: PillItem[] = [
  { key: 'a', label: 'AAA' },
  { key: 'b', label: 'BBB' },
  { key: 'c', label: 'CCC' },
];

function renderRow(items: PillItem[]) {
  return (
    <div>
      {items.map((item) => (
        <p key={item.key} data-testid={`popover-item-${item.key}`}>
          {item.label}
        </p>
      ))}
    </div>
  );
}

// --------------------------------------------------------------- measurement stubs

let containerWidth = 420;
/** The uniform width every pill measures at, unless overridden by its own label below. */
let pillWidth = 100;
/** What the hidden measuring row's own "+N" (the widest possible one) measures at. */
let overflowWidth = 60;
/** Per-label overrides, for the one pill a test needs to measure differently. */
let widthOverrides: Record<string, number> = {};

const originalOffsetWidth = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  'offsetWidth',
);
const originalClientWidth = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  'clientWidth',
);

function stubMeasurements() {
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
    configurable: true,
    get(this: HTMLElement) {
      const text = this.textContent ?? '';
      if (text in widthOverrides) return widthOverrides[text];
      if (text.startsWith('+')) return overflowWidth;
      return pillWidth;
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get() {
      return containerWidth;
    },
  });
}

/** Captures the `ResizeObserver` callback so a test can fire it directly, the way a real
 * column-resize drag would (AC-3b.1: "dragging the column border reflows without a reload"). */
class CapturingResizeObserver {
  static instances: CapturingResizeObserver[] = [];
  callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    CapturingResizeObserver.instances.push(this);
  }
  observe() {}
  unobserve() {}
  disconnect() {}
  trigger() {
    this.callback([] as unknown as ResizeObserverEntry[], this as unknown as ResizeObserver);
  }
}

beforeEach(() => {
  containerWidth = 420;
  pillWidth = 100;
  overflowWidth = 60;
  widthOverrides = {};
  CapturingResizeObserver.instances = [];
  stubMeasurements();
  vi.stubGlobal('ResizeObserver', CapturingResizeObserver);
});

afterEach(() => {
  if (originalOffsetWidth) {
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', originalOffsetWidth);
  }
  if (originalClientWidth) {
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', originalClientWidth);
  }
  vi.unstubAllGlobals();
});

function renderPills() {
  render(
    <PillOverflow
      items={ITEMS}
      ariaLabel="Sourced from"
      testId="pills"
      renderPopover={renderRow}
    />,
  );
  return within(screen.getByTestId('pills'));
}

describe('PillOverflow: fitting pills to the container it measures', () => {
  it('renders every pill, and no "+N", when the container fits them all (AC-3b.1)', () => {
    containerWidth = 420;
    const row = renderPills();

    expect(row.getByText('AAA')).toBeInTheDocument();
    expect(row.getByText('BBB')).toBeInTheDocument();
    expect(row.getByText('CCC')).toBeInTheDocument();
    expect(row.queryByText(/^\+/)).not.toBeInTheDocument();
  });

  it('folds what does not fit into one "+N" pill in a narrow column (AC-3b.1)', () => {
    containerWidth = 160;
    const row = renderPills();

    expect(row.getByText('AAA')).toBeInTheDocument();
    expect(row.queryByText('BBB')).not.toBeInTheDocument();
    expect(row.queryByText('CCC')).not.toBeInTheDocument();
    expect(row.getByText('+2')).toBeInTheDocument();
  });

  it('leaves room for "+N" on the same line rather than wrapping it under the pills', () => {
    // Two 100px pills fit 260px (204), but not with a 60px "+1" beside them (268): one pill
    // and "+2" keep the row to one line (Project Sales brands, owner 13 Oct).
    containerWidth = 260;
    const row = renderPills();

    expect(row.getByText('AAA')).toBeInTheDocument();
    expect(row.queryByText('BBB')).not.toBeInTheDocument();
    expect(row.getByText('+2')).toBeInTheDocument();
  });

  it('reflows to the whole set once the observed container grows, with no remount (AC-3b.1)', async () => {
    containerWidth = 160;
    const row = renderPills();
    expect(row.getByText('+2')).toBeInTheDocument();

    containerWidth = 420;
    expect(CapturingResizeObserver.instances).toHaveLength(1);
    act(() => {
      CapturingResizeObserver.instances[0].trigger();
    });

    await waitFor(() => {
      expect(row.getByText('BBB')).toBeInTheDocument();
      expect(row.getByText('CCC')).toBeInTheDocument();
      expect(row.queryByText(/^\+/)).not.toBeInTheDocument();
    });
  });

  it('never truncates the first pill\'s text, even when it alone overflows the container (S3b fix)', () => {
    // A quantity is never cut: pill 0 always renders whole, and "+N" wraps to its own line
    // instead of pill 0's text being capped below its content to make room for it.
    containerWidth = 200;
    widthOverrides = { AAA: 500 };
    const row = renderPills();

    const firstPill = row.getByText('AAA');
    expect(firstPill).toHaveTextContent('AAA');
    expect(firstPill.style.maxWidth).toBe('');
    expect(row.getByText('+2')).toBeInTheDocument();
  });
});

describe('PillOverflow: one popover for the whole row (AC-3b.2)', () => {
  it('opens the popover from "+N" with every item, not only the folded ones', async () => {
    containerWidth = 160;
    renderPills();

    fireEvent.click(screen.getByText('+2'));

    const popover = await screen.findByTestId('pills-popover');
    expect(within(popover).getByTestId('popover-item-a')).toBeInTheDocument();
    expect(within(popover).getByTestId('popover-item-b')).toBeInTheDocument();
    expect(within(popover).getByTestId('popover-item-c')).toBeInTheDocument();
  });

  it('opens the very same popover from a plain, visible pill', async () => {
    containerWidth = 160;
    const row = renderPills();

    fireEvent.click(row.getByText('AAA'));

    const popover = await screen.findByTestId('pills-popover');
    expect(within(popover).getByTestId('popover-item-a')).toBeInTheDocument();
    expect(within(popover).getByTestId('popover-item-b')).toBeInTheDocument();
    expect(within(popover).getByTestId('popover-item-c')).toBeInTheDocument();
  });

  it('closes on Escape, without expanding whatever row it sits inside', async () => {
    containerWidth = 160;
    renderPills();

    fireEvent.click(screen.getByText('+2'));
    const popover = await screen.findByTestId('pills-popover');

    fireEvent.keyDown(popover, { key: 'Escape' });

    await waitFor(() =>
      expect(screen.queryByTestId('pills-popover')).not.toBeInTheDocument(),
    );
  });

  it('is keyboard reachable: Enter on a pill opens the popover', async () => {
    containerWidth = 420;
    const row = renderPills();
    const pill = row.getByText('AAA');
    expect(pill).toHaveAttribute('tabindex', '0');

    pill.focus();
    fireEvent.keyDown(pill, { key: 'Enter' });

    expect(await screen.findByTestId('pills-popover')).toBeInTheDocument();
  });

  it('is keyboard reachable: Space on a pill opens the popover', async () => {
    containerWidth = 420;
    const row = renderPills();
    const pill = row.getByText('BBB');

    pill.focus();
    fireEvent.keyDown(pill, { key: ' ' });

    expect(await screen.findByTestId('pills-popover')).toBeInTheDocument();
  });
});

describe('PillOverflow: the click never reaches the row behind it', () => {
  it('stops a pill click from reaching a parent onClick (a board cell button, say)', () => {
    containerWidth = 420;
    const onParentClick = vi.fn();
    render(
      <div onClick={onParentClick}>
        <PillOverflow
          items={ITEMS}
          ariaLabel="Sourced from"
          testId="pills"
          renderPopover={renderRow}
        />
      </div>,
    );

    fireEvent.click(within(screen.getByTestId('pills')).getByText('AAA'));

    expect(onParentClick).not.toHaveBeenCalled();
  });
});

describe('PillOverflow: re-measures only on a real change, never on every render (nit, code review round 3 batch 2)', () => {
  it('does not touch offsetWidth again on a re-render that rebuilds items with the same content', () => {
    containerWidth = 420;
    let measureCalls = 0;
    const stubbed = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth')!;
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
      configurable: true,
      get(this: HTMLElement) {
        measureCalls += 1;
        return stubbed.get!.call(this);
      },
    });

    // A FRESH array literal every render - the exact shape every real call site (the
    // board cell, the contributing-lines column, the Stock tab's Taken cell) builds.
    function Harness({ salt }: { salt: string }) {
      const items: PillItem[] = [
        { key: 'a', label: 'AAA' },
        { key: 'b', label: 'BBB' },
        { key: 'c', label: 'CCC' },
      ];
      void salt;
      return (
        <PillOverflow
          items={items}
          ariaLabel="Sourced from"
          testId="pills"
          renderPopover={renderRow}
        />
      );
    }

    const { rerender } = render(<Harness salt="one" />);
    const afterFirstRender = measureCalls;
    expect(afterFirstRender).toBeGreaterThan(0);

    // A re-render with a brand-new `items` array reference, same key/label content.
    rerender(<Harness salt="two" />);

    expect(measureCalls).toBe(afterFirstRender);
  });

  it('still re-measures once a pill’s label actually changes', () => {
    containerWidth = 420;
    const { rerender } = render(
      <PillOverflow
        items={[{ key: 'a', label: 'AAA' }]}
        ariaLabel="Sourced from"
        testId="pills"
        renderPopover={renderRow}
      />,
    );
    expect(within(screen.getByTestId('pills')).getByText('AAA')).toBeInTheDocument();

    rerender(
      <PillOverflow
        items={[{ key: 'a', label: 'AAA 25' }]}
        ariaLabel="Sourced from"
        testId="pills"
        renderPopover={renderRow}
      />,
    );

    expect(within(screen.getByTestId('pills')).getByText('AAA 25')).toBeInTheDocument();
  });
});

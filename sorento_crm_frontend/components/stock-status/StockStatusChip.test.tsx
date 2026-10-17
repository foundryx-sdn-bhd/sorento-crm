/**
 * The list chip (UAC AC-20): two facts on two chips, a page of chips sharing ONE summary read,
 * and the panel only once the chip is opened.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// One layer down, at the fetch: the batching loader calls the summary read inside its own
// module, so this is where "one request per page" is observable.
const apiFetchMock = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (url: string) => apiFetchMock(url) }));
const panelSpy = vi.fn();
vi.mock('./StockStatusPanel', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./StockStatusPanel')>();
  return {
    ...actual,
    StockStatusPanel: (props: Record<string, unknown>) => {
      panelSpy(props);
      return <div data-testid="panel" />;
    },
  };
});

import {
  StockStatusChip,
  resetStockStatusChipsForTests,
} from './StockStatusChip';

function renderChips() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <StockStatusChip productId="a" itemCode="SRTWB1515" lineIds={['l1']} />
      <StockStatusChip productId="b" itemCode="CWCX605-RL" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // The open chip lives in a module store: each test starts with none open.
  resetStockStatusChipsForTests();
  apiFetchMock.mockReset();
  panelSpy.mockReset();
  apiFetchMock.mockResolvedValue(
    new Response(
      JSON.stringify([
        { product_id: 'a', on_hand: 499, so_qty: 10390 },
        { product_id: 'b', on_hand: 1030, so_qty: 12081 },
      ]),
      { status: 200 },
    ),
  );
});

describe('StockStatusChip', () => {
  it('reads every chip on the page in one request and shows On hand and SO as two chips', async () => {
    renderChips();
    await waitFor(() => expect(screen.getByText('499')).toBeInTheDocument());
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    const url = new URL(apiFetchMock.mock.calls[0][0], 'http://x');
    expect(url.pathname).toBe('/api/v1/inventory/stock-status/summary');
    expect(url.searchParams.get('product_ids')?.split(',').sort()).toEqual([
      'a',
      'b',
    ]);
    const chip = screen.getByRole('button', {
      name: 'Stock status of SRTWB1515',
    });
    expect(chip.textContent).toContain('On hand');
    expect(chip.textContent).toContain('-10,390');
    expect(chip.textContent).not.toContain('·');
  });

  it('opens the panel for its item, this line marked, only when clicked', async () => {
    renderChips();
    expect(panelSpy).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole('button', { name: 'Stock status of SRTWB1515' }),
    );
    expect(await screen.findByTestId('panel')).toBeInTheDocument();
    expect(panelSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({
        productId: 'a',
        lineIds: ['l1'],
        showOpenFullPage: true,
      }),
    );
  });

  // Crew audit, fulfilment board: a list rebuilds its columns when an input changes, and
  // TanStack then remounts every cell. The first click after load lost its popover that way.
  function Remountable({
    generation,
    productId = 'a',
  }: {
    generation: number;
    productId?: string;
  }) {
    return (
      <StockStatusChip
        key={generation}
        productId={productId}
        itemCode="SRTWB1515"
        lineIds={['l1']}
      />
    );
  }
  function renderRemountable() {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={client}>
        <Remountable generation={1} />
      </QueryClientProvider>,
    );
    const remount = (generation: number, ui?: React.ReactElement) =>
      view.rerender(
        <QueryClientProvider client={client}>
          {ui ?? <Remountable generation={generation} />}
        </QueryClientProvider>,
      );
    return { remount };
  }

  it('stays open when its cell is remounted (the board rebuilding its columns)', async () => {
    const { remount } = renderRemountable();
    await userEvent.click(
      screen.getByRole('button', { name: 'Stock status of SRTWB1515' }),
    );
    expect(await screen.findByTestId('panel')).toBeInTheDocument();
    remount(2);
    expect(await screen.findByTestId('panel')).toBeInTheDocument();
  });

  it('stays closed across a remount once closed, and a chip gone for good does not reopen later', async () => {
    const { remount } = renderRemountable();
    await userEvent.click(
      screen.getByRole('button', { name: 'Stock status of SRTWB1515' }),
    );
    await screen.findByTestId('panel');
    await userEvent.keyboard('{Escape}');
    await waitFor(() =>
      expect(screen.queryByTestId('panel')).not.toBeInTheDocument(),
    );
    remount(2);
    expect(screen.queryByTestId('panel')).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole('button', { name: 'Stock status of SRTWB1515' }),
    );
    await screen.findByTestId('panel');
    remount(3, <div />); // the page moved on: the chip is gone
    await new Promise((resolve) => setTimeout(resolve, 20));
    remount(4);
    expect(screen.queryByTestId('panel')).not.toBeInTheDocument();
  });

  it('two chips for the same product on two rows: a click opens exactly that one (reviewer B1)', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <StockStatusChip productId="a" itemCode="SRTWB1515" openKey="row-1" />
        <StockStatusChip productId="a" itemCode="SRTWB1515" openKey="row-2" />
      </QueryClientProvider>,
    );
    const [first, second] = screen.getAllByRole('button', {
      name: 'Stock status of SRTWB1515',
    });
    await userEvent.click(first);
    await waitFor(() => expect(screen.getAllByTestId('panel')).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getAllByTestId('panel')).toHaveLength(1);
    expect(first).toHaveAttribute('aria-expanded', 'true');
    expect(second).toHaveAttribute('aria-expanded', 'false');
  });

  // Reviewer S1: a fixed floor on the height pushed a mid-screen popover off a small screen.
  // The chip picks the side with more room when the room below is short, and the height
  // stays capped at what that side has, so it can never clip.
  async function openAt(top: number, viewportHeight: number) {
    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: viewportHeight,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <StockStatusChip productId="a" itemCode="SRTWB1515" openKey="row-1" />
      </QueryClientProvider>,
    );
    const chip = screen.getByRole('button', {
      name: 'Stock status of SRTWB1515',
    });
    chip.getBoundingClientRect = () =>
      ({
        top,
        bottom: top + 20,
        left: 300,
        right: 400,
        width: 100,
        height: 20,
        x: 300,
        y: top,
      }) as DOMRect;
    await userEvent.click(chip);
    await screen.findByTestId('panel');
    return document.querySelector('[data-stock-status-popover]') as HTMLElement;
  }

  it('opens above a chip low on the screen, below one high on it', async () => {
    expect((await openAt(665, 900)).dataset.requestedSide).toBe('top');
  });

  it('opens below a chip with room under it', async () => {
    expect((await openAt(150, 900)).dataset.requestedSide).toBe('bottom');
  });

  it('caps the height at the room on its side, no floor that could push it off screen', async () => {
    // The surface is the inner element (PopoverContent animates a child, Radix positions the outer).
    const content = (await openAt(333, 667)).firstElementChild as HTMLElement;
    expect(content.className).toContain(
      'max-h-(--radix-popover-content-available-height)',
    );
    expect(content.className).not.toMatch(/28rem/);
    expect((content.parentElement as HTMLElement).dataset.requestedSide).toBe(
      'top',
    );
  });
});

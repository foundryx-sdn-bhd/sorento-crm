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

import { StockStatusChip } from './StockStatusChip';

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
});

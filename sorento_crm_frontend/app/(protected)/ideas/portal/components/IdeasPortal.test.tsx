/**
 * IDEA-CRM-SIDE Phase 2 red tests for the portal screen `IdeasPortal` (AC-E-02..E-05).
 *
 * Contract: `<IdeasPortal />` (no props) reads `?tab=` (`roadmap` default, `feedback`,
 * `changelog`), renders Roadmap / Feedback / Changelog as line tabs and talks only to
 * `@/services/ideasPortalService` (the seam mocked here).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const nav = vi.hoisted(() => ({ search: '', push: vi.fn(), replace: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace, back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/ideas/portal',
  useSearchParams: () => new URLSearchParams(nav.search),
}));
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { name: 'Pat Staff' } }, status: 'authenticated' }),
}));
vi.mock('@/hooks/usePermissions', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useHasPermission: () => false };
});
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() } }));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const svc = vi.hoisted(() => ({
  getRoadmap: vi.fn(),
  listFeedback: vi.fn(),
  getChangelog: vi.fn(),
  getPortalIdea: vi.fn(),
  getVoters: vi.fn(),
  getActivity: vi.fn(),
  listPortalComments: vi.fn(),
  togglePortalVote: vi.fn(),
  postPortalComment: vi.fn(),
  likePortalComment: vi.fn(),
  setPortalHidden: vi.fn(),
  setPortalNote: vi.fn(),
}));
vi.mock('@/services/ideasPortalService', () => svc);

import { IdeasPortal } from './IdeasPortal';

function card(over: Record<string, unknown> = {}) {
  return {
    id: 'idea-1',
    ideaNumber: 'IDEA-0042',
    title: 'Faster quotes',
    statusLabel: 'Planned',
    statusColor: 'blue',
    upvotes: 3,
    myVote: false,
    commentCount: 1,
    ...over,
  };
}

const ROADMAP = {
  columns: [
    {
      key: 'planned',
      title: 'Planned',
      color: 'blue',
      count: 5,
      ideas: [card(), card({ id: 'idea-2', ideaNumber: 'IDEA-0043', title: 'Quote templates', upvotes: 4 })],
    },
    { key: 'shipped', title: 'Shipped', color: 'green', count: 0, ideas: [] },
  ],
};

function renderPortal(search = '') {
  nav.search = search;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IdeasPortal />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  svc.getRoadmap.mockResolvedValue(ROADMAP);
  svc.listFeedback.mockResolvedValue({ items: [card()], total: 1, page: 1, pageSize: 25 });
  svc.getChangelog.mockResolvedValue({ items: [] });
});

describe('AC-E-02 line tabs and ?tab=', () => {
  it('has the tabs Roadmap, Feedback and Changelog', async () => {
    renderPortal();
    for (const name of ['Roadmap', 'Feedback', 'Changelog']) {
      expect(await screen.findByRole('tab', { name })).toBeInTheDocument();
    }
  });

  it('opens on Roadmap by default', async () => {
    renderPortal();
    expect(await screen.findByRole('tab', { name: 'Roadmap' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(svc.getRoadmap).toHaveBeenCalled());
  });

  it.each([
    ['feedback', 'Feedback'],
    ['changelog', 'Changelog'],
    ['roadmap', 'Roadmap'],
  ])('?tab=%s opens the %s tab', async (key, name) => {
    renderPortal(`tab=${key}`);
    expect(await screen.findByRole('tab', { name })).toHaveAttribute('aria-selected', 'true');
  });

  it('an unknown ?tab= falls back to Roadmap', async () => {
    renderPortal('tab=nonsense');
    expect(await screen.findByRole('tab', { name: 'Roadmap' })).toHaveAttribute('aria-selected', 'true');
  });

  it('choosing a tab writes it to the URL so a refresh keeps it', async () => {
    renderPortal();
    fireEvent.click(await screen.findByRole('tab', { name: 'Feedback' }));
    await waitFor(() => expect(nav.replace.mock.calls.length + nav.push.mock.calls.length).toBeGreaterThan(0));
    const urls = [...nav.replace.mock.calls, ...nav.push.mock.calls].map((c) => String(c[0]));
    expect(urls.some((u) => /tab=feedback/.test(u))).toBe(true);
  });
});

describe('AC-E-03 roadmap', () => {
  it('renders one column per ss column with its title and count', async () => {
    renderPortal();
    const planned = await screen.findByText('Planned');
    const shipped = screen.getByText('Shipped');
    expect(planned.parentElement!.textContent).toContain('5');
    expect(shipped.parentElement!.textContent).toContain('0');
  });

  it('cards link to /ideas/portal/{id}', async () => {
    renderPortal();
    expect(await screen.findByRole('link', { name: /Faster quotes/ })).toHaveAttribute('href', '/ideas/portal/idea-1');
    expect(screen.getByRole('link', { name: /Quote templates/ })).toHaveAttribute('href', '/ideas/portal/idea-2');
  });

  it('a card shows its idea number', async () => {
    renderPortal();
    expect(await screen.findByText('IDEA-0042')).toBeInTheDocument();
    expect(screen.getByText('IDEA-0043')).toBeInTheDocument();
  });

  it('an error shows a message, not an empty board', async () => {
    svc.getRoadmap.mockRejectedValue(new Error("The Ideas workspace isn't reachable right now."));
    renderPortal();
    expect(await screen.findByText(/isn't reachable right now/)).toBeInTheDocument();
    expect(screen.queryByText('Planned')).toBeNull();
  });
});

describe('AC-E-04 feedback', () => {
  it('lists feedback cards linking to the card page', async () => {
    renderPortal('tab=feedback');
    expect(await screen.findByRole('link', { name: /Faster quotes/ })).toHaveAttribute('href', '/ideas/portal/idea-1');
  });

  it('empty: "No feedback yet"', async () => {
    svc.listFeedback.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 25 });
    renderPortal('tab=feedback');
    expect(await screen.findByText('No feedback yet')).toBeInTheDocument();
  });

  it('Top is the default sort; choosing New sends sort=new', async () => {
    renderPortal('tab=feedback');
    await screen.findByRole('link', { name: /Faster quotes/ });
    expect(svc.listFeedback.mock.calls[0][0]).toMatchObject({ sort: 'top' });
    fireEvent.click(screen.getByText('New'));
    await waitFor(() => expect(svc.listFeedback).toHaveBeenLastCalledWith(expect.objectContaining({ sort: 'new' })));
  });

  it('Mine sends mine=true', async () => {
    renderPortal('tab=feedback');
    await screen.findByRole('link', { name: /Faster quotes/ });
    fireEvent.click(screen.getByText('Mine'));
    await waitFor(() => expect(svc.listFeedback).toHaveBeenLastCalledWith(expect.objectContaining({ mine: true })));
  });

  it('typing in search sends q', async () => {
    renderPortal('tab=feedback');
    await screen.findByRole('link', { name: /Faster quotes/ });
    fireEvent.change(screen.getByPlaceholderText(/Search/i), { target: { value: 'quote' } });
    await waitFor(() => expect(svc.listFeedback).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'quote' })), {
      timeout: 2000,
    });
  });
});

describe('AC-E-05 changelog', () => {
  it('lists delivered items with a dd/MM/yyyy date and the note', async () => {
    svc.getChangelog.mockResolvedValue({
      items: [{ ...card({ statusLabel: 'Shipped' }), deliveredAt: '2026-10-05T12:00:00Z', note: 'Now live for all dealers' }],
    });
    renderPortal('tab=changelog');
    expect(await screen.findByText('Faster quotes')).toBeInTheDocument();
    expect(screen.getByText('05/10/2026')).toBeInTheDocument();
    expect(screen.getByText('Now live for all dealers')).toBeInTheDocument();
  });

  it('empty: "Nothing shipped yet"', async () => {
    renderPortal('tab=changelog');
    expect(await screen.findByText('Nothing shipped yet')).toBeInTheDocument();
  });

  it('does not fetch the changelog while another tab is open', async () => {
    renderPortal();
    await screen.findByText('Planned');
    expect(svc.getChangelog).not.toHaveBeenCalled();
    expect(within(document.body).queryByText('Nothing shipped yet')).toBeNull();
  });
});

describe('fix round 1 nits', () => {
  it('the tab follows ?tab= when the URL changes under the page', async () => {
    const view = renderPortal();
    expect(await screen.findByRole('tab', { name: 'Roadmap' })).toHaveAttribute('aria-selected', 'true');
    nav.search = 'tab=changelog';
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    view.rerender(
      <QueryClientProvider client={client}>
        <IdeasPortal />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Changelog' })).toHaveAttribute('aria-selected', 'true'));
  });

  it('one tab click writes the URL once', async () => {
    renderPortal();
    const tab = await screen.findByRole('tab', { name: 'Feedback' });
    // Radix selects on mousedown (onValueChange) and the trigger also handles click: both fire here.
    fireEvent.mouseDown(tab, { button: 0, ctrlKey: false });
    fireEvent.click(tab);
    await waitFor(() => expect(nav.replace).toHaveBeenCalled());
    expect(nav.replace).toHaveBeenCalledTimes(1);
  });

  it('a changelog item without a title links with its idea number', async () => {
    svc.getChangelog.mockResolvedValue({
      items: [{ ...card({ title: null }), deliveredAt: '2026-10-05T12:00:00Z', note: null }],
    });
    renderPortal('tab=changelog');
    expect(await screen.findByRole('link', { name: 'IDEA-0042' })).toHaveAttribute('href', '/ideas/portal/idea-1');
  });
});

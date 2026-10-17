/**
 * DirectoryTreeSidebar: deleting the open folder, or an ancestor of it, moves to the deleted
 * folder's parent and REPLACES the history entry, so Back never lands on the deleted folder
 * (FILES-URL-NAV AC-8, crew answer 2a).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DndContext } from '@dnd-kit/core';

const h = vi.hoisted(() => ({
  tree: [
    {
      id: 'mkt',
      name: 'Marketing',
      parent_id: null,
      sort_order: 0,
      created_at: '',
      children: [
        {
          id: 'camp',
          name: 'Campaigns',
          parent_id: 'mkt',
          sort_order: 0,
          created_at: '',
          children: [{ id: 'q1', name: 'Q1', parent_id: 'camp', sort_order: 0, created_at: '', children: [] }],
        },
      ],
    },
    { id: 'tech', name: 'Technical', parent_id: null, sort_order: 1, created_at: '', children: [] },
  ],
  // Delete succeeds at once.
  deleteMutate: (_id: string, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.(),
}));

vi.mock('../../attachments/hooks/useAttachments', () => ({
  useDirectoryTree: () => ({ data: h.tree, isLoading: false, isSuccess: true, isFetching: false }),
  useCreateDirectory: () => ({ mutate: () => {}, isPending: false }),
  useUpdateDirectory: () => ({ mutate: () => {}, isPending: false }),
  useDeleteDirectory: () => ({ mutate: h.deleteMutate, isPending: false }),
}));
vi.mock('@/hooks/useQuickAccess', () => ({
  useQuickAccess: () => ({ data: [] }),
  useAddQuickAccess: () => ({ mutate: () => {} }),
  useRemoveQuickAccess: () => ({ mutate: () => {} }),
}));

import DirectoryTreeSidebar from './DirectoryTreeSidebar';

function renderSidebar(selectedId: string | null, onSelect = vi.fn()) {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <DndContext>
        <DirectoryTreeSidebar selectedId={selectedId} onSelect={onSelect} />
      </DndContext>
    </QueryClientProvider>
  );
  return onSelect;
}

// Opening the row's menu in jsdom also bubbles a click to the row (a plain select, no
// options), so only the navigation calls the delete makes are asserted.
function replaceCalls(onSelect: ReturnType<typeof vi.fn>) {
  return onSelect.mock.calls.filter((c) => c.length > 1);
}

function deleteFolder(name: string) {
  const row = screen.getAllByText(name)[0].closest('.group') as HTMLElement;
  const trigger = within(row).getByRole('button', { name: 'Folder actions' });
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: 'mouse' });
  fireEvent.click(screen.getByRole('menuitem', { name: /Delete/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Move to Trash' }));
}

beforeEach(() => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((q: string) => ({
      matches: false,
      media: q,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  );
});
afterEach(() => vi.unstubAllGlobals());

describe('DirectoryTreeSidebar delete navigation (AC-8)', () => {
  it('deleting the open folder goes to its parent, replacing the history entry', () => {
    const onSelect = renderSidebar('q1');
    deleteFolder('Q1');
    expect(replaceCalls(onSelect)).toEqual([['camp', { replace: true }]]);
  });

  it('deleting an ancestor of the open folder goes to that ancestor\'s parent', () => {
    const onSelect = renderSidebar('q1');
    deleteFolder('Campaigns');
    expect(replaceCalls(onSelect)).toEqual([['mkt', { replace: true }]]);
  });

  it('deleting a top-level folder on the open path goes to the drive root', () => {
    const onSelect = renderSidebar('q1');
    deleteFolder('Marketing');
    expect(replaceCalls(onSelect)).toEqual([[null, { replace: true }]]);
  });

  it('deleting an unrelated folder leaves the open folder alone', () => {
    const onSelect = renderSidebar('q1');
    deleteFolder('Technical');
    expect(replaceCalls(onSelect)).toEqual([]);
  });
});

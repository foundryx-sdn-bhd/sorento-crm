/**
 * AttachmentsInFolderPanel wiring for FILES-URL-NAV: the folder arrives as a prop that
 * follows the URL, so these tests change the prop the way Back / Forward does.
 *  - AC-9: any folder change clears the search box.
 *  - AC-6: a folder the settled tree does not hold shows "Folder not available", never
 *    in Trash, never while the tree is still fetching, and held through a later refetch.
 *  - S1: a drill click does not reset (and so refetch) the old folder before the URL lands.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { DndContext } from '@dnd-kit/core';
import type { AttachmentDirectoryTreeNode } from '../../attachments/services/directoryService';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/resource-management/attachment-directories',
  useSearchParams: () => new URLSearchParams(''),
}));

const h = vi.hoisted(() => {
  const tree = [
    {
      id: 'mkt',
      name: 'Marketing',
      parent_id: null,
      sort_order: 0,
      created_at: '',
      children: [
        { id: 'camp', name: 'Campaigns', parent_id: 'mkt', sort_order: 0, created_at: '', children: [] },
      ],
    },
  ];
  return {
    tree,
    treeState: { data: tree as unknown[], isSuccess: true, isFetching: false, isLoading: false },
    mutation: () => ({ mutate: () => {}, mutateAsync: async () => {}, isPending: false }),
  };
});
const tree = h.tree as AttachmentDirectoryTreeNode[];
vi.mock('../../attachments/hooks/useAttachments', () => ({
  useDriveContents: () => {
    return {
      data: { data: [], pagination: { total: 0, page: 1, limit: 50, totalPages: 0 } },
      isLoading: false,
      isPlaceholderData: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    };
  },
  useDirectoryTree: () => h.treeState,
  useDownloadAttachment: h.mutation,
  useRestoreAttachment: h.mutation,
  useBulkRestoreAttachments: h.mutation,
  useRestoreDirectory: h.mutation,
  useUpdateAttachment: h.mutation,
  useCreateDirectory: h.mutation,
  useUpdateDirectory: h.mutation,
  useDeleteDirectory: h.mutation,
  useMoveDirectory: h.mutation,
  useBulkMoveAttachments: h.mutation,
  useUploadAttachment: h.mutation,
  useBulkImportAttachment: h.mutation,
  useDeleteAttachment: h.mutation,
  useArchiveAttachment: h.mutation,
  useBulkArchiveAttachments: h.mutation,
  useBulkDeleteAttachments: h.mutation,
  usePermanentDeleteDirectory: h.mutation,
  useResubmitAttachmentWebhook: h.mutation,
  useAttachmentTypesList: () => ({ data: [] }),
  useAttachments: () => ({ data: undefined }),
  useReorderAttachments: h.mutation,
}));
vi.mock('../../attachment-types/hooks/useAttachmentTypes', () => ({
  useAttachmentTypes: () => ({ data: { data: [] } }),
}));
vi.mock('@/app/(protected)/user-management/contact-access-types/hooks/useContactAccessTypes', () => ({
  useContactAccessTypes: () => ({ data: [] }),
}));
vi.mock('@/app/providers/CompanyProvider', () => ({
  useCompany: () => ({ grants: [], activeCompanyId: null, companies: [] }),
}));
vi.mock('@/services/userSelectService', () => ({ getUserLookup: async () => [] }));

// The panel's dialogs are closed in every test here; stub them so their own providers
// (upload manager, etc.) are not needed.
vi.mock('../../attachments/components/AttachmentUploadDialog', () => ({ default: () => null }));
vi.mock('../../attachments/components/AttachmentBulkImportDialog', () => ({ default: () => null }));
vi.mock('../../attachments/components/AttachmentBulkDownloadDialog', () => ({ default: () => null }));
vi.mock('../../attachments/components/AttachmentDetailModal', () => ({ default: () => null }));

import AttachmentsInFolderPanel from './AttachmentsInFolderPanel';

function renderPanel(props: { directoryId: string | null; onSelectFolder?: (id: string | null) => void }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (p: typeof props) => (
    <QueryClientProvider client={client}>
      <DndContext>
        <AttachmentsInFolderPanel {...p} />
      </DndContext>
    </QueryClientProvider>
  );
  const utils = render(ui(props));
  return { ...utils, rerenderWith: (p: typeof props) => utils.rerender(ui(p)) };
}

const searchBox = () => screen.getByPlaceholderText('Search files & folders...') as HTMLInputElement;

beforeEach(() => {
  h.treeState = { data: tree, isSuccess: true, isFetching: false, isLoading: false };
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

describe('AttachmentsInFolderPanel folder URL wiring', () => {
  it('AC-9: a folder change from outside (Back / Forward) clears the search box', () => {
    const { rerenderWith } = renderPanel({ directoryId: 'camp', onSelectFolder: vi.fn() });
    fireEvent.change(searchBox(), { target: { value: 'invoice' } });
    expect(searchBox().value).toBe('invoice');
    rerenderWith({ directoryId: 'mkt', onSelectFolder: vi.fn() });
    expect(searchBox().value).toBe('');
  });

  it('S1: a drill click leaves the open folder alone until the URL changes', () => {
    const onSelectFolder = vi.fn();
    renderPanel({ directoryId: 'mkt', onSelectFolder });
    fireEvent.change(searchBox(), { target: { value: 'invoice' } });
    fireEvent.click(screen.getByRole('button', { name: 'All files' }));
    expect(onSelectFolder).toHaveBeenCalledWith(null);
    // Not reset yet: the reset arrives with the new directoryId, not before it.
    expect(searchBox().value).toBe('invoice');
  });

  it('the open folder\'s own crumb still clears the search (nothing else will)', () => {
    const onSelectFolder = vi.fn();
    renderPanel({ directoryId: 'mkt', onSelectFolder });
    fireEvent.change(searchBox(), { target: { value: 'invoice' } });
    fireEvent.click(screen.getByRole('button', { name: 'Marketing' }));
    expect(searchBox().value).toBe('');
  });

  it('AC-6: a folder the settled tree does not hold shows "Folder not available", no grid', () => {
    renderPanel({ directoryId: 'gone', onSelectFolder: vi.fn() });
    expect(screen.getByTestId('folder-unavailable')).toBeInTheDocument();
    expect(screen.getByText('Folder not available')).toBeInTheDocument();
    expect(
      screen.getByText('It may have been deleted, moved to Trash, or you do not have access.')
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Search files & folders...')).toBeNull();
    // The way back is the breadcrumb's Home crumb.
    expect(screen.getByRole('button', { name: 'All files' })).toBeInTheDocument();
  });

  it('AC-6: a folder in the tree renders the normal listing', () => {
    renderPanel({ directoryId: 'camp', onSelectFolder: vi.fn() });
    expect(screen.queryByTestId('folder-unavailable')).toBeNull();
    expect(searchBox()).toBeInTheDocument();
  });

  it('AC-6: no verdict while the tree is still fetching (a just-created folder)', () => {
    h.treeState = { data: tree, isSuccess: true, isFetching: true, isLoading: false };
    renderPanel({ directoryId: 'new-folder', onSelectFolder: vi.fn() });
    expect(screen.queryByTestId('folder-unavailable')).toBeNull();
  });

  it('AC-6: no verdict before the tree has loaded at all', () => {
    h.treeState = { data: [], isSuccess: false, isFetching: true, isLoading: true };
    renderPanel({ directoryId: 'camp', onSelectFolder: vi.fn() });
    expect(screen.queryByTestId('folder-unavailable')).toBeNull();
  });

  it('S2: the unavailable state holds through a later background refetch', () => {
    const { rerenderWith } = renderPanel({ directoryId: 'gone', onSelectFolder: vi.fn() });
    expect(screen.getByTestId('folder-unavailable')).toBeInTheDocument();
    h.treeState = { data: tree, isSuccess: true, isFetching: true, isLoading: false };
    rerenderWith({ directoryId: 'gone', onSelectFolder: vi.fn() });
    expect(screen.getByTestId('folder-unavailable')).toBeInTheDocument();
  });

  it('Trash page (no folder URL): a crumb click still clears the search inline', () => {
    renderPanel({ directoryId: 'trash:camp' });
    fireEvent.change(searchBox(), { target: { value: 'invoice' } });
    fireEvent.click(screen.getByRole('button', { name: 'All files' }));
    expect(searchBox().value).toBe('');
  });

  it('AC-6: never in Trash (sentinel ids are not folders in the live tree)', () => {
    renderPanel({ directoryId: '__trash__' });
    expect(screen.queryByTestId('folder-unavailable')).toBeNull();
    renderPanel({ directoryId: 'trash:gone' });
    expect(screen.queryByTestId('folder-unavailable')).toBeNull();
  });
});

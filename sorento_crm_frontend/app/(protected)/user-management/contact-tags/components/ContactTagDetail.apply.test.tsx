/**
 * CONTACT-TAGS S3, UAC T5.2 CTA: the tag detail header "Apply to N contacts" opens ApplyTagSettingsDialog
 * for the whole tag (props.tagId), N = the tag's contact_count.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render as rtlRender, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/contact-tags/tag-1',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/common/ListPager', () => ({ default: () => null }));
vi.mock('@/hooks/useDeferredAction', () => ({
  useDeferredAction: () => ({ pending: null, cancel: vi.fn(), start: vi.fn(), isBlocked: false }),
}));
vi.mock('../services/contactTagService', () => ({
  getContactTag: vi.fn().mockResolvedValue({
    id: 'tag-1', name: 'Dealer', description: null, is_active: true, sort_order: 0,
    settings: { chatbot: { chatbot_stock_allowed: false } }, contact_count: 7, differs_count: 2,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  }),
  getContactTags: vi.fn().mockResolvedValue({ data: [], pagination: { total: 0 } }),
  updateContactTag: vi.fn(),
}));
vi.mock('../../contacts/[id]/services/contactService', () => ({
  getContacts: vi.fn().mockResolvedValue({ data: [], pagination: { total: 0 } }),
}));
vi.mock('../../contacts/components/ApplyTagSettingsDialog', () => ({
  default: (props: { open: boolean; tagId?: string }) =>
    props.open ? <div data-testid="apply-dialog">{props.tagId}</div> : null,
}));

import ContactTagDetail from './ContactTagDetail';

describe('ContactTagDetail apply CTA', () => {
  it('"Apply to 7 contacts" opens the dialog for the tag', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rtlRender(
      <QueryClientProvider client={client}>
        <ContactTagDetail id="tag-1" />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Apply to 7 contacts' }));
    expect(screen.getByTestId('apply-dialog')).toHaveTextContent('tag-1');
  });
});

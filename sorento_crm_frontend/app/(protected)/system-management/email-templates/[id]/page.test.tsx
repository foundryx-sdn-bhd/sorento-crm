/**
 * S8, AC-EM040/044 - the template detail page: a legacy template
 * (`layout_json: null`) shows the implicit brand-header/custom-text/footer
 * list, and Save sends `layout_json` with the blocks in their on-screen
 * order.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'tpl-1' }),
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/common/PageHeader', () => ({
  PageHeader: ({ title, actions }: { title: React.ReactNode; actions?: React.ReactNode }) => (
    <header>
      <h1>{title}</h1>
      <div data-testid="header-actions">{actions}</div>
    </header>
  ),
}));

const TEMPLATE = {
  id: 'tpl-1',
  code: 'auth_password_reset',
  name: 'Password reset',
  description: null,
  subject: 'Reset your password',
  preheader: null,
  body_html: '<p>Click the button below.</p>',
  body_text: null,
  layout_json: null,
  is_active: true,
  is_system: true,
  created_by_user_id: null,
  created_at: '2026-01-01T00:00:00',
  updated_at: '2026-01-01T00:00:00',
};

const updateMut = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const savedPreviewMut = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false, data: undefined as unknown }));
const draftPreviewMut = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false, data: undefined as unknown }));

vi.mock('../hooks/useEmailTemplates', () => ({
  useEmailTemplate: () => ({ data: TEMPLATE, isLoading: false, refetch: vi.fn() }),
  usePreviewEmailTemplate: () => savedPreviewMut,
  usePreviewEmailTemplateDraft: () => draftPreviewMut,
  useTemplateVariableCatalog: () => ({ data: { variables: [] } }),
  useUpdateEmailTemplate: () => updateMut,
}));

import EmailTemplateDetailPage from './page';

beforeEach(() => {
  updateMut.mutateAsync.mockReset();
  updateMut.mutateAsync.mockResolvedValue(TEMPLATE);
  savedPreviewMut.mutate.mockReset();
  draftPreviewMut.mutate.mockReset();
});

describe('EmailTemplateDetailPage', () => {
  it('a legacy template (layout_json null) shows the implicit block list', () => {
    render(<EmailTemplateDetailPage />);

    expect(screen.getByText('Brand header')).toBeInTheDocument();
    expect(screen.getByText('Custom text')).toBeInTheDocument();
    expect(screen.getByText('Footer')).toBeInTheDocument();
  });

  it('Save sends layout_json with the blocks in the on-screen order', async () => {
    render(<EmailTemplateDetailPage />);

    fireEvent.click(screen.getByRole('button', { name: /Edit/ }));

    // Move the footer block up one, ahead of custom text.
    const moveUpButtons = screen.getAllByRole('button', { name: 'Move up' });
    fireEvent.click(moveUpButtons[moveUpButtons.length - 1]);

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(updateMut.mutateAsync).toHaveBeenCalledTimes(1));
    const payload = updateMut.mutateAsync.mock.calls[0][0];
    expect(payload.layout_json.blocks.map((b: { type: string }) => b.type)).toEqual([
      'brand_header',
      'footer',
      'custom_text',
    ]);
  });

  // EMAIL-HANDOVER-QTY AC-14: the card width rides on layout_json.
  it('a template without a width shows Standard and saves layout_json.width standard', async () => {
    render(<EmailTemplateDetailPage />);
    expect(screen.getByText('Standard (600px)')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(updateMut.mutateAsync).toHaveBeenCalledTimes(1));
    expect(updateMut.mutateAsync.mock.calls[0][0].layout_json.width).toBe('standard');
  });

  it('choosing Wide sends layout_json.width wide on Save and on the draft preview', async () => {
    render(<EmailTemplateDetailPage />);

    fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
    fireEvent.click(screen.getByLabelText('Width'));
    fireEvent.click(screen.getByRole('option', { name: 'Wide (900px)' }));

    await waitFor(() =>
      expect(draftPreviewMut.mutate).toHaveBeenCalledWith(
        expect.objectContaining({
          layout_json: expect.objectContaining({ width: 'wide' }),
        }),
      ),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updateMut.mutateAsync).toHaveBeenCalledTimes(1));
    expect(updateMut.mutateAsync.mock.calls[0][0].layout_json.width).toBe('wide');
  });

  it('choosing Print sends layout_json.width print (OI-EMAIL-PRINT)', async () => {
    render(<EmailTemplateDetailPage />);

    fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
    fireEvent.click(screen.getByLabelText('Width'));
    fireEvent.click(screen.getByRole('option', { name: 'Print, A4 portrait (672px)' }));

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(updateMut.mutateAsync).toHaveBeenCalledTimes(1));
    expect(updateMut.mutateAsync.mock.calls[0][0].layout_json.width).toBe('print');
  });
});

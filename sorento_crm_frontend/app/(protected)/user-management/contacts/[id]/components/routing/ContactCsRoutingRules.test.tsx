/**
 * CS-ROUTING-TARGETS reviewer round 1: every routing write now needs
 * `user_management.contacts.edit`, so a viewer without it must see the rules read-only
 * instead of editing them and hitting a 403 on Save (PR-CHECKLIST "Never stuck").
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const hasPermission = vi.fn(() => true);
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => hasPermission(slug),
}));

vi.mock('./csRoutingMock', () => ({
  CS_USE_CASES: [
    { key: 'purchase_request', label: 'Purchase Request' },
    { key: 'sponsorship_form', label: 'Sponsorship Form' },
  ],
  getRoutingRules: vi.fn(async () => [
    {
      id: 'r1',
      use_case: 'purchase_request',
      cs_pic_user_id: 'u-1',
      cs_pic_name: 'ZZT Dina',
      match_conditions: [],
      priority: 0,
    },
  ]),
  getCsCandidates: vi.fn(async () => [
    { id: 'u-1', name: 'ZZT Dina', email: 'dina@t.zzt' },
  ]),
  getRoutableFields: vi.fn(async () => []),
  saveRoutingRules: vi.fn(),
}));

import ContactCsRoutingRules from './ContactCsRoutingRules';

function renderRules() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ContactCsRoutingRules contactId="c-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  hasPermission.mockReset();
});

describe('ContactCsRoutingRules permission', () => {
  it('without contacts.edit shows the rules read-only', async () => {
    hasPermission.mockImplementation(() => false);

    renderRules();

    expect(await screen.findByText('Priority 1')).toBeInTheDocument();
    expect(hasPermission).toHaveBeenCalledWith('user_management.contacts.edit');
    expect(
      screen.queryByRole('button', { name: /save routing rules/i }),
    ).toBeNull();
    expect(screen.queryByRole('button', { name: /add rule/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /remove rule/i })).toBeNull();
    for (const select of screen.getAllByRole('combobox')) {
      expect(select).toBeDisabled();
    }
  });

  it('with contacts.edit keeps the editor', async () => {
    hasPermission.mockImplementation(() => true);

    renderRules();

    expect(await screen.findByText('Priority 1')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /save routing rules/i }),
    ).toBeEnabled();
    expect(screen.getByRole('button', { name: /add rule/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /remove rule/i })).toBeEnabled();
    for (const select of screen.getAllByRole('combobox')) {
      expect(select).toBeEnabled();
    }
  });
});

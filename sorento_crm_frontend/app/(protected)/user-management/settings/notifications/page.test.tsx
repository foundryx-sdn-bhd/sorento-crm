/**
 * M5-06 - the notification settings table renders on DataGrid instead of a
 * raw `<Table>`.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const mockSettings = {
  notifyStockEmail: true,
  notifyStockWeb: false,
  notifyStockRoleIds: [],
  notifyNewOrderEmail: false,
  notifyNewOrderWeb: false,
  notifyNewOrderRoleIds: [],
  notifyOrderStatusUpdateEmail: false,
  notifyOrderStatusUpdateWeb: false,
  notifyOrderStatusUpdateRoleIds: [],
  notifyPaymentFailureEmail: false,
  notifyPaymentFailureWeb: false,
  notifyPaymentFailureRoleIds: [],
  notifySystemErrorFailureEmail: false,
  notifySystemErrorWeb: false,
  notifySystemErrorRoleIds: [],
};

vi.mock('../components/settings-context', () => ({
  useSettings: () => ({ settings: mockSettings, roles: [] }),
}));

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

// Save is offered only to the edit slug holder (SETTINGS-PERM-GATE); this file is
// about the grid, so the caller holds it.
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => slug === 'user_management.settings.edit',
}));

import NotificationSettingsPage from './page';

function wrap(node: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe('NotificationSettingsPage - DataGrid', () => {
  it('renders the column headers and a real row for each notification setting', () => {
    wrap(<NotificationSettingsPage />);

    expect(screen.getByText('Notification')).toBeInTheDocument();
    expect(screen.getByText('Users')).toBeInTheDocument();
    expect(screen.getByText('Email')).toBeInTheDocument();
    expect(screen.getByText('Web')).toBeInTheDocument();

    expect(screen.getByText('Stock Alerts')).toBeInTheDocument();
    expect(screen.getByText('Notify when stock reaches the threshold.')).toBeInTheDocument();
    expect(screen.getByText('System Errors')).toBeInTheDocument();
  });
});

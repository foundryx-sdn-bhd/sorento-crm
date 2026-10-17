import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const perms = vi.hoisted(() => ({ set: new Set<string>() }));
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({
    permissionSet: perms.set,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));
const state = vi.hoisted(() => ({ byKind: {} as Record<string, unknown> }));
vi.mock('../../_shared/hooks/useProjectForms', () => ({
  useProjectForms: (kind: string) => state.byKind[kind] ?? { isLoading: true },
}));

import { FormsPanel } from './FormsPanel';

const project = { id: 'p-1' } as never;

beforeEach(() => {
  perms.set = new Set();
  state.byKind = {};
});

describe('FormsPanel', () => {
  it('renders only the permitted sections', () => {
    perms.set = new Set(['procurement.stock_inquiries.view']);
    state.byKind['stock-inquiries'] = { isLoading: false, isError: false, data: { rows: [], total: 0 } };
    render(<FormsPanel project={project} />);
    expect(screen.getByText('Stock inquiries')).toBeTruthy();
    expect(screen.queryByText('Complaints')).toBeNull();
    expect(screen.getByText('No stock inquiries on this project')).toBeTruthy();
  });

  it('links a row to the form and shows its status', () => {
    perms.set = new Set(['sales.opportunities.view']);
    state.byKind['sales-opportunities'] = {
      isLoading: false,
      isError: false,
      data: {
        total: 1,
        rows: [
          { id: 'o1', href: '/sales/opportunities/o1', number: 'OP-1', date: null, statusLabel: 'New', statusClass: '', summary: 'Tower A' },
        ],
      },
    };
    render(<FormsPanel project={project} />);
    expect(screen.getByRole('link', { name: /OP-1/ }).getAttribute('href')).toBe('/sales/opportunities/o1');
    expect(screen.getByText('New')).toBeTruthy();
  });

  it('shows the error with a Retry, never a stuck spinner', () => {
    perms.set = new Set(['complaint_management.complaints.view']);
    state.byKind['complaints'] = {
      isLoading: false,
      isError: true,
      error: new Error('Failed to fetch complaints'),
      refetch: vi.fn(),
    };
    render(<FormsPanel project={project} />);
    expect(screen.getByText('Failed to fetch complaints')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('says so when the user can view no form type', () => {
    render(<FormsPanel project={project} />);
    expect(screen.getByText('No forms to show')).toBeTruthy();
  });
});

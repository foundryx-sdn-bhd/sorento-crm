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

vi.mock('@/components/common/PanelDataGrid', () => ({
  PanelDataGrid: (props: {
    title: string;
    rows: { id: string; number: string }[];
    isLoading?: boolean;
    error?: unknown;
    onRetry?: () => void;
    emptyTitle: string;
    emptyBody?: string;
    rowHref?: (row: { id: string; number: string }) => string;
    columns: { id?: string; size?: number }[];
  }) => (
    <section aria-label={props.title}>
      <h3>{props.title}</h3>
      {props.error ? (
        <div>
          <span role="alert">{(props.error as Error).message}</span>
          <button onClick={props.onRetry}>Retry</button>
        </div>
      ) : props.isLoading ? (
        <span>loading</span>
      ) : props.rows.length === 0 ? (
        <div>
          <p>{props.emptyTitle}</p>
          <p>{props.emptyBody}</p>
        </div>
      ) : (
        props.rows.map((r) => (
          <a key={r.id} href={props.rowHref?.(r)}>
            {r.number}
          </a>
        ))
      )}
    </section>
  ),
}));

import { FormsPanel } from './FormsPanel';

const project = { id: 'p-1' } as never;

beforeEach(() => {
  perms.set = new Set();
  state.byKind = {};
});

describe('FormsPanel', () => {
  it('renders only the permitted sections, each with a hint under the empty heading', () => {
    perms.set = new Set(['procurement.stock_inquiries.view']);
    state.byKind['stock-inquiries'] = { isLoading: false, isError: false, data: { rows: [], total: 0 } };
    render(<FormsPanel project={project} />);
    expect(screen.getByText('Stock inquiries')).toBeTruthy();
    expect(screen.queryByText('Complaints')).toBeNull();
    expect(screen.getByText('No stock inquiries on this project')).toBeTruthy();
    expect(screen.getByText('Stock inquiries linked to this project appear here.')).toBeTruthy();
  });

  it('opens a row at the form detail page through rowHref', () => {
    perms.set = new Set(['sales.opportunities.view']);
    state.byKind['sales-opportunities'] = {
      isLoading: false,
      isError: false,
      data: {
        total: 1,
        rows: [
          { id: 'o1', href: '/sales/opportunities/o1', number: 'OP-1', date: null, status: 'new', statusLabel: 'New', summary: 'Tower A' },
        ],
      },
    };
    render(<FormsPanel project={project} />);
    expect(screen.getByRole('link', { name: /OP-1/ }).getAttribute('href')).toBe('/sales/opportunities/o1');
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
    expect(screen.getByRole('alert').textContent).toBe('Failed to fetch complaints');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('says so when the user can view no form type', () => {
    render(<FormsPanel project={project} />);
    expect(screen.getByText('No forms to show')).toBeTruthy();
  });
});

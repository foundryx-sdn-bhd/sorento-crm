import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import RespondWorkspacesAdmin from './RespondWorkspacesAdmin';
import type { RespondWorkspace } from '../services/respondWorkspaceService';

/**
 * Ideas embed (iframe SSO) admin fields - AC-E-1 / AC-E-12.
 *
 * The three embed fields are present + editable in the Ideation section, and the
 * signing secret input is write-only (password type - the browser never echoes the
 * stored plaintext). The dialog is opened via the toolbar "Add workspace" button:
 * DataGrid row-action buttons don't drive React state reliably under jsdom, so the
 * edit-mode masked-placeholder display is left to the backend masking tests
 * (``test_ideation_embed_config.py``) + Playwright at handoff.
 */

const listRespondWorkspaces = vi.fn();
const listIdeationProducts = vi.fn();
const updateRespondWorkspace = vi.fn();
const createRespondWorkspace = vi.fn();
const deleteRespondWorkspace = vi.fn();
const setDefaultRespondWorkspace = vi.fn();

vi.mock('../services/respondWorkspaceService', () => ({
  listRespondWorkspaces: (...a: unknown[]) => listRespondWorkspaces(...a),
  listIdeationProducts: (...a: unknown[]) => listIdeationProducts(...a),
  updateRespondWorkspace: (...a: unknown[]) => updateRespondWorkspace(...a),
  createRespondWorkspace: (...a: unknown[]) => createRespondWorkspace(...a),
  deleteRespondWorkspace: (...a: unknown[]) => deleteRespondWorkspace(...a),
  setDefaultRespondWorkspace: (...a: unknown[]) => setDefaultRespondWorkspace(...a),
}));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const ROW: RespondWorkspace = {
  id: 'ws-1',
  space_id: '364817',
  name: 'Sorento Main',
  base_url: 'https://api.respond.io',
  whatsapp_number: '60123456789',
  is_active: true,
  is_default: true,
  api_key_masked: '****abcd',
  ideation_shared_service_url: 'https://chat.foundryx.my/be',
  ideation_product_id: null,
  ideation_intake_api_key_masked: '****1234',
  ideation_embed_connection_id: 'conn-live-1',
  ideation_embed_fe_base_url: 'https://chat.foundryx.my',
  ideation_embed_signing_secret_masked: '****cdef',
  chatbot_retry_ingress_url: null,
  has_chatbot_retry_key: false,
  chatbot_sender_url: null,
  has_chatbot_sender_key: false,
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
};

beforeEach(() => {
  listRespondWorkspaces.mockReset().mockResolvedValue([ROW]);
  listIdeationProducts.mockReset().mockResolvedValue({ products: [], error: null });
  createRespondWorkspace.mockReset().mockResolvedValue(ROW);
  if (!window.matchMedia) {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
  }
  if (!('ResizeObserver' in window)) {
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => cleanup());

async function openAddDialog() {
  renderWithClient(<RespondWorkspacesAdmin />);
  // wait for the grid to settle, then open the create dialog via the toolbar
  await screen.findByRole('button', { name: /add workspace/i });
  fireEvent.click(screen.getByRole('button', { name: /add workspace/i }));
  await waitFor(() =>
    expect(screen.getByText('Ideas embed (iframe SSO)')).toBeInTheDocument(),
  );
}

describe('RespondWorkspacesAdmin - Ideas embed fields', () => {
  it('renders the three embed fields in the Ideation section', async () => {
    await openAddDialog();

    const conn = screen.getByLabelText(/Embed connection ID/i) as HTMLInputElement;
    expect(conn).toBeInTheDocument();
    expect(conn.value).toBe('');

    const feBase = screen.getByLabelText(/Embed FE base URL/i) as HTMLInputElement;
    expect(feBase).toBeInTheDocument();
    expect(feBase.value).toBe('');

    const secret = screen.getByLabelText(/Embed signing secret/i) as HTMLInputElement;
    // write-only: password type so the browser never echoes plaintext (AC-E-12)
    expect(secret.type).toBe('password');
    expect(secret.value).toBe('');
    expect(secret.placeholder).toMatch(/signs the SSO assertion/i);
  });

  it('the embed fields are editable', async () => {
    await openAddDialog();

    const conn = screen.getByLabelText(/Embed connection ID/i) as HTMLInputElement;
    fireEvent.change(conn, { target: { value: 'conn-typed' } });
    expect(conn.value).toBe('conn-typed');

    const feBase = screen.getByLabelText(/Embed FE base URL/i) as HTMLInputElement;
    fireEvent.change(feBase, { target: { value: 'https://fe.example.com' } });
    expect(feBase.value).toBe('https://fe.example.com');

    const secret = screen.getByLabelText(/Embed signing secret/i) as HTMLInputElement;
    fireEvent.change(secret, { target: { value: 'super-secret' } });
    expect(secret.value).toBe('super-secret');
  });

  it('explains the FE base URL is distinct from the backend URL (AC-E-3)', async () => {
    await openAddDialog();
    expect(screen.getByText(/NOT the backend \/ API URL/i)).toBeInTheDocument();
  });
});

/**
 * The requester's screen, across the states she can actually land in.
 *
 * The two that matter most are the ones a happy-path-only test would miss: a
 * dead link (she needs to be told to ask for a new one, not shown an empty
 * form), and a submitted batch (the same link becomes a read-only status page,
 * which is the whole point of the token being multi-use).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { OnboardingIntakeContext } from '@/components/common/onboarding/types';
import { IntakeScreen } from './IntakeScreen';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
    warning: vi.fn(),
    custom: vi.fn(),
  },
}));

const fetchIntakeContext = vi.fn();
const saveRows = vi.fn();
const submitIntake = vi.fn();

vi.mock('../lib/onboarding-client', async () => {
  const actual = await vi.importActual<typeof import('../lib/onboarding-client')>(
    '../lib/onboarding-client',
  );
  return {
    ...actual,
    fetchIntakeContext: (...args: unknown[]) => fetchIntakeContext(...args),
    saveRows: (...args: unknown[]) => saveRows(...args),
    submitIntake: (...args: unknown[]) => submitIntake(...args),
  };
});

const CONTEXT: OnboardingIntakeContext = {
  title: 'MOCHA staff onboarding',
  company_name: 'MOCHA Sdn Bhd',
  requester_name: 'Esther Lim',
  requester_email: 'person13@example.com',
  status: 'sent',
  expires_at: '2026-08-28T09:00:00',
  editable: true,
  requester_note: null,
  templates: [
    {
      id: 'tpl-sales',
      name: 'Salesperson',
      description: 'Sees their own orders.',
      default_needs_system_account: true,
      default_needs_respond_contact: true,
      default_needs_agent_seat: false,
    },
  ],
  people: [],
};

function renderScreen(token = 'TOKEN') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IntakeScreen token={token} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('IntakeScreen', () => {
  it('tells her what the system already knows, and asks nothing for it', async () => {
    fetchIntakeContext.mockResolvedValue(CONTEXT);
    renderScreen();
    expect(await screen.findByText('MOCHA staff onboarding')).toBeInTheDocument();
    expect(screen.getByText('MOCHA Sdn Bhd')).toBeInTheDocument();
    expect(screen.getByText('Esther Lim')).toBeInTheDocument();
    expect(screen.getByText(/Link valid until/)).toBeInTheDocument();
  });

  it('shows a loading state before the context lands', () => {
    fetchIntakeContext.mockReturnValue(new Promise(() => {}));
    const { container } = renderScreen();
    expect(container.querySelectorAll('[data-slot="skeleton"], .animate-pulse').length)
      .toBeGreaterThan(0);
  });

  it('tells her to ask for a new link rather than showing an empty form', async () => {
    fetchIntakeContext.mockRejectedValue(new Error('This onboarding link has expired.'));
    renderScreen();
    expect(await screen.findByText('This onboarding link has expired.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Submit for review/ })).not.toBeInTheDocument();
  });

  it('refuses to submit an empty batch', async () => {
    fetchIntakeContext.mockResolvedValue(CONTEXT);
    renderScreen();
    const submit = await screen.findByRole('button', { name: /Submit for review/ });
    expect(submit).toBeDisabled();
  });

  it('lets her add a person, which is the only way a list is built', async () => {
    fetchIntakeContext.mockResolvedValue(CONTEXT);
    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: /Add a person/ }));
    expect(await screen.findByText('1 person ready to submit.')).toBeInTheDocument();
    // Still blocked: an unnamed row cannot be submitted.
    expect(screen.getByRole('button', { name: /Submit for review/ })).toBeDisabled();
    expect(screen.getByText('Every person needs a name.')).toBeInTheDocument();
  });

  it('lets one person need more than one thing', async () => {
    // The whole path the requester walks, with the screen owning the rows: add a
    // person, open Needs, pick a second and a third. Each click is a toggle, and
    // the menu has to SAY which needs are chosen - the tick alone is a bare icon
    // no reader can see, and cmdk marks the highlighted row rather than the
    // chosen ones, so a working multi-select was reported as a single select.
    fetchIntakeContext.mockResolvedValue(CONTEXT);
    renderScreen();

    fireEvent.click(await screen.findByRole('button', { name: /Add a person/ }));
    const needs = await screen.findByLabelText('Needs');
    // A new row starts with a system account and nothing else.
    expect(needs).toHaveTextContent('System account');

    fireEvent.click(needs);
    fireEvent.click(await screen.findByRole('option', { name: 'Access to chatbot AI' }));
    expect(screen.getByLabelText('Needs')).toHaveTextContent(
      'System account, Access to chatbot AI',
    );

    fireEvent.click(screen.getByRole('option', { name: 'Respond.io account' }));
    expect(screen.getByLabelText('Needs')).toHaveTextContent(
      'System account, Access to chatbot AI, Respond.io account',
    );
    for (const label of ['System account', 'Access to chatbot AI', 'Respond.io account']) {
      expect(screen.getByRole('option', { name: label })).toHaveAttribute('aria-checked', 'true');
    }

    // And clicking a chosen need drops that one alone.
    fireEvent.click(screen.getByRole('option', { name: 'System account' }));
    expect(screen.getByLabelText('Needs')).toHaveTextContent(
      'Access to chatbot AI, Respond.io account',
    );
    expect(screen.getByRole('option', { name: 'System account' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
  });

  it('groups the journey into named cards rather than numbered steps', async () => {
    fetchIntakeContext.mockResolvedValue(CONTEXT);
    renderScreen();
    // Sections, not a wizard: "1. Give us the people" told her which step she
    // was on, which is the one thing a one-screen form already shows her.
    expect(await screen.findByText('People')).toBeInTheDocument();
    expect(screen.getAllByText('Notes').length).toBeGreaterThan(0);
    expect(screen.queryByText(/1\. Give us the people/)).not.toBeInTheDocument();
    expect(screen.queryByText(/2\. Say what each person needs/)).not.toBeInTheDocument();
  });

  it('offers no file upload, because rows are typed into the system', async () => {
    fetchIntakeContext.mockResolvedValue(CONTEXT);
    const { container } = renderScreen();
    await screen.findByText('MOCHA staff onboarding');
    // The workbook reader is gone (captain decision, 2026-08-15), so a dropzone
    // here would offer a path the server no longer has.
    expect(container.querySelector('input[type="file"]')).toBeNull();
  });

  it('turns into a read-only status page once submitted', async () => {
    fetchIntakeContext.mockResolvedValue({
      ...CONTEXT,
      status: 'submitted',
      editable: false,
      people: [
        {
          id: 'p1',
          row_number: 1,
          full_name: 'Nurul Aisyah',
          nick_name: 'Aisyah',
          role_label: 'Sales admin',
          phone_raw: '010-0000002',
          email_raw: 'person4@example.com',
          template_id: 'tpl-sales',
          requester_note: null,
          reviewer_note: null,
          needs_system_account: true,
          needs_respond_contact: true,
          needs_agent_seat: false,
          review_status: 'approved' as const,
          rejection_reason: null,
          collisions: [],
          user_step: 'done' as const,
          user_error: null,
          user_label: 'Nurul Aisyah',
          contact_step: 'pending' as const,
          contact_error: null,
          agent_step: 'pending' as const,
          agent_error: null,
        },
      ],
    });

    renderScreen();
    // "1 person", not "1 people": one name in the batch is an ordinary batch.
    expect(await screen.findByText(/1 person submitted for review/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Submit for review/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add a person/ })).not.toBeInTheDocument();
    // Her rows are still on screen - the link is a status page, not a dead end.
    expect(screen.getAllByText('Nurul Aisyah').length).toBeGreaterThan(0);
  });

  it('saves what she has typed so far without submitting it', async () => {
    // The link is multi-use until submission and the intake email tells her she
    // can come back to it, so the rows have to be able to reach the server
    // before the batch is sealed. Until Save draft existed the only save was the
    // one submit made on its way out, and closing the tab lost the lot.
    fetchIntakeContext.mockResolvedValue(CONTEXT);
    saveRows.mockResolvedValue({ ...CONTEXT, people: [] });

    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: /Add a person/ }));
    const nameInput = (await screen.findAllByLabelText('Name, row 1'))[0];
    fireEvent.change(nameInput, { target: { value: 'Half A List' } });
    fireEvent.blur(nameInput);

    fireEvent.click(screen.getByRole('button', { name: /Save draft/ }));

    await waitFor(() => expect(saveRows).toHaveBeenCalled());
    const [, rows] = saveRows.mock.calls[0];
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ row_number: 1, full_name: 'Half A List' });
    expect(submitIntake).not.toHaveBeenCalled();

    // Still hers to edit: saving is not submitting.
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith('1 person saved.'));
    expect(screen.getByRole('button', { name: /Add a person/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Submit for review/ })).toBeEnabled();
    expect(screen.getAllByLabelText('Name, row 1')[0]).toHaveValue('Half A List');
  });

  it('submits the batch and flips to the status view', async () => {
    fetchIntakeContext.mockResolvedValue(CONTEXT);
    saveRows.mockResolvedValue({ ...CONTEXT, people: [] });
    submitIntake.mockResolvedValue({
      ...CONTEXT,
      status: 'submitted',
      editable: false,
      people: [],
    });

    renderScreen();
    fireEvent.click(await screen.findByRole('button', { name: /Add a person/ }));
    const nameInputs = await screen.findAllByLabelText('Name, row 1');
    fireEvent.change(nameInputs[0], { target: { value: 'Typed Person' } });
    // The grid commits a text edit when the field is left, so leaving it is
    // part of typing a name, exactly as it is for a real requester.
    fireEvent.blur(nameInputs[0]);

    const submit = screen.getByRole('button', { name: /Submit for review/ });
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);

    await waitFor(() => expect(submitIntake).toHaveBeenCalled());
    // Saved before submitting: the rows have to reach the server first.
    expect(saveRows).toHaveBeenCalled();
    expect(await screen.findByText(/submitted for review/)).toBeInTheDocument();
  });
});

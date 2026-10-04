/**
 * The one grid both onboarding screens render.
 *
 * What is worth pinning here is the DIFFERENCE between the modes, because the
 * whole reason this is one component is that the requester's view and the
 * reviewer's view must not drift: same columns in the same order, with the
 * reviewer's extra columns added rather than substituted.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PeopleGrid } from './PeopleGrid';
import type {
  OnboardingPerson,
  OnboardingTemplateOption,
} from './types';

// DataGrid fetches the user's hidden/resized columns and renders skeletons until
// that answers; under jsdom nothing does, so rows never mount without this.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const TEMPLATES: OnboardingTemplateOption[] = [
  {
    id: 'tpl-sales',
    name: 'Salesperson',
    description: 'Sees their own orders.',
    default_needs_system_account: true,
    default_needs_respond_contact: true,
    default_needs_agent_seat: false,
  },
  {
    id: 'tpl-dealer',
    name: 'Dealer',
    description: 'No system login.',
    default_needs_system_account: false,
    default_needs_respond_contact: true,
    default_needs_agent_seat: false,
  },
];

function person(overrides: Partial<OnboardingPerson> = {}): OnboardingPerson {
  return {
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
    review_status: 'proposed',
    rejection_reason: null,
    collisions: [],
    user_step: 'pending',
    user_error: null,
    user_label: null,
    contact_step: 'pending',
    contact_error: null,
    agent_step: 'pending',
    agent_error: null,
    ...overrides,
  };
}

/**
 * The grid's own card. One rendering at every width now - the parallel
 * small-screen card layout is gone, so a value can only be on screen once.
 */
function grid() {
  return screen.getByTestId('people-grid');
}

/** The template picker for a row: a SearchableSelect behind an sr-only label. */
function templateTrigger() {
  return within(grid()).getAllByLabelText('Access template')[0];
}

describe('PeopleGrid', () => {
  it('renders an empty state rather than an empty table', () => {
    render(
      <PeopleGrid
        mode="intake"
        people={[]}
        templates={TEMPLATES}
        emptyMessage="No people yet. Add a person."
      />,
    );
    expect(screen.getAllByText(/No people yet/i).length).toBeGreaterThan(0);
  });

  it('renders a loading state', () => {
    const { container } = render(
      <PeopleGrid mode="intake" people={[]} templates={TEMPLATES} isLoading />,
    );
    expect(container.querySelectorAll('[data-slot="skeleton"], .animate-pulse').length)
      .toBeGreaterThan(0);
  });

  it('shows the requester her own values back, raw', () => {
    render(<PeopleGrid mode="intake" people={[person()]} templates={TEMPLATES} />);
    const cells = within(grid());
    expect(cells.getByLabelText('Name, row 1')).toHaveValue('Nurul Aisyah');
    // The dash and the spacing are hers. Normalising what she sees reads as the
    // system losing her input.
    expect(cells.getByLabelText('Phone, row 1')).toHaveValue('010-0000002');
  });

  it('raises a patch when a field is edited, once the field is left', () => {
    const onPatch = vi.fn();
    render(
      <PeopleGrid
        mode="intake"
        people={[person()]}
        templates={TEMPLATES}
        onPatchPerson={onPatch}
      />,
    );
    const email = within(grid()).getByLabelText('Email, row 1');
    fireEvent.change(email, { target: { value: 'person29@example.com' } });
    // An edit is one event, not one per character: the parent hears about it
    // when she moves off the field.
    fireEvent.blur(email);
    expect(onPatch).toHaveBeenCalledWith('p1', { email_raw: 'person29@example.com' });
  });

  it('keeps the caret in the cell while a name is typed, and patches once', () => {
    // The bug this pins: the columns were rebuilt whenever a handler identity
    // changed, which is every keystroke for a parent that owns the rows. New
    // cell renderers meant React remounted the cell, so the input lost focus
    // after the first character and a name could not be typed at all.
    const onPatch = vi.fn();
    const { rerender } = render(
      <PeopleGrid
        mode="intake"
        people={[person({ full_name: '' })]}
        // A FRESH array every render, which is what a query response actually
        // hands over: same options, new identity. Passing the module-level
        // constant let a `templates` memo dependency look stable when in real
        // use it never is.
        templates={[...TEMPLATES]}
        // Inline, like both real callers: the grid has to survive it.
        onPatchPerson={(id, patch) => onPatch(id, patch)}
      />,
    );

    const name = within(grid()).getByLabelText('Name, row 1') as HTMLInputElement;
    name.focus();
    expect(document.activeElement).toBe(name);

    let typed = '';
    for (const char of 'Aisyah') {
      typed += char;
      fireEvent.change(name, { target: { value: typed } });
      // The controlled parent re-renders on every keystroke it hears about; do
      // the same here so a remount would show up.
      rerender(
        <PeopleGrid
          mode="intake"
          people={[person({ full_name: '' })]}
          templates={[...TEMPLATES]}
          onPatchPerson={(id, patch) => onPatch(id, patch)}
        />,
      );
      expect(document.activeElement).toBe(name);
      expect(name.value).toBe(typed);
    }

    // Nothing was sent while she was still typing.
    expect(onPatch).not.toHaveBeenCalled();

    fireEvent.blur(name);
    expect(onPatch).toHaveBeenCalledTimes(1);
    expect(onPatch).toHaveBeenCalledWith('p1', { full_name: 'Aisyah' });
  });

  it('lets a half-typed name survive an update arriving from outside', () => {
    // The other direction of the same guard: a refetch lands while she is mid
    // word. Her typing has to win, or the field she is looking at changes under
    // the caret and what she had entered is gone.
    const onPatch = vi.fn();
    const { rerender } = render(
      <PeopleGrid
        mode="review"
        people={[person({ full_name: 'Nurul Aisyah' })]}
        templates={[...TEMPLATES]}
        onPatchPerson={onPatch}
      />,
    );

    const name = within(grid()).getByLabelText('Name, row 1') as HTMLInputElement;
    name.focus();
    fireEvent.change(name, { target: { value: 'Nurul Ais' } });

    rerender(
      <PeopleGrid
        mode="review"
        people={[person({ full_name: 'Somebody Else Entirely' })]}
        templates={[...TEMPLATES]}
        onPatchPerson={onPatch}
      />,
    );

    expect(document.activeElement).toBe(name);
    expect(name).toHaveValue('Nurul Ais');
    // And her draft is what commits, not the value that tried to overwrite it.
    fireEvent.blur(name);
    expect(onPatch).toHaveBeenCalledWith('p1', { full_name: 'Nurul Ais' });
  });

  it('takes an update from outside into a field nobody is typing into', () => {
    const { rerender } = render(
      <PeopleGrid
        mode="review"
        people={[person({ full_name: 'Nurul Aisyah' })]}
        templates={TEMPLATES}
        onPatchPerson={vi.fn()}
      />,
    );
    expect(within(grid()).getByLabelText('Name, row 1')).toHaveValue('Nurul Aisyah');

    // A refetch (or another reviewer's edit) landing on an idle field must win.
    rerender(
      <PeopleGrid
        mode="review"
        people={[person({ full_name: 'Nurul Aisyah binti Rahman' })]}
        templates={TEMPLATES}
        onPatchPerson={vi.fn()}
      />,
    );
    expect(within(grid()).getByLabelText('Name, row 1')).toHaveValue(
      'Nurul Aisyah binti Rahman',
    );
  });

  it('commits on Enter without waiting for the field to be left', () => {
    const onPatch = vi.fn();
    render(
      <PeopleGrid
        mode="intake"
        people={[person()]}
        templates={TEMPLATES}
        onPatchPerson={onPatch}
      />,
    );
    const nickname = within(grid()).getByLabelText('Nickname, row 1');
    fireEvent.change(nickname, { target: { value: 'Ais' } });
    fireEvent.keyDown(nickname, { key: 'Enter' });
    expect(onPatch).toHaveBeenCalledWith('p1', { nick_name: 'Ais' });
    // And leaving the field afterwards does not send the same edit twice.
    fireEvent.blur(nickname);
    expect(onPatch).toHaveBeenCalledTimes(1);
  });

  it('pre-fills the three needs from the template, without locking them', async () => {
    const onPatch = vi.fn();
    render(
      <PeopleGrid
        mode="intake"
        people={[person()]}
        templates={TEMPLATES}
        onPatchPerson={onPatch}
      />,
    );
    fireEvent.click(templateTrigger());
    fireEvent.click(await screen.findByRole('option', { name: /Dealer/ }));
    expect(onPatch).toHaveBeenCalledWith('p1', {
      template_id: 'tpl-dealer',
      needs_system_account: false,
      needs_respond_contact: true,
      needs_agent_seat: false,
    });
  });

  it('is a searchable select, not a raw dropdown', () => {
    render(<PeopleGrid mode="intake" people={[person()]} templates={TEMPLATES} />);
    // Every dropdown-select in this codebase is SearchableSelect, which exposes
    // the combobox role a raw <select> in a cell never did.
    expect(templateTrigger()).toHaveAttribute('role', 'combobox');
  });

  it('offers a way to clear the template', () => {
    const onPatch = vi.fn();
    render(
      <PeopleGrid
        mode="intake"
        people={[person()]}
        templates={TEMPLATES}
        onPatchPerson={onPatch}
      />,
    );
    // An optional select the user can change but never unset is the ADR's named
    // failure; `clearable` is what makes it clearable.
    const clear = within(templateTrigger()).getByRole('button', { name: 'Clear selection' });
    fireEvent.pointerDown(clear);
    expect(onPatch).toHaveBeenCalledWith('p1', { template_id: null });
  });

  it('asks what each person needs with the standard multi-select', async () => {
    const onPatch = vi.fn();
    render(
      <PeopleGrid
        mode="intake"
        people={[person()]}
        templates={TEMPLATES}
        onPatchPerson={onPatch}
      />,
    );
    // A stack of checkboxes was this grid's own invention; every other picker in
    // the app is the shared multi-select, which exposes the combobox role.
    const trigger = within(grid()).getByLabelText('Needs');
    expect(trigger).toHaveAttribute('role', 'combobox');

    fireEvent.click(trigger);
    fireEvent.click(await screen.findByRole('option', { name: 'Respond.io account' }));
    // Every flag rides on the patch, not only the one that moved: the selection
    // IS the answer, so an unticked option has to arrive as `false`.
    expect(onPatch).toHaveBeenCalledWith('p1', {
      needs_system_account: true,
      needs_respond_contact: true,
      needs_agent_seat: true,
    });
  });

  it.each(['intake', 'review'] as const)(
    'keeps a destructive control out from under the needs field in %s mode',
    (mode) => {
      // The reported "it behaves like a single select" came from here, not from
      // the toggle. The component's default trigger renders one removable chip
      // per choice, and in a 240px cell those chips fill the field: the X for
      // the first chip sat 14px from the field's own centre and unset that need
      // on pointer-down WITHOUT opening the menu, so the next pick looked like
      // it had replaced the selection. The trigger reports the answer; only the
      // menu changes it.
      render(
        <PeopleGrid
          mode={mode}
          people={[person({ needs_system_account: true, needs_respond_contact: true })]}
          templates={TEMPLATES}
          onPatchPerson={vi.fn()}
        />,
      );
      const trigger = within(grid()).getByLabelText('Needs');
      expect(within(trigger).queryByRole('button')).not.toBeInTheDocument();
      expect(trigger).toHaveTextContent('System account, Access to chatbot AI');
    },
  );

  it.each(['intake', 'review'] as const)(
    'toggles one need at a time in %s mode, never replacing the selection',
    async (mode) => {
      // The bug this pins: picking a second need silently dropped the first, so
      // the control behaved as a single select. Each click has to be a toggle
      // against the row as it stands NOW, which means the cell must be reading
      // the person the parent last handed it and not the one it first rendered.
      const onPatch = vi.fn();
      let current = person({
        needs_system_account: true,
        needs_respond_contact: false,
        needs_agent_seat: false,
      });
      const { rerender } = render(
        <PeopleGrid
          mode={mode}
          people={[current]}
          templates={TEMPLATES}
          onPatchPerson={onPatch}
        />,
      );

      const draw = () =>
        rerender(
          <PeopleGrid
            mode={mode}
            people={[current]}
            templates={TEMPLATES}
            onPatchPerson={onPatch}
          />,
        );

      fireEvent.click(within(grid()).getByLabelText('Needs'));
      fireEvent.click(await screen.findByRole('option', { name: 'Access to chatbot AI' }));
      expect(onPatch).toHaveBeenLastCalledWith('p1', {
        needs_system_account: true,
        needs_respond_contact: true,
        needs_agent_seat: false,
      });

      // The controlled parent applies the patch and re-renders, exactly as the
      // intake screen and the review page both do.
      current = { ...current, needs_respond_contact: true };
      draw();

      fireEvent.click(await screen.findByRole('option', { name: 'Respond.io account' }));
      expect(onPatch).toHaveBeenLastCalledWith('p1', {
        needs_system_account: true,
        needs_respond_contact: true,
        needs_agent_seat: true,
      });

      current = { ...current, needs_agent_seat: true };
      draw();

      // And a second click on a chosen option unticks that one alone.
      fireEvent.click(await screen.findByRole('option', { name: 'System account' }));
      expect(onPatch).toHaveBeenLastCalledWith('p1', {
        needs_system_account: false,
        needs_respond_contact: true,
        needs_agent_seat: true,
      });
    },
  );

  it.each(['intake', 'review'] as const)(
    'reports every chosen need as chosen in %s mode, not only the last one clicked',
    async (mode) => {
      // How the working toggle above still got reported as a single select. The
      // only sign that a need was chosen was a bare tick icon with no text, so
      // nothing reading the page rather than looking at it could tell a chosen
      // row from an unchosen one. What it COULD read was cmdk's `aria-selected`,
      // which marks the keyboard-highlighted row and follows the pointer - so
      // every click looked like it had moved the selection off the previous
      // need. The menu has to say which needs are chosen, in the row itself.
      const onPatch = vi.fn();
      let current = person({
        needs_system_account: true,
        needs_respond_contact: false,
        needs_agent_seat: false,
      });
      const { rerender } = render(
        <PeopleGrid mode={mode} people={[current]} templates={TEMPLATES} onPatchPerson={onPatch} />,
      );
      const draw = () =>
        rerender(
          <PeopleGrid
            mode={mode}
            people={[current]}
            templates={TEMPLATES}
            onPatchPerson={onPatch}
          />,
        );
      const need = (name: string) => screen.getByRole('option', { name });

      fireEvent.click(within(grid()).getByLabelText('Needs'));
      await screen.findByRole('option', { name: 'System account' });
      expect(need('System account')).toHaveAttribute('aria-checked', 'true');
      expect(need('Access to chatbot AI')).toHaveAttribute('aria-checked', 'false');

      fireEvent.click(need('Access to chatbot AI'));
      current = { ...current, needs_respond_contact: true };
      draw();
      // Both, even though the highlight has moved to the one just clicked.
      expect(need('System account')).toHaveAttribute('aria-checked', 'true');
      expect(need('Access to chatbot AI')).toHaveAttribute('aria-checked', 'true');

      fireEvent.click(need('Respond.io account'));
      current = { ...current, needs_agent_seat: true };
      draw();
      expect(need('System account')).toHaveAttribute('aria-checked', 'true');
      expect(need('Access to chatbot AI')).toHaveAttribute('aria-checked', 'true');
      expect(need('Respond.io account')).toHaveAttribute('aria-checked', 'true');
      // And the trigger says the same thing the menu does.
      expect(within(grid()).getByLabelText('Needs')).toHaveTextContent(
        'System account, Access to chatbot AI, Respond.io account',
      );
    },
  );

  it('says what each person needs in words when the row cannot be edited', () => {
    render(<PeopleGrid mode="readonly" people={[person()]} templates={TEMPLATES} />);
    const cells = within(grid());
    expect(cells.queryByLabelText('Needs')).not.toBeInTheDocument();
    expect(cells.getAllByText(/System account, Access to chatbot AI/).length).toBeGreaterThan(0);
  });

  it('carries the role the requester typed, and lets it be corrected', () => {
    const onPatch = vi.fn();
    render(
      <PeopleGrid
        mode="review"
        people={[person()]}
        templates={TEMPLATES}
        onPatchPerson={onPatch}
      />,
    );
    const role = within(grid()).getByLabelText('Role, row 1');
    expect(role).toHaveValue('Sales admin');
    fireEvent.change(role, { target: { value: 'Sales admin, KL' } });
    fireEvent.blur(role);
    expect(onPatch).toHaveBeenCalledWith('p1', { role_label: 'Sales admin, KL' });
  });

  it('carries no parser leftovers, now that rows are typed in', () => {
    // Issues and Section were both fed by the workbook reader, which is gone
    // (captain decision, 2026-08-15). A column nothing can ever fill reads as a
    // feature that is broken rather than one that was withdrawn.
    render(<PeopleGrid mode="review" people={[person()]} templates={TEMPLATES} />);
    expect(screen.queryByText('Issues')).not.toBeInTheDocument();
    expect(screen.queryByText('Section')).not.toBeInTheDocument();
  });

  it('hides collisions and verdicts from the requester', () => {
    render(
      <PeopleGrid
        mode="intake"
        people={[person({ collisions: [{ kind: 'user_email', label: 'Already a user: X' }] })]}
        templates={TEMPLATES}
      />,
    );
    expect(screen.queryByText('Already exists')).not.toBeInTheDocument();
    expect(screen.queryByText('Already a user: X')).not.toBeInTheDocument();
  });

  it('shows collisions and the lane ledger to the reviewer', () => {
    render(
      <PeopleGrid
        mode="review"
        people={[
          person({
            collisions: [{ kind: 'user_email', label: 'Already a user: Tan Wei Ming' }],
            user_step: 'skipped',
          }),
        ]}
        templates={TEMPLATES}
      />,
    );
    expect(screen.getAllByText('Already a user: Tan Wei Ming').length).toBeGreaterThan(0);
    // The captain's words for the three lanes. "WhatsApp contact" and
    // "Chat-agent seat" named the plumbing; these name what the person gets.
    expect(screen.getAllByText('System account').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Access to chatbot AI').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Respond.io account').length).toBeGreaterThan(0);
    expect(screen.queryByText('WhatsApp contact')).not.toBeInTheDocument();
    expect(screen.queryByText('Chat-agent seat')).not.toBeInTheDocument();
  });

  it('labels a pending contact lane as not yet automated rather than in progress', () => {
    render(
      <PeopleGrid mode="review" people={[person()]} templates={TEMPLATES} />,
    );
    expect(screen.getAllByText('Not yet automated').length).toBeGreaterThan(0);
  });

  it('surfaces a failed lane with its reason', () => {
    render(
      <PeopleGrid
        mode="review"
        people={[person({ user_step: 'failed', user_error: 'Email already registered.' })]}
        templates={TEMPLATES}
      />,
    );
    expect(screen.getAllByText('Email already registered.').length).toBeGreaterThan(0);
  });

  it('offers approve and reject per row in review, in the same words as the badge', () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <PeopleGrid
        mode="review"
        people={[person()]}
        templates={TEMPLATES}
        onApprovePerson={onApprove}
        onRejectPerson={onReject}
      />,
    );
    // "Approve", not "Keep": the badge for an approved row already says
    // Approved, and one verdict must not have two names.
    fireEvent.click(within(grid()).getByRole('button', { name: 'Approve' }));
    fireEvent.click(within(grid()).getByRole('button', { name: 'Reject' }));
    expect(onApprove).toHaveBeenCalledWith('p1');
    expect(onReject).toHaveBeenCalledWith('p1');
  });

  it('shows a rejection reason on the row that carries it', () => {
    render(
      <PeopleGrid
        mode="review"
        people={[
          person({ review_status: 'rejected', rejection_reason: 'Left the company.' }),
        ]}
        templates={TEMPLATES}
      />,
    );
    expect(screen.getAllByText('Rejected').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Left the company.').length).toBeGreaterThan(0);
  });

  it('is read-only in readonly mode, with the same values on screen', () => {
    render(<PeopleGrid mode="readonly" people={[person()]} templates={TEMPLATES} />);
    const cells = within(grid());
    expect(cells.queryByLabelText('Name, row 1')).not.toBeInTheDocument();
    expect(cells.getAllByText('Nurul Aisyah').length).toBeGreaterThan(0);
    expect(cells.getAllByText('010-0000002').length).toBeGreaterThan(0);
  });

  it('titles the card it is, so it needs no outer section around it', () => {
    render(
      <PeopleGrid
        mode="intake"
        people={[person()]}
        templates={TEMPLATES}
        title="Access"
        description="Submitted 1"
        actions={<button type="button">Add a person</button>}
      />,
    );
    const card = within(grid());
    expect(card.getByText('Access')).toBeInTheDocument();
    expect(card.getByText('Submitted 1')).toBeInTheDocument();
    expect(card.getByRole('button', { name: 'Add a person' })).toBeInTheDocument();
  });

  it('confirms before a row is removed', async () => {
    const onRemove = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PeopleGrid
          mode="intake"
          people={[person()]}
          templates={TEMPLATES}
          onRemovePerson={onRemove}
        />
      </QueryClientProvider>,
    );

    // The standard delete affordance: an icon button, not a word this grid
    // alone used. It carries its own label, so the row is still readable.
    const remove = within(grid()).getByRole('button', { name: 'Remove' });
    expect(remove.querySelector('svg')).not.toBeNull();
    expect(remove).not.toHaveTextContent('Remove');

    fireEvent.click(remove);
    // Removing a row throws away what she typed, so it is confirmed in the
    // standard words rather than being one click.
    expect(await screen.findByText('Confirm delete')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Remove Nurul Aisyah from this request? This action cannot be undone.',
      ),
    ).toBeInTheDocument();
    expect(onRemove).not.toHaveBeenCalled();

    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(onRemove).toHaveBeenCalledWith('p1'));
  });
});

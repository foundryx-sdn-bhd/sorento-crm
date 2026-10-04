/**
 * The Add user modal, S3 wiring (s3-contract.md 2.1, AC-48, AC-58, AC-59).
 *
 * Mocked at the SERVICE boundary (`../services/userService`, the contacts
 * service, the role-select hook, the companies service), never at `apiFetch` -
 * `userService.ts` and `contactService.ts` are being swapped from Phase 1
 * mocks to real calls by the coder while this file is written, and a spec
 * mocking `apiFetch` directly would silently start asserting against whatever
 * the mock constants used to say instead of the contract.
 *
 * `SearchableSelect` / `SearchableMultiSelect` are stubbed to plain, accessible
 * controls (the established pattern - see `SalesOrderFormModal.test.tsx`,
 * `SalesTeamModal.test.tsx`): the async WhatsApp contact field becomes a search
 * box + a button per fetched row, the static pickers become a native `<select>`
 * or a `<fieldset>` of checkboxes.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}
if (!('ResizeObserver' in window)) {
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? vi.fn();

/** A real-shaped UUID, so the "no UUID in rendered text" assertion means something. */
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

type StubOption = { value: string; label: string };

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    value: string;
    onChange: (v: string) => void;
    options?: StubOption[];
    fetchOptions?: (query: string, pageIndex: number) => Promise<StubOption[]>;
    selectedOption?: StubOption;
    placeholder?: string;
    disabled?: boolean;
    clearable?: boolean;
  }) => {
    const { value, onChange, options, fetchOptions, selectedOption, placeholder, disabled, clearable } =
      props;
    const [fetched, setFetched] = React.useState<StubOption[]>([]);
    React.useEffect(() => {
      if (!fetchOptions) return;
      let live = true;
      void fetchOptions('', 0).then((rows) => {
        if (live) setFetched(rows);
      });
      return () => {
        live = false;
      };
    }, [fetchOptions]);

    if (fetchOptions) {
      return (
        <div aria-label={placeholder} aria-disabled={disabled ? 'true' : 'false'}>
          <span data-testid="whatsapp-contact-value">{selectedOption?.label ?? value ?? ''}</span>
          {!disabled &&
            fetched.map((o) => (
              <button type="button" key={o.value} onClick={() => onChange(o.value)}>
                {o.label}
              </button>
            ))}
          {!disabled && clearable && value && (
            <button type="button" aria-label={`Clear ${placeholder}`} onClick={() => onChange('')}>
              Clear
            </button>
          )}
        </div>
      );
    }

    return (
      <select
        aria-label={placeholder}
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{placeholder}</option>
        {(options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  },
}));

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: {
    value: string[];
    onChange: (v: string[]) => void;
    options?: StubOption[];
    placeholder?: string;
  }) => (
    <fieldset aria-label={props.placeholder}>
      {(props.options ?? []).map((o) => (
        <label key={o.value}>
          <input
            type="checkbox"
            checked={props.value.includes(o.value)}
            onChange={(e) =>
              props.onChange(
                e.target.checked
                  ? [...props.value, o.value]
                  : props.value.filter((v) => v !== o.value),
              )
            }
          />
          {o.label}
        </label>
      ))}
    </fieldset>
  ),
}));

const apiFetchMock = vi.fn();
vi.mock('@/lib/api', () => ({
  apiFetch: (...a: unknown[]) => apiFetchMock(...a),
}));

vi.mock('@/lib/toast', () => ({
  toast: { custom: vi.fn(), success: vi.fn(), error: vi.fn() },
}));

const superadminRef = vi.hoisted(() => ({ current: false }));
vi.mock('@/lib/is-superadmin', () => ({ isSuperadminUser: () => superadminRef.current }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { email: 'admin@zzt.test' } } }),
}));

const routerPush = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush }),
}));

vi.mock('@/app/(protected)/system-management/companies/services/companyService', () => ({
  getCompaniesSelect: vi.fn(async () => [{ id: 'co-1', name: 'Sorento', code: 'SRT' }]),
}));

const ROLE_LIST = [
  { id: 'role-portal', slug: 'portal_user', name: 'Portal user' },
  { id: 'role-sales', slug: 'salesperson', name: 'Salesperson' },
];
// Mutable so a test can hold the roles back until after the contact loaded (N4).
const rolesRef = vi.hoisted(() => ({ current: null as unknown }));
vi.mock('../../roles/hooks/use-role-select-query', () => ({
  useRoleSelectQuery: () => ({ data: rolesRef.current }),
}));

// The contact fixture uses a real UUID shape - the "no UUID in the text"
// assertions below only prove something with an id that actually looks like one.
const CONTACT_LOCKED = {
  id: 'a1111111-1111-4111-8111-111111111111',
  name: 'Aisyah Rahman',
  phone_number: '+60123456789',
  suggested_role_slug: 'salesperson',
};
const CONTACT_PICKED = {
  id: 'b2222222-2222-4222-8222-222222222222',
  name: 'Zaid Hassan',
  phone_number: '+60120000000',
  suggested_role_slug: 'portal_user',
};
const CONTACTS_BY_ID: Record<string, typeof CONTACT_LOCKED> = {
  [CONTACT_LOCKED.id]: CONTACT_LOCKED,
  [CONTACT_PICKED.id]: CONTACT_PICKED,
};

const getContactMock = vi.fn(async (id: string) => CONTACTS_BY_ID[id]);
const getContactsMock = vi.fn();
const getContactCompaniesMock = vi.fn();
vi.mock('../../contacts/[id]/services/contactService', () => ({
  getContact: (id: string) => getContactMock(id),
  getContacts: (...a: unknown[]) => getContactsMock(...a),
  getContactCompanies: (...a: unknown[]) => getContactCompaniesMock(...a),
}));

const createUserMock = vi.fn();
const updateUserContactLinkMock = vi.fn();
vi.mock('../services/userService', () => ({
  createUser: (...a: unknown[]) => createUserMock(...a),
  updateUserContactLink: (...a: unknown[]) => updateUserContactLinkMock(...a),
}));

// Fix round 2, S2: every user lookup in this form (the 409 holder, Superior,
// Copy roles) goes through the shared `userSelectService` (CLAUDE.md hard rule).
const getUsersSelectMock = vi.fn();
vi.mock('@/services/userSelectService', () => ({
  getUsersSelect: (...a: unknown[]) => getUsersSelectMock(...a),
}));

import UserAddDialog from './user-add-dialog';

function codedRejection(code: string, message: string) {
  const error = new Error(message) as Error & { code: string };
  error.code = code;
  return Promise.reject(error);
}

function renderDialog(contact?: { id: string }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const closeDialog = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <UserAddDialog open closeDialog={closeDialog} contact={contact} />
    </QueryClientProvider>,
  );
  return { closeDialog };
}

async function tickRole(name: RegExp | string) {
  const roles = screen.getByRole('group', { name: 'Select roles' });
  const checkbox = within(roles).getByLabelText(name);
  fireEvent.click(checkbox);
  return checkbox;
}

beforeEach(() => {
  vi.clearAllMocks();
  superadminRef.current = false;
  apiFetchMock.mockResolvedValue({ ok: true, json: async () => [] } as unknown as Response);
  getContactsMock.mockResolvedValue({
    data: Object.values(CONTACTS_BY_ID),
    pagination: { total: 2 },
  });
  getContactCompaniesMock.mockResolvedValue([{ id: 'co-1', name: 'Sorento' }]);
  createUserMock.mockResolvedValue({ id: 'user-new' } as never);
  rolesRef.current = ROLE_LIST;
  getUsersSelectMock.mockResolvedValue([]);
  updateUserContactLinkMock.mockResolvedValue({});
});

afterEach(() => cleanup());

describe('UserAddDialog - S3, AC-58: no invitation checkbox, no /invite call', () => {
  it('has no "Send invitation email" checkbox', () => {
    renderDialog();
    expect(screen.queryByText(/send invitation email/i)).not.toBeInTheDocument();
  });

  it('submits through createUser and never calls an /invite URL', async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'New User' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'new@zzt.test' } });
    await tickRole('Portal user');

    const submit = screen.getByRole('button', { name: 'Add user' });
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);

    await waitFor(() => expect(createUserMock).toHaveBeenCalledTimes(1));
    expect(createUserMock).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'New User',
        email: 'new@zzt.test',
        respond_contact_id: null,
        role_ids: ['role-portal'],
      }),
    );
    expect(
      apiFetchMock.mock.calls.some((call) => String(call[0]).includes('/invite')),
    ).toBe(false);
  });
});

describe('UserAddDialog - S3 2.1: email optional with a phone, required without one', () => {
  it('submits with a phone and no email', async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Phone Only' } });
    fireEvent.change(screen.getByLabelText('Contact Number'), {
      target: { value: '+60110000567' },
    });
    await tickRole('Portal user');

    const submit = screen.getByRole('button', { name: 'Add user' });
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);

    await waitFor(() => expect(createUserMock).toHaveBeenCalledTimes(1));
  });

  it('blocks submit with neither email nor phone, naming the reason', async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Nothing Given' } });
    await tickRole('Portal user');

    const submit = screen.getByRole('button', { name: 'Add user' });
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);

    expect(await screen.findByText(/enter an email or a phone number/i)).toBeInTheDocument();
    expect(createUserMock).not.toHaveBeenCalled();
  });
});

describe('UserAddDialog - S3 2.1: opened from a contact, locked', () => {
  it('locks the WhatsApp contact, fills name/phone/companies, selects the suggested role', async () => {
    superadminRef.current = true;
    renderDialog({ id: CONTACT_LOCKED.id });

    await waitFor(() =>
      expect(screen.getByLabelText('Name')).toHaveValue(CONTACT_LOCKED.name),
    );
    expect(screen.getByLabelText('Contact Number')).toHaveValue(CONTACT_LOCKED.phone_number);
    expect(screen.getByLabelText('Contact Number')).toBeDisabled();

    const whatsapp = screen.getByLabelText('Link a WhatsApp contact (optional)');
    expect(whatsapp).toHaveAttribute('aria-disabled', 'true');

    const companies = screen.getByRole('group', { name: 'Select companies' });
    await waitFor(() =>
      expect(within(companies).getByLabelText('Sorento')).toBeChecked(),
    );

    const roles = screen.getByRole('group', { name: 'Select roles' });
    await waitFor(() =>
      expect(within(roles).getByLabelText('Salesperson')).toBeChecked(),
    );
    expect(screen.getByText(/suggested: salesperson/i)).toBeInTheDocument();

    expect(document.body.textContent ?? '').not.toMatch(UUID_RE);
  });

  it('plan 6.2 quick create: Add user is enabled and submits with nothing typed', async () => {
    renderDialog({ id: CONTACT_LOCKED.id });

    await waitFor(() =>
      expect(screen.getByLabelText('Name')).toHaveValue(CONTACT_LOCKED.name),
    );
    const roles = screen.getByRole('group', { name: 'Select roles' });
    await waitFor(() =>
      expect(within(roles).getByLabelText('Salesperson')).toBeChecked(),
    );

    const submit = screen.getByRole('button', { name: 'Add user' });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);

    await waitFor(() => expect(createUserMock).toHaveBeenCalledTimes(1));
    expect(createUserMock).toHaveBeenCalledWith(
      expect.objectContaining({ respond_contact_id: CONTACT_LOCKED.id }),
    );
  });

  it('does not overwrite a name already typed when a contact is picked', async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Custom Name' } });

    // Separator-agnostic: which glyph joins name and phone is the NEXT test's
    // job. This one is only about dirtyFields not being overwritten.
    const escapedPhone = CONTACT_PICKED.phone_number.replace(/[+.]/g, '\\$&');
    const pickButton = await screen.findByRole('button', {
      name: new RegExp(`${CONTACT_PICKED.name}.*${escapedPhone}`),
    });
    fireEvent.click(pickButton);

    await waitFor(() =>
      expect(screen.getByLabelText('Contact Number')).toHaveValue(CONTACT_PICKED.phone_number),
    );
    expect(screen.getByLabelText('Name')).toHaveValue('Custom Name');
  });

  it('contract 2.1: the WhatsApp contact option reads "name - phone", never an id', async () => {
    // s3-contract.md 2.1: "label `name - phone`, never an id". The dialog's own
    // `contactLabel` joins with ` · ` (a middle dot) instead of a hyphen - this
    // assertion is written to the contract's literal example, not the
    // component's actual separator, and is expected to fail until that's fixed.
    renderDialog();
    expect(
      await screen.findByText(`${CONTACT_PICKED.name} - ${CONTACT_PICKED.phone_number}`),
    ).toBeInTheDocument();
  });
});

describe('UserAddDialog - S3 2.1: inline 409s', () => {
  it('CONTACT_ALREADY_LINKED shows "Open user"', async () => {
    renderDialog({ id: CONTACT_LOCKED.id });
    await waitFor(() => expect(screen.getByLabelText('Name')).toHaveValue(CONTACT_LOCKED.name));
    // The suggested role (Salesperson) is already ticked by the contact-fill
    // effect - clicking it again would UNCHECK it and fail validation instead.
    await waitFor(() =>
      expect(
        within(screen.getByRole('group', { name: 'Select roles' })).getByLabelText('Salesperson'),
      ).toBeChecked(),
    );
    // The contact fill uses `setValue` with no `shouldDirty`, so the form
    // itself is not dirty yet (and "Add user" stays disabled) until something
    // is touched through a real field interaction.
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'admin-typed@zzt.test' } });

    createUserMock.mockImplementationOnce(() =>
      codedRejection('CONTACT_ALREADY_LINKED', 'WhatsApp contact already linked to Bob Lee'),
    );
    getUsersSelectMock.mockImplementation(async (params?: { respond_contact_id?: string }) =>
      params?.respond_contact_id ? [{ id: 'user-7', name: 'Bob Lee', email: null }] : [],
    );

    const submit = screen.getByRole('button', { name: 'Add user' });
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);

    expect(await screen.findByText('WhatsApp contact already linked to Bob Lee')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Open user' })).toBeInTheDocument();
    expect(getUsersSelectMock).toHaveBeenCalledWith({ respond_contact_id: CONTACT_LOCKED.id });
  });

  it('PHONE_BELONGS_TO_USER shows "Link this contact to <name> instead"', async () => {
    renderDialog({ id: CONTACT_LOCKED.id });
    await waitFor(() => expect(screen.getByLabelText('Name')).toHaveValue(CONTACT_LOCKED.name));
    await waitFor(() =>
      expect(
        within(screen.getByRole('group', { name: 'Select roles' })).getByLabelText('Salesperson'),
      ).toBeChecked(),
    );
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'admin-typed@zzt.test' } });

    createUserMock.mockImplementationOnce(() =>
      codedRejection('PHONE_BELONGS_TO_USER', 'This phone already belongs to Jane Tan.'),
    );
    getUsersSelectMock.mockImplementation(async (params?: { phone?: string }) =>
      params?.phone ? [{ id: 'user-42', name: 'Jane Tan', email: null }] : [],
    );

    const submit = screen.getByRole('button', { name: 'Add user' });
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);

    expect(await screen.findByText('This phone already belongs to Jane Tan.')).toBeInTheDocument();
    expect(
      await screen.findByRole('button', { name: 'Link this contact to Jane Tan instead' }),
    ).toBeInTheDocument();
    expect(getUsersSelectMock).toHaveBeenCalledWith({ phone: CONTACT_LOCKED.phone_number });
  });
});

describe('UserAddDialog - fix round 2, S2: user pickers via userSelectService', () => {
  it('loads Superior and Copy roles from getUsersSelect({ status: ACTIVE })', async () => {
    getUsersSelectMock.mockResolvedValue([{ id: 'user-9', name: 'Mei Ling', email: null }]);
    renderDialog();
    await waitFor(() => expect(getUsersSelectMock).toHaveBeenCalledWith({ status: 'ACTIVE' }));
    const superior = screen.getByRole('combobox', { name: 'None' });
    expect(await within(superior).findByRole('option', { name: 'Mei Ling' })).toBeInTheDocument();
    expect(
      apiFetchMock.mock.calls.some((call) => String(call[0]).includes('/users/select')),
    ).toBe(false);
  });
});

describe('UserAddDialog - fix round 2, S4: Companies required when a contact is set', () => {
  it('marks Companies required and blocks a superadmin save with none picked', async () => {
    superadminRef.current = true;
    getContactCompaniesMock.mockResolvedValue([]);
    renderDialog({ id: CONTACT_LOCKED.id });
    await waitFor(() => expect(screen.getByLabelText('Name')).toHaveValue(CONTACT_LOCKED.name));
    await waitFor(() =>
      expect(
        within(screen.getByRole('group', { name: 'Select roles' })).getByLabelText('Salesperson'),
      ).toBeChecked(),
    );

    expect(screen.getByText('Companies').closest('label')).toHaveTextContent('Companies *');

    fireEvent.click(screen.getByRole('button', { name: 'Add user' }));
    expect(await screen.findByText('Pick at least one company.')).toBeInTheDocument();
    expect(createUserMock).not.toHaveBeenCalled();
  });

  it('does not require Companies without a contact', async () => {
    superadminRef.current = true;
    renderDialog();
    expect(screen.getByText('Companies').closest('label')).toHaveTextContent(/^Companies$/);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'No Contact' } });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'nc@zzt.test' } });
    await tickRole('Portal user');
    fireEvent.click(screen.getByRole('button', { name: 'Add user' }));
    await waitFor(() => expect(createUserMock).toHaveBeenCalledTimes(1));
  });
});

describe('UserAddDialog - fix round 2, N4: the suggested role lands when roles load late', () => {
  it('selects the suggested role once roles arrive after the contact', async () => {
    rolesRef.current = undefined;
    renderDialog({ id: CONTACT_LOCKED.id });
    await waitFor(() => expect(screen.getByLabelText('Name')).toHaveValue(CONTACT_LOCKED.name));

    // Roles arrive; any re-render picks them up from the hook.
    rolesRef.current = ROLE_LIST;
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'late@zzt.test' } });

    const roles = screen.getByRole('group', { name: 'Select roles' });
    await waitFor(() => expect(within(roles).getByLabelText('Salesperson')).toBeChecked());
  });
});

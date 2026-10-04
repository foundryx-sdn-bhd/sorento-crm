/**
 * ============================================================================
 * Companies + access management - DETERMINISTIC MOCK BACKING STORE (Phase 1)
 * ============================================================================
 * PROTOTYPE DATA. No network, no `Math.random`, no `Date` - every value is a
 * hand-authored constant so the UI renders identically on every load. The CRUD
 * mutates in-memory arrays/maps so add / edit / delete / assign feel real within
 * a session; a page reload resets to this seed.
 *
 * Phase 2 flips `USE_COMPANY_MOCKS` to false in `services/companyService.ts`;
 * every function below already has its real `apiFetch` counterpart wired to the
 * contract, so the swap is one line + deleting the mock import. This store also
 * mirrors the two seed companies (`co-sorento` / `co-mocha`) that back the
 * top-right CompanySwitcher, so the admin screen and the switcher agree.
 * ============================================================================
 */
import type { Company, CompanyContact, CompanyFormData, CompanyUser } from '../types/company.types';

// ── Seed companies (match app/providers/CompanyProvider seed ids) ────────────

let companies: Company[] = [
  {
    id: 'co-sorento',
    name: 'Sorento',
    code: 'SRT',
    is_active: true,
    so_feed_live: true,
    autocount_ref: 'AC-SORENTO',
    logo_url: null,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'co-mocha',
    name: 'Mocha',
    code: 'MCH',
    is_active: true,
    so_feed_live: false,
    autocount_ref: 'AC-MOCHA',
    logo_url: null,
    created_at: '2026-07-01T00:00:00Z',
  },
];

// ── Access maps (prototype user_companies + respond_contact_companies) ───────

// Assigned users are stored denormalized (id/name/email) so the "remove" list
// renders without a second lookup - the real API returns the same shape.
const companyUsers: Record<string, CompanyUser[]> = {
  'co-sorento': [],
  'co-mocha': [],
};

const companyContactIds: Record<string, string[]> = {
  'co-sorento': ['ct-001', 'ct-002'],
  'co-mocha': ['ct-003'],
};

// Mock respond-contact catalog (the "add contact" picker source).
const MOCK_CONTACTS: CompanyContact[] = [
  { id: 'ct-001', name: 'Ahmad Rahman', phone: '+60 12-345 6789' },
  { id: 'ct-002', name: 'Siti Nurhaliza', phone: '+60 13-000 0554' },
  { id: 'ct-003', name: 'Wei Ming Lee', phone: '+60 16-000 0555' },
  { id: 'ct-004', name: 'Priya Suresh', phone: '+60 17-000 0210' },
  { id: 'ct-005', name: 'David Chong', phone: '+60 19-000 0556' },
];

// Deterministic id generator for mock-created companies (no Date/random).
let idCounter = 0;
const nextId = () => `co-new-${(idCounter += 1)}`;

// ── Derived counts ───────────────────────────────────────────────────────────

function withCounts(company: Company): Company {
  return {
    ...company,
    user_count: (companyUsers[company.id] ?? []).length,
    contact_count: (companyContactIds[company.id] ?? []).length,
  };
}

// ── Company CRUD ─────────────────────────────────────────────────────────────

export function mockListCompanies(): Company[] {
  return companies.map(withCounts);
}

export function mockGetCompany(id: string): Company {
  const row = companies.find((c) => c.id === id);
  if (!row) throw new Error('Company not found');
  return withCounts(row);
}

export function mockCreateCompany(body: CompanyFormData): Company {
  const row: Company = {
    id: nextId(),
    name: body.name,
    code: body.code,
    is_active: body.is_active,
    so_feed_live: body.so_feed_live,
    autocount_ref: body.autocount_ref || null,
    logo_url: body.logo_url || null,
    created_at: '2026-07-24T00:00:00Z',
  };
  companies = [...companies, row];
  companyUsers[row.id] = [];
  companyContactIds[row.id] = [];
  return withCounts(row);
}

export function mockUpdateCompany(id: string, body: CompanyFormData): Company {
  const idx = companies.findIndex((c) => c.id === id);
  if (idx === -1) throw new Error('Company not found');
  const row: Company = {
    ...companies[idx],
    name: body.name,
    code: body.code,
    is_active: body.is_active,
    so_feed_live: body.so_feed_live,
    autocount_ref: body.autocount_ref || null,
    logo_url: body.logo_url || null,
  };
  companies = companies.map((c) => (c.id === id ? row : c));
  return withCounts(row);
}

export function mockDeleteCompany(id: string): void {
  companies = companies.filter((c) => c.id !== id);
  delete companyUsers[id];
  delete companyContactIds[id];
}

// ── User access (prototype user_companies) ───────────────────────────────────

export function mockListCompanyUsers(companyId: string): CompanyUser[] {
  return (companyUsers[companyId] ?? []).map((u) => ({ ...u }));
}

export function mockAddCompanyUser(companyId: string, user: CompanyUser): CompanyUser[] {
  const current = companyUsers[companyId] ?? [];
  if (!current.some((u) => u.id === user.id)) {
    companyUsers[companyId] = [...current, { ...user }];
  }
  return mockListCompanyUsers(companyId);
}

export function mockRemoveCompanyUser(companyId: string, userId: string): CompanyUser[] {
  companyUsers[companyId] = (companyUsers[companyId] ?? []).filter((u) => u.id !== userId);
  return mockListCompanyUsers(companyId);
}

// ── Contact membership (prototype respond_contact_companies) ──────────────────

export function mockListCompanyContacts(companyId: string): CompanyContact[] {
  const ids = companyContactIds[companyId] ?? [];
  return ids
    .map((id) => MOCK_CONTACTS.find((c) => c.id === id))
    .filter((c): c is CompanyContact => Boolean(c))
    .map((c) => ({ ...c }));
}

/** Contacts not yet tagged to this company - the "add contact" picker source. */
export function mockAvailableContacts(companyId: string): CompanyContact[] {
  const assigned = new Set(companyContactIds[companyId] ?? []);
  return MOCK_CONTACTS.filter((c) => !assigned.has(c.id)).map((c) => ({ ...c }));
}

export function mockAddCompanyContact(companyId: string, contactId: string): CompanyContact[] {
  const current = companyContactIds[companyId] ?? [];
  if (!current.includes(contactId)) {
    companyContactIds[companyId] = [...current, contactId];
  }
  return mockListCompanyContacts(companyId);
}

export function mockRemoveCompanyContact(companyId: string, contactId: string): CompanyContact[] {
  companyContactIds[companyId] = (companyContactIds[companyId] ?? []).filter((id) => id !== contactId);
  return mockListCompanyContacts(companyId);
}

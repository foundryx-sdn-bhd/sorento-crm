import type {
  AccessOverride,
  ContactAccessInput,
  Registry,
  RegistryDomain,
  RegistryField,
} from '@/services/chatbotAccessService';

/**
 * Pure logic behind the access switches (mock v8): rows from the registry plus the roles a
 * contact holds plus its overrides. Keys are used for identity only; none of them is ever
 * shown (the UI prints labels).
 */

/** What a role grants: domain names and field keys. */
export interface GrantSet {
  id?: string;
  name?: string;
  domains: string[];
  fields: string[];
}

export interface AccessState {
  roles: GrantSet[];
  overrides: AccessOverride[];
}

export interface DetailRow {
  key: string;
  label: string;
  on: boolean;
  changed: boolean;
}

export interface StampRow {
  key: string;
  on: boolean;
  changed?: boolean;
  label: string;
  previewOn: string;
  previewOff: string;
}

export interface SwitchRow {
  key: string;
  label: string;
  description?: string | null;
  on: boolean;
  changed: boolean;
  addedCount: number;
  removedCount: number;
  needs?: string;
  blocked?: boolean;
  fields: DetailRow[];
  stamp?: StampRow;
}

export interface SwitchGroup {
  label: string;
  rows: SwitchRow[];
}

const REPORTS = 'Reports';
const OTHER = 'Other';

/** The suffix a stamp adds to a pick-list line, for the on/off preview. */
const STAMP_SUFFIX: Record<string, string> = {
  'stamp.incoming': 'has incoming',
  'stamp.product_attachment': 'has Product Photos',
};

const isStamp = (f: RegistryField) => f.key.startsWith('stamp.');
const isReport = (f: RegistryField) => f.kind === 'report';
const shown = (d: RegistryDomain) => d.supported !== false;

function stampPreview(f: RegistryField): { previewOn: string; previewOff: string } {
  const suffix = STAMP_SUFFIX[f.key] ?? 'has';
  return { previewOn: `1. Product A - ${suffix}\n2. Product B`, previewOff: '1. Product A\n2. Product B' };
}

function union(sets: string[][]): Set<string> {
  return new Set(sets.flat());
}

function applyOverrides(base: Set<string>, overrides: AccessOverride[], domains: boolean): Set<string> {
  const out = new Set(base);
  for (const o of overrides) {
    if (domains ? o.field_key !== null : o.field_key === null) continue;
    const key = domains ? o.domain_name : (o.field_key as string);
    if (o.granted) out.add(key);
    else out.delete(key);
  }
  return out;
}

/** A tick the roles alone give, and the one the contact ends up with. */
export function resolveState(state: AccessState) {
  const roleDomains = union(state.roles.map((r) => r.domains));
  const roleFields = union(state.roles.map((r) => r.fields));
  return {
    roleDomains,
    roleFields,
    domains: applyOverrides(roleDomains, state.overrides, true),
    fields: applyOverrides(roleFields, state.overrides, false),
  };
}

export function buildRows(registry: Registry, state: AccessState): SwitchGroup[] {
  const { roleDomains, roleFields, domains, fields } = resolveState(state);
  const byName = new Map(registry.domains.map((d) => [d.name, d]));
  const groups: SwitchGroup[] = [];
  const reportRows: SwitchRow[] = [];
  const groupFor = (label: string) => {
    let g = groups.find((x) => x.label === label);
    if (!g) {
      g = { label, rows: [] };
      groups.push(g);
    }
    return g;
  };
  const fieldChanged = (key: string) => fields.has(key) !== roleFields.has(key);

  for (const d of registry.domains.filter(shown)) {
    const details = d.fields.filter((f) => !isStamp(f) && !isReport(f));
    const stampField = d.fields.find(isStamp);
    const detailRows: DetailRow[] = details.map((f) => ({
      key: f.key,
      label: f.label,
      on: fields.has(f.key),
      changed: fieldChanged(f.key),
    }));
    const addedCount = detailRows.filter((f) => f.changed && f.on).length;
    const removedCount = detailRows.filter((f) => f.changed && !f.on).length;
    const stampChanged = stampField ? fieldChanged(stampField.key) : false;
    const row: SwitchRow = {
      key: d.name,
      label: d.access_label || d.label,
      description: d.access_description ?? null,
      on: domains.has(d.name),
      changed:
        domains.has(d.name) !== roleDomains.has(d.name) || detailRows.some((f) => f.changed) || stampChanged,
      addedCount,
      removedCount,
      fields: detailRows,
    };
    if (stampField) {
      row.stamp = {
        key: stampField.key,
        on: fields.has(stampField.key),
        changed: stampChanged,
        label: stampField.label,
        ...stampPreview(stampField),
      };
    }
    const groupLabel = d.access_section === 'reports' ? REPORTS : d.group || OTHER;
    if (groupLabel === REPORTS) reportRows.push(row);
    else groupFor(groupLabel).rows.push(row);

    for (const f of d.fields.filter(isReport)) {
      reportRows.push({
        key: f.key,
        label: f.label,
        on: fields.has(f.key),
        changed: fieldChanged(f.key),
        addedCount: 0,
        removedCount: 0,
        needs: byName.get(d.name)?.access_label || d.label,
        blocked: !domains.has(d.name),
        fields: [],
      });
    }
  }
  if (reportRows.length) groups.push({ label: REPORTS, rows: reportRows });
  // Reports last, wherever the registry listed them.
  groups.sort((a, b) => Number(a.label === REPORTS) - Number(b.label === REPORTS));
  return groups;
}

/** Which registry domain owns this row key, and whether the key is a field of it. */
function locate(registry: Registry, rowKey: string): { domain: string; field: string | null } | null {
  for (const d of registry.domains) {
    if (d.name === rowKey) return { domain: d.name, field: null };
    if (d.fields.some((f) => f.key === rowKey)) return { domain: d.name, field: rowKey };
  }
  return null;
}

/** Drop every override a row owns: its domain and its details, but not a report living under it. */
export function resetRow(
  overrides: AccessOverride[],
  rowKey: string,
  registry: Registry,
): AccessOverride[] {
  const loc = locate(registry, rowKey);
  if (!loc) return overrides;
  if (loc.field) return overrides.filter((o) => o.field_key !== loc.field);
  const domain = registry.domains.find((d) => d.name === loc.domain);
  const reports = new Set((domain?.fields ?? []).filter(isReport).map((f) => f.key));
  return overrides.filter(
    (o) => !(o.domain_name === loc.domain && (o.field_key === null || !reports.has(o.field_key))),
  );
}

/**
 * Flip a domain or a field. The new value is stored as an override only while it differs
 * from what the roles give; flipping back removes the override.
 */
export function toggleKey(
  overrides: AccessOverride[],
  rowKey: string,
  registry: Registry,
  roles: GrantSet[],
): AccessOverride[] {
  const loc = locate(registry, rowKey);
  if (!loc) return overrides;
  const state = resolveState({ roles, overrides });
  const isField = loc.field !== null;
  const current = isField ? state.fields.has(rowKey) : state.domains.has(rowKey);
  const roleHas = isField ? state.roleFields.has(rowKey) : state.roleDomains.has(rowKey);
  const next = !current;
  const rest = overrides.filter((o) =>
    isField ? o.field_key !== rowKey : !(o.field_key === null && o.domain_name === rowKey),
  );
  if (next === roleHas) return rest;
  return [...rest, { domain_name: loc.domain, field_key: loc.field, granted: next }];
}

const overrideKey = (o: AccessOverride) => `${o.domain_name}|${o.field_key ?? ''}`;

/** How many things differ between two saved states, and the one full payload to send. */
export function diffAccess(
  initial: ContactAccessInput,
  current: ContactAccessInput,
): { count: number; payload: ContactAccessInput } {
  const a = new Set(initial.role_ids);
  const b = new Set(current.role_ids);
  let count = 0;
  for (const id of a) if (!b.has(id)) count += 1;
  for (const id of b) if (!a.has(id)) count += 1;
  const before = new Map(initial.overrides.map((o) => [overrideKey(o), o.granted]));
  const after = new Map(current.overrides.map((o) => [overrideKey(o), o.granted]));
  for (const [k, g] of before) if (!after.has(k) || after.get(k) !== g) count += 1;
  for (const k of after.keys()) if (!before.has(k)) count += 1;
  return { count, payload: current };
}

/** Number of ticks that differ between two grant sets (role page). */
export function diffGrants(a: GrantSet, b: GrantSet): number {
  const sym = (x: string[], y: string[]) => {
    const sx = new Set(x);
    const sy = new Set(y);
    return [...sx].filter((k) => !sy.has(k)).length + [...sy].filter((k) => !sx.has(k)).length;
  };
  return sym(a.domains, b.domains) + sym(a.fields, b.fields);
}

/** Flip a domain or field in a plain grant set (role page: no overrides). */
export function toggleGrant(grants: GrantSet, rowKey: string, registry: Registry): GrantSet {
  const loc = locate(registry, rowKey);
  if (!loc) return grants;
  const list = loc.field ? grants.fields : grants.domains;
  const next = list.includes(rowKey) ? list.filter((k) => k !== rowKey) : [...list, rowKey];
  return loc.field ? { ...grants, fields: next } : { ...grants, domains: next };
}

/**
 * Access model S6, mock v8 (AC-AM-4b, AC-AM-5, AC-AM-7, AC-AM-22, AC-AM-24, AC-AM-25).
 * Red test: lib/chatbot-access/tree.ts does not export buildRows / resetRow / diffAccess yet.
 *
 * Contract pinned here:
 *   registry.domains[]: { name, label, group (plain-English section label), access_section, fields[] }
 *     field { key, label, kind:'field'|'report' }; a key starting 'stamp.' is a stamp, not a detail.
 *   buildRows(registry, { roles, overrides }) -> Group[]
 *     Group = { label, rows: Row[] }; groups in order of first appearance, 'Reports' group last.
 *     Row = { key, label, on, changed, addedCount, removedCount, needs?, blocked?,
 *             fields:[{key,label,on,changed}], stamp?:{key,on} }
 *     Domain row key = domain name; a report-kind field is a row in the Reports group keyed by field key.
 *   Role grants: role.domains (names), role.fields (field keys). Override { domain_name, field_key|null, granted }.
 *   resetRow(overrides, rowKey, registry) -> overrides without any entry for that row or its fields.
 *   diffAccess(initial, current) -> { count, payload } for state { role_ids, overrides }.
 */
import { describe, it, expect } from 'vitest';

import { buildRows, resetRow, diffAccess } from './tree';

const registry = {
  domains: [
    { name: 'inventory', label: 'Stock available', group: 'Stock and incoming', access_section: null, fields: [
      { key: 'inventory.sellable', label: 'Outstanding sales orders on stock', kind: 'field' },
      { key: 'low_stock', label: 'Low stock report', kind: 'report' },
    ] },
    { name: 'incoming', label: 'Incoming stock', group: 'Stock and incoming', access_section: null, fields: [
      { key: 'incoming.eta', label: 'ETA', kind: 'field' },
      { key: 'incoming.consignee', label: 'Consignee', kind: 'field' },
      { key: 'stamp.incoming', label: 'Show has / no incoming in pick lists', kind: 'field' },
    ] },
    { name: 'order', label: 'Orders and deliveries', group: 'Orders', access_section: null, fields: [] },
    { name: 'sales', label: 'Sales report', group: 'Reports', access_section: 'reports', fields: [] },
  ],
};
const purchasing = { id: 'r1', name: 'Purchasing', domains: ['inventory', 'incoming'], fields: ['inventory.sellable', 'incoming.eta', 'stamp.incoming'] };

const rowOf = (g: ReturnType<typeof buildRows>, key: string) => g.flatMap((x) => x.rows).find((r) => r.key === key)!;

describe('buildRows', () => {
  it('AC-AM-7 groups rows by the registry group label, Reports last', () => {
    const g = buildRows(registry, { roles: [purchasing], overrides: [] });
    expect(g.map((x) => x.label)).toEqual(['Stock and incoming', 'Orders', 'Reports']);
    expect(g[0].rows.map((r) => r.label)).toEqual(['Stock available', 'Incoming stock']);
  });

  it('AC-AM-4b a reports-section domain and a report field both land in Reports; the report field needs its domain', () => {
    const g = buildRows(registry, { roles: [purchasing], overrides: [] });
    const reports = g.find((x) => x.label === 'Reports')!.rows.map((r) => r.key);
    expect(reports).toEqual(expect.arrayContaining(['sales', 'low_stock']));
    expect(rowOf(g, 'low_stock').needs).toBe('Stock available');
    expect(rowOf(g, 'inventory').fields.map((f) => f.key)).toEqual(['inventory.sellable']);
  });

  it('AC-AM-4b a report is blocked while its owning domain is off and granting it never grants the domain', () => {
    const g = buildRows(registry, { roles: [{ id: 'r', name: 'R', domains: [], fields: ['low_stock'] }], overrides: [] });
    expect(rowOf(g, 'low_stock').blocked).toBe(true);
    expect(rowOf(g, 'inventory').on).toBe(false);
  });

  it('AC-AM-7 a row that follows the roles is not changed', () => {
    const g = buildRows(registry, { roles: [purchasing], overrides: [] });
    expect(rowOf(g, 'inventory')).toMatchObject({ on: true, changed: false, addedCount: 0, removedCount: 0 });
    expect(rowOf(g, 'order')).toMatchObject({ on: false, changed: false });
  });

  it('AC-AM-7 an add override on a detail marks the row changed with addedCount', () => {
    const g = buildRows(registry, { roles: [purchasing], overrides: [{ domain_name: 'incoming', field_key: 'incoming.consignee', granted: true }] });
    const r = rowOf(g, 'incoming');
    expect(r.changed).toBe(true);
    expect(r.addedCount).toBe(1);
    expect(r.fields.find((f) => f.key === 'incoming.consignee')).toMatchObject({ on: true, changed: true });
    expect(r.fields.find((f) => f.key === 'incoming.eta')).toMatchObject({ on: true, changed: false });
  });

  it('AC-AM-5 a remove override beats the role: domain off, changed', () => {
    const g = buildRows(registry, { roles: [purchasing], overrides: [{ domain_name: 'inventory', field_key: null, granted: false }] });
    expect(rowOf(g, 'inventory')).toMatchObject({ on: false, changed: true });
  });

  it('AC-AM-5 a field remove override raises removedCount', () => {
    const g = buildRows(registry, { roles: [purchasing], overrides: [{ domain_name: 'inventory', field_key: 'inventory.sellable', granted: false }] });
    expect(rowOf(g, 'inventory')).toMatchObject({ changed: true, removedCount: 1 });
  });

  it('AC-AM-22 several roles union their grants', () => {
    const other = { id: 'r2', name: 'Dealer', domains: ['order'], fields: [] };
    const g = buildRows(registry, { roles: [purchasing, other], overrides: [] });
    expect(rowOf(g, 'order').on).toBe(true);
    expect(rowOf(g, 'inventory').on).toBe(true);
  });

  it('AC-AM-24 the stamp is separate from the details and carries its own on state', () => {
    const g = buildRows(registry, { roles: [purchasing], overrides: [] });
    const r = rowOf(g, 'incoming');
    expect(r.stamp).toMatchObject({ key: 'stamp.incoming', on: true });
    expect(r.fields.map((f) => f.key)).not.toContain('stamp.incoming');
    const off = buildRows(registry, { roles: [purchasing], overrides: [{ domain_name: 'incoming', field_key: 'stamp.incoming', granted: false }] });
    expect(rowOf(off, 'incoming').stamp).toMatchObject({ on: false });
    expect(rowOf(off, 'incoming').changed).toBe(true);
  });
});

describe('resetRow', () => {
  const ov = [
    { domain_name: 'incoming', field_key: 'incoming.consignee', granted: true },
    { domain_name: 'incoming', field_key: null, granted: true },
    { domain_name: 'inventory', field_key: 'inventory.sellable', granted: false },
  ];
  it('AC-AM-7 removes every override for the domain and its fields, keeps the rest', () => {
    expect(resetRow(ov, 'incoming', registry)).toEqual([ov[2]]);
  });
  it('AC-AM-7 a report row resets its own field override', () => {
    const o = [{ domain_name: 'inventory', field_key: 'low_stock', granted: true }, ov[2]];
    expect(resetRow(o, 'low_stock', registry)).toEqual([ov[2]]);
  });
});

describe('diffAccess', () => {
  const initial = { role_ids: ['r1'], overrides: [{ domain_name: 'incoming', field_key: 'incoming.consignee', granted: true }] };
  it('AC-AM-7 counts nothing when unchanged', () => {
    expect(diffAccess(initial, { ...initial }).count).toBe(0);
  });
  it('AC-AM-7 counts role and override changes and returns one full payload', () => {
    const current = {
      role_ids: ['r1', 'r2'],
      overrides: [],
    };
    const d = diffAccess(initial, current);
    expect(d.count).toBe(2);
    expect(d.payload).toEqual(current);
  });
});

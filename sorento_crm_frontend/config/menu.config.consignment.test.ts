/** AC-CONSIGN-CRM UAC-19: a Consignment item sits right after Sales Orders in Project Sales Admin. */
import { describe, expect, it } from 'vitest';
import { MENU_SIDEBAR } from './menu.config';

describe('menu.config - Consignment under Project Sales Admin (UAC-19)', () => {
  it('is the child right after Sales Orders, same permission and module', () => {
    const psa = MENU_SIDEBAR.find((item) => !item.heading && item.title === 'Project Sales Admin');
    expect(psa).toBeDefined();
    const children = psa!.children!;
    const at = children.findIndex((c) => c.title === 'Sales Orders');
    expect(at).toBeGreaterThanOrEqual(0);
    expect(children[at + 1]).toMatchObject({
      title: 'Consignment',
      path: '/scm/consignments',
      permission: 'scm.dashboard.view',
      moduleKey: 'scm',
    });
  });
});

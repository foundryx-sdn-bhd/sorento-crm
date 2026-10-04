/** AC-16: Order Management > Customer Groups sits right after Customers, same view permission. */
import { describe, expect, it } from 'vitest';
import { MENU_SIDEBAR } from './menu.config';
import type { MenuConfig, MenuItem } from './types';

function findParentOf(items: MenuConfig, path: string): MenuItem[] | undefined {
  for (const item of items) {
    if (item.children) {
      if (item.children.some((c) => c.path === path)) return item.children;
      const deeper = findParentOf(item.children, path);
      if (deeper) return deeper;
    }
  }
  return undefined;
}

describe('MENU_SIDEBAR - Customer Groups entry', () => {
  it('is Customer Groups -> /order-management/customer-groups, right after Customers', () => {
    const siblings = findParentOf(MENU_SIDEBAR, '/order-management/customers');
    expect(siblings).toBeDefined();
    const customersAt = siblings!.findIndex((c) => c.path === '/order-management/customers');
    expect(siblings![customersAt + 1]).toMatchObject({
      title: 'Customer Groups',
      path: '/order-management/customer-groups',
      permission: 'order_management.customers.view',
    });
  });
});

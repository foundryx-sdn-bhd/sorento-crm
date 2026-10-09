/**
 * CLAUDE-CONNECTOR S3 red test: the admin page is reachable from the sidebar under
 * "Users & Access", gated by the connector manage slug (backend registry:
 * analytics.connector.manage).
 */
import { describe, expect, it } from 'vitest';
import { MENU_SIDEBAR } from './menu.config';
import type { MenuConfig, MenuItem } from './types';

function find(items: MenuConfig, path: string, trail: MenuItem[] = []): MenuItem[] | undefined {
  for (const item of items) {
    if (item.path === path) return [...trail, item];
    if (item.children) {
      const hit = find(item.children, path, [...trail, item]);
      if (hit) return hit;
    }
  }
  return undefined;
}

describe('Claude connector menu entry', () => {
  it('sits under Users & Access and needs analytics.connector.manage', () => {
    const trail = find(MENU_SIDEBAR, '/user-management/claude-connector');
    expect(trail, 'menu entry missing').toBeDefined();
    const leaf = trail![trail!.length - 1];
    expect(leaf.permission).toBe('analytics.connector.manage');
    expect(leaf.title).toMatch(/claude connector/i);
    expect(trail!.some((t) => t.title === 'Users & Access')).toBe(true);
  });
});

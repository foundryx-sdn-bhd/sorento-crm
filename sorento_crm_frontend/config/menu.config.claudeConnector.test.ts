/**
 * CLAUDE-CONNECTOR r3 red test: the grants admin moved to the shared service (contract section 0),
 * so the sidebar has no Claude connector entry and nothing gated by analytics.connector.manage.
 */
import { describe, expect, it } from 'vitest';
import { MENU_SIDEBAR } from './menu.config';
import type { MenuConfig, MenuItem } from './types';

function walk(items: MenuConfig, out: MenuItem[] = []): MenuItem[] {
  for (const item of items) {
    out.push(item);
    if (item.children) walk(item.children, out);
  }
  return out;
}

describe('Claude connector menu entry', () => {
  it('is gone from the sidebar', () => {
    const all = walk(MENU_SIDEBAR);
    expect(all.find((i) => i.path === '/user-management/claude-connector')).toBeUndefined();
    expect(all.filter((i) => /claude connector/i.test(String(i.title ?? '')))).toEqual([]);
    expect(all.filter((i) => i.permission === 'analytics.connector.manage')).toEqual([]);
  });
});

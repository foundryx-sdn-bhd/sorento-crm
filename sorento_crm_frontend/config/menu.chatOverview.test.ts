/**
 * CA-1 facts: the Chat Overview report sits directly after Chat History in the
 * system-management sidebar and compact sidebar, gated by `chatbot.analytics.view`, its route maps
 * to the `base` module, and the page is the kernel ReportPage with key chat_overview.
 */
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { MENU_SIDEBAR, MENU_SIDEBAR_COMPACT } from './menu.config';
import { moduleKeyForPath } from '@/lib/route-module-map';

type Node = {
  title?: string;
  path?: string;
  permission?: string;
  children?: Node[];
};

const HISTORY = '/system-management/chat-history';

function findSiblings(tree: Node[]): Node[] | null {
  for (const n of tree) {
    if (n.path === HISTORY) return tree;
    if (n.children) {
      const hit = findSiblings(n.children);
      if (hit) return hit;
    }
  }
  return null;
}

function nextAfterHistory(tree: Node[]): Node | undefined {
  const siblings = findSiblings(tree);
  expect(siblings, 'Chat History list not found').not.toBeNull();
  const idx = siblings!.findIndex((c) => c.path === HISTORY);
  return siblings![idx + 1];
}

describe('Chat Overview menu', () => {
  it('sidebar: Chat Overview directly follows Chat History', () => {
    const item = nextAfterHistory(MENU_SIDEBAR as Node[]);
    expect(item?.title).toBe('Chat Overview');
    expect(item?.path).toBe('/system-management/chat-overview');
    expect(item?.permission).toBe('chatbot.analytics.view');
  });

  it('compact menu: Chat Overview directly follows Chat History', () => {
    const item = nextAfterHistory(MENU_SIDEBAR_COMPACT as Node[]);
    expect(item?.title).toBe('Chat Overview');
    expect(item?.path).toBe('/system-management/chat-overview');
    expect(item?.permission).toBe('chatbot.analytics.view');
  });

  it('maps the route to the base module', () => {
    expect(moduleKeyForPath('/system-management/chat-overview')).toBe('base');
  });

  it('page is the kernel ReportPage with reportKey chat_overview', () => {
    const root = path.resolve(__dirname, '..');
    const page = path.join(
      root,
      'app/(protected)/system-management/chat-overview/page.tsx',
    );
    expect(existsSync(page)).toBe(true);
    const src = readFileSync(page, 'utf8');
    expect(src).toContain('reportKey="chat_overview"');
    expect(src).toMatch(
      /import\s+\{[^}]*\bReportPage\b[^}]*\}\s+from\s+['"]@\/components\/reports\/ReportPage['"]/,
    );
  });
});

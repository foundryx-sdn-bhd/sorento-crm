/**
 * CA-2 taxonomy (tester-first RED): Top Asks, Bot Failures and Topic Audit follow Chat Overview
 * in the system-management sidebar and the compact sidebar, in that order. Top Asks and Bot
 * Failures are gated `chatbot.analytics.view`; Topic Audit is gated `system.chat_history.view`
 * (a menu item takes one slug; the server enforces both). The two report pages are the kernel
 * ReportPage with keys chat_top_asks / chat_bot_failures. Plan: PLAN-chat-analytics-ca2-taxonomy-09oct.md.
 */
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { MENU_SIDEBAR, MENU_SIDEBAR_COMPACT } from './menu.config';

type Node = {
  title?: string;
  path?: string;
  permission?: string;
  children?: Node[];
};

const OVERVIEW = '/system-management/chat-overview';

const EXPECTED = [
  {
    title: 'Top Asks',
    path: '/system-management/top-asks',
    permission: 'chatbot.analytics.view',
  },
  {
    title: 'Bot Failures',
    path: '/system-management/bot-failures',
    permission: 'chatbot.analytics.view',
  },
  {
    title: 'Topic Audit',
    path: '/system-management/topic-audit',
    permission: 'system.chat_history.view',
  },
];

function findSiblings(tree: Node[]): Node[] | null {
  for (const n of tree) {
    if (n.path === OVERVIEW) return tree;
    if (n.children) {
      const hit = findSiblings(n.children);
      if (hit) return hit;
    }
  }
  return null;
}

function afterOverview(tree: Node[]): Node[] {
  const siblings = findSiblings(tree);
  expect(siblings, 'Chat Overview not found').not.toBeNull();
  const idx = siblings!.findIndex((c) => c.path === OVERVIEW);
  return siblings!.slice(idx + 1, idx + 1 + EXPECTED.length);
}

describe('Top Asks, Bot Failures, Topic Audit menu', () => {
  it('sidebar: the three entries follow Chat Overview in order', () => {
    const items = afterOverview(MENU_SIDEBAR as Node[]).map((n) => ({
      title: n.title,
      path: n.path,
      permission: n.permission,
    }));
    expect(items).toEqual(EXPECTED);
  });

  it('compact menu: the three entries follow Chat Overview in order', () => {
    const items = afterOverview(MENU_SIDEBAR_COMPACT as Node[]).map((n) => ({
      title: n.title,
      path: n.path,
      permission: n.permission,
    }));
    expect(items).toEqual(EXPECTED);
  });

  const root = path.resolve(__dirname, '..');
  const pageSrc = (slug: string) =>
    path.join(root, `app/(protected)/system-management/${slug}/page.tsx`);

  it.each([
    ['top-asks', 'chat_top_asks'],
    ['bot-failures', 'chat_bot_failures'],
  ])('%s page is the kernel ReportPage with reportKey %s', (slug, key) => {
    expect(existsSync(pageSrc(slug))).toBe(true);
    const src = readFileSync(pageSrc(slug), 'utf8');
    expect(src).toContain(`reportKey="${key}"`);
    expect(src).toMatch(
      /import\s+\{[^}]*\bReportPage\b[^}]*\}\s+from\s+['"]@\/components\/reports\/ReportPage['"]/,
    );
  });

  it('topic-audit page exists', () => {
    expect(existsSync(pageSrc('topic-audit'))).toBe(true);
  });
});

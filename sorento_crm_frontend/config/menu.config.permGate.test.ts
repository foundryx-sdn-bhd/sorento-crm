/**
 * MENU-PERM-GATE (PLAN-menu-perm-gate-06oct.md, AC-4): every entry the owner saw leak into a
 * retail salesperson's sidebar names the EXISTING registry slug of the page it opens
 * (sorento_crm_backend/app/rbac/permission_registry.py). Nothing here is a new slug.
 */
import { describe, expect, it } from 'vitest';
import { MENU_SIDEBAR } from './menu.config';
import type { MenuConfig, MenuItem } from './types';

function leafByPath(items: MenuConfig, path: string): MenuItem | undefined {
  for (const item of items) {
    if (item.path === path) return item;
    if (item.children) {
      const found = leafByPath(item.children, path);
      if (found) return found;
    }
  }
  return undefined;
}

const SINGLE_SLUG: Array<[string, string]> = [
  ['/marketing-management/promotions', 'marketing.promotions.view'],
  [
    '/marketing-management/promotion-attachments',
    'marketing.promotion_attachments.view',
  ],
  ['/marketing-management/promotion-types', 'marketing.promotion_types.view'],
  [
    '/marketing-management/promotion-products',
    'marketing.promotion_products.view',
  ],
  ['/marketing-management/campaigns', 'marketing.campaigns.view'],
  ['/complaint-management/complaints', 'complaint_management.complaints.view'],
  ['/sla-management/sla-policies', 'sla_management.sla_policies.view'],
  [
    '/sla-management/conversation-sla-tracking',
    'sla_management.conversation_sla_tracking.view',
  ],
  [
    '/sla-management/form-sla-tracking',
    'sla_management.conversation_sla_tracking.view',
  ],
  [
    '/sla-management/team-pending',
    'sla_management.conversation_sla_tracking.view',
  ],
  ['/sla-management/form-sla-config', 'sla_management.form_sla_config.view'],
  ['/sla-management/escalation-logs', 'sla_management.escalation_logs.view'],
  ['/forms-management/forms', 'forms.forms.view'],
  ['/resource-management/trash', 'resource.attachments.view'],
  ['/resource-management/attachment-types', 'resource.attachment_types.view'],
  ['/integration-management/integrations', 'integration.integrations.view'],
];

describe('menu.config - permission gates (MENU-PERM-GATE)', () => {
  it.each(SINGLE_SLUG)('%s is gated by %s', (path, slug) => {
    const leaf = leafByPath(MENU_SIDEBAR, path);
    expect(leaf, `${path} is in MENU_SIDEBAR`).toBeDefined();
    expect(leaf!.permission).toBe(slug);
  });

  it('Dashboards opens the My pending widget and the SLA KPI dashboard, so either slug shows it', () => {
    const leaf = leafByPath(MENU_SIDEBAR, '/');
    expect(leaf).toBeDefined();
    expect(leaf!.permission).toBeUndefined();
    expect([...(leaf!.permissionsAny ?? [])].sort()).toEqual(
      ['sla.kpi.view', 'sla_management.conversation_sla_tracking.view'].sort(),
    );
  });

  it('Files is the attachment drive, so either the attachments or the directories view slug shows it', () => {
    const leaf = leafByPath(
      MENU_SIDEBAR,
      '/resource-management/attachment-directories',
    );
    expect(leaf).toBeDefined();
    expect(leaf!.permission).toBeUndefined();
    expect([...(leaf!.permissionsAny ?? [])].sort()).toEqual(
      [
        'resource.attachment_directories.view',
        'resource.attachments.view',
      ].sort(),
    );
  });

  it('every leaf of MENU_SIDEBAR is gated by a permission or marked superadminOnly', () => {
    const ungated: string[] = [];
    function walk(items: MenuConfig) {
      for (const item of items) {
        if (
          item.path &&
          !item.permission &&
          !item.permissionsAny?.length &&
          !item.superadminOnly
        ) {
          ungated.push(item.path);
        }
        if (item.children) walk(item.children);
      }
    }
    walk(MENU_SIDEBAR);
    expect(ungated).toEqual([]);
  });
});

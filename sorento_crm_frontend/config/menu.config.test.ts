/**
 * Sidebar wiring tests for MENU_SIDEBAR reorganisation (Slice 5 of
 * PLAN-module-access-gates.md).
 *
 * Validates that the reorganised sidebar preserves every existing path, places
 * moved entries in their new groups, and applies the correct gates.
 */
import { describe, expect, it } from 'vitest';
import { MENU_SIDEBAR, MENU_SIDEBAR_COMPACT } from './menu.config';
import { filterOrphanHeadings } from '@/lib/menu-access-filter';
import type { MenuConfig, MenuItem } from './types';

/** Recursively collect all `path` values from a menu tree. */
function collectPaths(items: MenuConfig): Set<string> {
  const paths = new Set<string>();
  for (const item of items) {
    if (item.path) paths.add(item.path);
    if (item.children) {
      for (const p of collectPaths(item.children)) {
        paths.add(p);
      }
    }
  }
  return paths;
}

/** Find a top-level group (non-heading) by title in a menu. */
function findGroup(menu: MenuConfig, title: string): MenuItem | undefined {
  return menu.find((item) => !item.heading && item.title === title);
}

/** Find a sub-group (child without a path) by title under a parent group. */
function findSubGroup(parent: MenuItem, title: string): MenuItem | undefined {
  return parent.children?.find((item) => item.title === title && !item.path && item.children);
}

/** Find a leaf by title within a menu array (non-recursive). */
function findLeaf(items: MenuConfig, title: string): MenuItem | undefined {
  return items.find((item) => item.title === title && item.path);
}

// ---------------------------------------------------------------------------
// Path preservation
// ---------------------------------------------------------------------------

/**
 * The old MENU_SIDEBAR paths that must appear in the new one. The only
 * intentional removal is `/system-management/outgoing-mails`, which was
 * converted from a leaf into a sub-group container (Outgoing Mails) with
 * Email Outbox and Respond Outbox as children.
 *
 * `/scm/incoming` (Incoming Containers) is no longer tracked here at all (S3,
 * AC-C3, supplier documents / proforma invoice first lane, 9 Sep 2026): the page
 * itself is deleted, not just retired from the menu, so it is dropped from
 * `OLD_PATHS` below rather than added to this set.
 */
const INTENTIONALLY_REMOVED_PATHS = new Set<string>();

/** Snapshot of every leaf path from the pre-reorganisation MENU_SIDEBAR. */
const OLD_PATHS: string[] = [
  '/',
  '/ideas',
  '/user-management/users',
  '/user-management/onboarding-requests',
  '/user-management/roles',
  '/user-management/permissions',
  '/user-management/access-agents',
  '/user-management/teams',
  '/user-management/contact-access-agents',
  '/user-management/contact-access-types',
  '/user-management/market-segments',
  '/master-data-management/sales-agents',
  '/user-management/account',
  '/user-management/logs',
  '/user-management/settings',
  '/scm',
  '/scm/reorder',
  '/scm/loading-plan',
  '/scm/proforma-invoices',
  '/scm/policies',
  '/scm/sales-orders',
  '/scm/purchase-orders',
  '/scm/market-signals',
  '/scm/simulation',
  '/dealer-kit',
  '/dealer-kit/collections',
  '/dealer-kit/tile-designs',
  '/dealer-kit/brochure-images',
  '/dealer-kit/flyer-readings',
  '/dealer-kit/editions',
  '/dealer-kit/bundles',
  '/dealer-kit/design',
  '/dealer-kit/design/summary',
  '/order-management/orders',
  '/order-management/order-statuses',
  '/order-management/customers',
  '/complaint-management/complaints',
  '/complaint-management/complaint-root-causes',
  '/complaint-management/complaint-resolutions',
  '/sla-management/sla-policies',
  '/sla-management/conversations',
  '/sla-management/conversation-sla-tracking',
  '/sla-management/form-sla-tracking',
  '/sla-management/team-pending',
  '/sla-management/form-sla-config',
  '/sla-management/escalation-logs',
  '/sla-management/message-snippets',
  '/sla-management/kpi-dashboard',
  '/master-data-management/products',
  '/master-data-management/product-attachments',
  '/master-data-management/certificates',
  '/master-data-management/product-categories',
  '/master-data-management/product-specifications',
  '/master-data-management/spec-verification',
  '/master-data-management/flyer-spec-proposals',
  '/master-data-management/brands',
  '/master-data-management/units-of-measure',
  '/procurement-management/suppliers',
  '/procurement-management/product-suppliers',
  '/procurement-management/packing-lists',
  '/procurement-management/spo-allocations',
  '/procurement-management/grn',
  '/procurement-management/picking-lines',
  '/procurement-management/stock-inquiries',
  '/procurement-management/purchase-requests',
  '/procurement-management/sponsorship-forms',
  '/inventory-management/warehouses',
  '/inventory-management/storage-zones',
  '/inventory-management/stock',
  '/inventory-management/stock-batches',
  '/inventory-management/stock-ledger',
  '/project-sales/pipeline',
  '/project-sales/leads',
  '/project-sales/lead-acceptance',
  '/project-sales/my-tasks',
  '/project-sales/stock-claims',
  '/project-sales/divergences',
  '/project-sales/fulfilment-planning',
  '/project-sales/plans',
  '/project-sales/order-inquiries',
  '/project-sales/planning-changes',
  '/project-sales/reports',
  '/project-sales/parties',
  '/project-sales/setup',
  '/project-sales/series',
  '/project-sales/price-floors',
  '/marketing-management/promotions',
  '/marketing-management/promotion-attachments',
  '/marketing-management/promotion-types',
  '/marketing-management/promotion-products',
  '/marketing-management/campaigns',
  '/forms-management/forms',
  '/workflow-forms-management/definitions',
  '/resource-management/attachment-directories',
  '/resource-management/trash',
  '/resource-management/attachment-types',
  '/system-management/companies',
  '/system-management/app-store',
  '/system-management/app-store/bundles',
  '/system-management/import-jobs',
  '/system-management/import-logs',
  '/system-management/tracking-validation',
  '/system-management/audit-logs',
  '/system-management/health',
  '/system-management/activity',
  '/integration-management/integrations',
  '/integration-management/integration-logs',
  '/integration-management/whatsapp-templates',
  '/system-management/scheduled-tasks',
  '/system-management/outgoing-mails',
  '/system-management/email-outbox',
  '/system-management/respond-outbox',
  '/system-management/chat-history',
  '/system-management/api-call-logs',
  '/system-management/email-event-configs',
  '/system-management/email-templates',
  '/system-management/automation',
  '/system-management/work-calendar',
  '/system-management/numbering-rules',
  '/system-management/status-graphs',
  '/master-data-management/lookup-sets',
  '/system-management/respond-workspaces',
  '/system-management/respond-contacts',
  '/system-management/ai-assistant',
  '/system-management/ai-assistant/prompts',
  '/system-management/ai-assistant/usage',
  '/system-management/ai-assistant/model-prices',
  '/system-management/ai-assistant/wishlist',
  '/system-management/mcp-tools',
];

describe('menu.config - path preservation', () => {
  it('every old leaf path exists in the new MENU_SIDEBAR (minus intentional removals)', () => {
    const newPaths = collectPaths(MENU_SIDEBAR);
    const missing: string[] = [];
    for (const p of OLD_PATHS) {
      if (!INTENTIONALLY_REMOVED_PATHS.has(p) && !newPaths.has(p)) {
        missing.push(p);
      }
    }
    expect(missing).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Planning changes - moved to Supply Chain > Project Demand
// ---------------------------------------------------------------------------

describe('menu.config - Planning changes entry', () => {
  it('is present under Supply Chain > Project Demand in MENU_SIDEBAR', () => {
    const scm = findGroup(MENU_SIDEBAR, 'Supply Chain');
    expect(scm).toBeDefined();
    const projectDemand = findSubGroup(scm!, 'Project Demand');
    expect(projectDemand).toBeDefined();
    const entry = findLeaf(projectDemand!.children!, 'Planning changes');
    expect(entry).toBeDefined();
    expect(entry).toMatchObject({
      title: 'Planning changes',
      path: '/project-sales/planning-changes',
      permission: 'projects.projects.view',
    });
  });

  it('sits right after Plans in Project Demand (Order Inquiries moved to Procurement > Supply Chain)', () => {
    const scm = findGroup(MENU_SIDEBAR, 'Supply Chain');
    const projectDemand = findSubGroup(scm!, 'Project Demand');
    const titles = projectDemand!.children!.map((item) => item.title);
    expect(titles).not.toContain('Order Inquiries');
    const plansAt = titles.indexOf('Plans');
    expect(plansAt).toBeGreaterThanOrEqual(0);
    expect(titles[plansAt + 1]).toBe('Planning changes');
  });

  it('is still present under Project Sales in MENU_SIDEBAR_COMPACT', () => {
    const ps = MENU_SIDEBAR_COMPACT.find((item) => item.title === 'Project Sales');
    expect(ps).toBeDefined();
    const entry = ps!.children!.find((item) => item.title === 'Planning changes');
    expect(entry).toBeDefined();
    expect(entry).toMatchObject({
      title: 'Planning changes',
      path: '/project-sales/planning-changes',
      permission: 'projects.projects.view',
    });
  });
});

// ---------------------------------------------------------------------------
// Stock transfers - PLAN-scm-cs-planning-uat.md section E, AC-E5
// ---------------------------------------------------------------------------

describe('menu.config - Stock transfers entry', () => {
  // The two sidebars name the group differently - `Inventory` on the main one,
  // `Inventory Management` on the compact one - which is exactly why both copies get
  // asserted rather than one being assumed to mirror the other.
  it('sits under Inventory in BOTH sidebars, right after Stock Ledger', () => {
    for (const [menu, group] of [
      [MENU_SIDEBAR, 'Inventory'],
      [MENU_SIDEBAR_COMPACT, 'Inventory Management'],
    ] as const) {
      const inventory = findGroup(menu, group);
      expect(inventory).toBeDefined();
      const entry = findLeaf(inventory!.children!, 'Stock Transfers');
      expect(entry).toMatchObject({
        title: 'Stock Transfers',
        path: '/inventory-management/stock-transfers',
        permission: 'inventory.stock_transfers.view',
      });
      const titles = inventory!.children!.map((item) => item.title);
      expect(titles[titles.indexOf('Stock Ledger') + 1]).toBe('Stock Transfers');
    }
  });
});

// ---------------------------------------------------------------------------
// New gates
// ---------------------------------------------------------------------------

describe('menu.config - Ideas (AC-E-01, IDEA-CRM-SIDE)', () => {
  it('is a group, not a leaf', () => {
    const ideas = findGroup(MENU_SIDEBAR, 'Ideas');
    expect(ideas).toBeDefined();
    expect(ideas!.path).toBeUndefined();
    expect(ideas!.children?.length).toBeGreaterThan(0);
  });

  it('has "All ideas" at /ideas and "Roadmap" at /ideas/portal, in that order', () => {
    const ideas = findGroup(MENU_SIDEBAR, 'Ideas');
    const kids = (ideas?.children ?? []).map((c) => [c.title, c.path]);
    expect(kids).toEqual([
      ['All ideas', '/ideas'],
      ['Roadmap', '/ideas/portal'],
    ]);
  });

  it('gates both children with ideation.board.view', () => {
    const ideas = findGroup(MENU_SIDEBAR, 'Ideas');
    for (const child of ideas?.children ?? []) {
      expect(child.permission).toBe('ideation.board.view');
    }
    expect(ideas?.children).toHaveLength(2);
  });

  it('keeps /ideas reachable from the sidebar', () => {
    expect(collectPaths(MENU_SIDEBAR).has('/ideas')).toBe(true);
    expect(collectPaths(MENU_SIDEBAR).has('/ideas/portal')).toBe(true);
  });
});

describe('menu.config - Dealer Kit', () => {
  it('has moduleKey dealer_kit', () => {
    const dk = findGroup(MENU_SIDEBAR, 'Dealer Kit');
    expect(dk).toBeDefined();
    expect(dk!.moduleKey).toBe('dealer_kit');
  });

  it('every leaf has permission dealer_kit.page.view', () => {
    const dk = findGroup(MENU_SIDEBAR, 'Dealer Kit');
    const leafPerms: string[] = [];
    function walk(items: MenuConfig) {
      for (const item of items) {
        if (item.path) leafPerms.push(item.permission ?? '');
        if (item.children) walk(item.children);
      }
    }
    walk(dk!.children!);
    expect(leafPerms.length).toBeGreaterThan(0);
    expect(leafPerms.every((p) => p === 'dealer_kit.page.view')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Orphan heading filter
// ---------------------------------------------------------------------------

describe('filterOrphanHeadings', () => {
  it('drops a heading with no following groups', () => {
    const input: MenuConfig = [
      { heading: 'OVERVIEW' },
      { title: 'Dashboard', path: '/' },
      { heading: 'EMPTY' },
      { heading: 'TOOLS' },
      { title: 'Settings', path: '/settings' },
    ];
    const result = filterOrphanHeadings(input);
    const headings = result.filter((i) => i.heading).map((i) => i.heading);
    expect(headings).toEqual(['OVERVIEW', 'TOOLS']);
    expect(headings).not.toContain('EMPTY');
  });

  it('drops a trailing heading at the end of the list', () => {
    const input: MenuConfig = [
      { heading: 'FIRST' },
      { title: 'Item', path: '/item' },
      { heading: 'ORPHAN' },
    ];
    const result = filterOrphanHeadings(input);
    expect(result.filter((i) => i.heading)).toHaveLength(1);
    expect(result[0].heading).toBe('FIRST');
  });

  it('keeps all headings when each has at least one group', () => {
    const input: MenuConfig = [
      { heading: 'A' },
      { title: 'X', path: '/x' },
      { heading: 'B' },
      { title: 'Y', path: '/y' },
    ];
    const result = filterOrphanHeadings(input);
    expect(result).toHaveLength(4);
  });
});

// ---------------------------------------------------------------------------
// Section structure
// ---------------------------------------------------------------------------

describe('menu.config - section headings', () => {
  it('has exactly 6 headings in order', () => {
    const headings = MENU_SIDEBAR.filter((i) => i.heading).map((i) => i.heading);
    expect(headings).toEqual([
      'OVERVIEW',
      'SALES',
      'SUPPLY CHAIN',
      'CATALOGUE',
      'OPERATIONS',
      'ADMINISTRATION',
    ]);
  });
});

describe('menu.config - Complaint Management', () => {
  it('is a standalone group with moduleKey complaints', () => {
    const complaints = findGroup(MENU_SIDEBAR, 'Complaints');
    expect(complaints).toBeDefined();
    expect(complaints!.moduleKey).toBe('complaints');
  });
});

describe('menu.config - SLA Management', () => {
  it('is a standalone group with moduleKey sla', () => {
    const sla = findGroup(MENU_SIDEBAR, 'SLA');
    expect(sla).toBeDefined();
    expect(sla!.moduleKey).toBe('sla');
  });
});

describe('menu.config - Project Sales Admin', () => {
  it('is a standalone group with moduleKey procurement', () => {
    const psa = findGroup(MENU_SIDEBAR, 'Project Sales Admin');
    expect(psa).toBeDefined();
    expect(psa!.moduleKey).toBe('procurement');
  });
});

// ---------------------------------------------------------------------------
// Purchasing consolidation batch (PLAN-scm-purchasing-consolidation-6sep.md,
// lane A): navigation section 1, AC-A1 / AC-A2 / AC-A6.
// ---------------------------------------------------------------------------

describe('menu.config - Procurement > Supply Chain sub-group (AC-A1)', () => {
  it('keeps Suppliers, Product-Suppliers, Packing Lists, SPO Allocations, GRN, Picking Lines, Stock Inquiries, New Product Applications in position, then adds Supply Chain', () => {
    const procurement = findGroup(MENU_SIDEBAR, 'Procurement');
    expect(procurement).toBeDefined();
    const titles = procurement!.children!.map((item) => item.title);
    expect(titles).toEqual([
      'Suppliers',
      'Product-Suppliers',
      // Cost Price Uploads (#1288, Lane A): the supplier's price list, reviewed and applied
      // as dated cost lists - right after Product-Suppliers, the entity it writes to.
      'Cost Price Uploads',
      'Packing Lists',
      'SPO Allocations',
      'GRN',
      'Picking Lines',
      'Stock Inquiries',
      // NEW-PRODUCT-APP: right after Stock Inquiries, the record it starts from.
      'New Product Applications',
      'Supply Chain',
    ]);
  });

  it('Supply Chain sub-group holds Reorder Planning, Stock Debt, Loading Plan, Order Inquiries, Purchase Orders, Proforma Invoices in order, each gated correctly', () => {
    const procurement = findGroup(MENU_SIDEBAR, 'Procurement');
    const supplyChain = findSubGroup(procurement!, 'Supply Chain');
    expect(supplyChain).toBeDefined();
    expect(supplyChain!.children!.map((item) => item.title)).toEqual([
      'Reorder Planning',
      // No "Low stock report" (owner hand test 26 Sep, W5): it is opened from a plan,
      // Reorder planning > Actions, never from the sidebar.
      // Moved from Supply Chain > Project Demand (PLAN-stock-debt-filters-totals-export-
      // 24sep.md, R6/AC-36): beside the rest of purchasing's worklists, right after
      // Reorder Planning, not under the board that decides.
      'Stock Debt',
      'Loading Plan',
      'Order Inquiries',
      'Purchase Orders',
      'Proforma Invoices',
    ]);
    expect(findLeaf(supplyChain!.children!, 'Reorder Planning')).toMatchObject({
      path: '/scm/reorder',
      permission: 'scm.reorder.run',
      moduleKey: 'scm',
    });
    expect(JSON.stringify(MENU_SIDEBAR)).not.toContain('/scm/low-stock-report');
    expect(findLeaf(supplyChain!.children!, 'Stock Debt')).toMatchObject({
      path: '/project-sales/stock-debt',
      permission: 'projects.stock_debt.view',
    });
    expect(findLeaf(supplyChain!.children!, 'Loading Plan')).toMatchObject({
      path: '/scm/loading-plan',
      permission: 'scm.reorder.run',
      moduleKey: 'scm',
    });
    expect(findLeaf(supplyChain!.children!, 'Order Inquiries')).toMatchObject({
      path: '/project-sales/order-inquiries',
      permission: 'projects.projects.view',
    });
    expect(findLeaf(supplyChain!.children!, 'Order Inquiries')!.moduleKey).toBeUndefined();
    expect(findLeaf(supplyChain!.children!, 'Purchase Orders')).toMatchObject({
      path: '/scm/purchase-orders',
      permission: 'scm.dashboard.view',
      moduleKey: 'scm',
    });
    expect(findLeaf(supplyChain!.children!, 'Proforma Invoices')).toMatchObject({
      path: '/scm/proforma-invoices',
      permission: 'scm.dashboard.view',
      moduleKey: 'scm',
    });
  });

  it('top-level Supply Chain group no longer carries Order Inquiries, Reorder Planning, Loading Plan, Purchase Orders, Proforma Invoices or an Orders sub-group', () => {
    const scm = findGroup(MENU_SIDEBAR, 'Supply Chain');
    expect(scm).toBeDefined();
    expect(findSubGroup(scm!, 'Orders')).toBeUndefined();
    const planning = findSubGroup(scm!, 'Planning');
    expect(planning!.children!.map((item) => item.title)).toEqual([
      'Simulation',
      'Market Signals',
      'Policies',
    ]);
    const projectDemand = findSubGroup(scm!, 'Project Demand');
    expect(projectDemand!.children!.map((item) => item.title)).not.toContain('Order Inquiries');
  });
});

describe('menu.config - Sales Orders under Project Sales Admin (AC-A1)', () => {
  it('is the first child, gated by scm.dashboard.view and moduleKey scm', () => {
    const psa = findGroup(MENU_SIDEBAR, 'Project Sales Admin');
    expect(psa).toBeDefined();
    expect(psa!.children![0]).toMatchObject({
      title: 'Sales Orders',
      path: '/scm/sales-orders',
      permission: 'scm.dashboard.view',
      moduleKey: 'scm',
    });
  });
});

describe('menu.config - Incoming Containers retired from menu (AC-A6)', () => {
  it('/scm/incoming is absent from MENU_SIDEBAR', () => {
    expect(collectPaths(MENU_SIDEBAR).has('/scm/incoming')).toBe(false);
  });
});

describe('menu.config - old paths still resolvable (AC-A2)', () => {
  it('MENU_SIDEBAR carries every /scm/* leaf moved in this batch under its new parent', () => {
    const paths = collectPaths(MENU_SIDEBAR);
    for (const p of [
      '/scm/reorder',
      '/scm/loading-plan',
      '/scm/purchase-orders',
      '/scm/proforma-invoices',
      '/scm/sales-orders',
      '/project-sales/order-inquiries',
    ]) {
      expect(paths.has(p)).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// MODEL-PRICES (MP-1): "Model prices" sits right after "Usage" under AI Assistant
// ---------------------------------------------------------------------------

describe('menu.config - Model prices entry', () => {
  it('is the item right after Usage in System > AI Assistant', () => {
    const system = findGroup(MENU_SIDEBAR, 'System');
    expect(system).toBeDefined();
    const ai = findSubGroup(system!, 'AI Assistant');
    expect(ai).toBeDefined();
    const titles = ai!.children!.map((c) => c.title);
    expect(titles[titles.indexOf('Usage') + 1]).toBe('Model prices');
    const entry = findLeaf(ai!.children!, 'Model prices');
    expect(entry?.path).toBe('/system-management/ai-assistant/model-prices');
    expect(entry?.permission).toBe('system.ai_assistant_settings.view');
  });
});

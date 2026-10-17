/**
 * Map URL prefixes (protected app routes) to installable module keys.
 * Longer prefixes should appear first so more specific routes win.
 *
 * Per-module assets dropped into `modules/<key>/routes.json` (via the App Store upload
 * flow or manual placement) are auto-merged in. Hardcoded entries below are legacy
 * fallbacks for modules not yet migrated to the per-module package layout.
 */
import { moduleRoutePrefixes as discoveredRoutePrefixes } from '@/modules/registry';

const LEGACY_ROUTE_MODULE_PREFIXES: { prefix: string; moduleKey: string }[] = [
  { prefix: '/scm', moduleKey: 'scm' },
  { prefix: '/order-management', moduleKey: 'order' },
  { prefix: '/master-data-management', moduleKey: 'product' },
  { prefix: '/complaint-management', moduleKey: 'complaints' },
  { prefix: '/sla-management', moduleKey: 'sla' },
  { prefix: '/procurement-management', moduleKey: 'procurement' },
  { prefix: '/inventory-management', moduleKey: 'inventory' },
  { prefix: '/marketing-management', moduleKey: 'marketing' },
  { prefix: '/forms-management', moduleKey: 'forms' },
  { prefix: '/workflow-forms-management', moduleKey: 'workflow_forms' },
  { prefix: '/resource-management', moduleKey: 'resources' },
  { prefix: '/integration-management', moduleKey: 'base' },
  { prefix: '/system-management', moduleKey: 'base' },
  { prefix: '/user-management', moduleKey: 'base' },
  { prefix: '/commercial-management', moduleKey: 'commercial_core' },
  { prefix: '/commercial-core', moduleKey: 'commercial_core' },
  { prefix: '/commercial', moduleKey: 'commercial_core' },
  { prefix: '/project-sales', moduleKey: 'projects' },
  { prefix: '/sales', moduleKey: 'sales' },
  { prefix: '/finance', moduleKey: 'finance' },
  { prefix: '/dealer-kit', moduleKey: 'dealer_kit' },
];

function buildRouteMap(): { prefix: string; moduleKey: string }[] {
  const discovered = discoveredRoutePrefixes();
  const seen = new Set(discovered.map((d) => d.prefix));
  const merged: { prefix: string; moduleKey: string }[] = [...discovered];
  for (const entry of LEGACY_ROUTE_MODULE_PREFIXES) {
    if (!seen.has(entry.prefix)) merged.push(entry);
  }
  // Sort by length desc so the most specific prefix wins.
  return merged.sort((a, b) => b.prefix.length - a.prefix.length);
}

export const ROUTE_MODULE_PREFIXES: { prefix: string; moduleKey: string }[] = buildRouteMap();

export function moduleKeyForPath(pathname: string): string | null {
  const path = pathname.split('?')[0] || '/';
  for (const { prefix, moduleKey } of ROUTE_MODULE_PREFIXES) {
    if (path === prefix || path.startsWith(`${prefix}/`)) {
      return moduleKey;
    }
  }
  return null;
}

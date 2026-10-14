/**
 * ============================================================================
 * SCM Policy Configuration - DETERMINISTIC MOCK BACKING STORE  (Phase 1 only)
 * ============================================================================
 * PROTOTYPE DATA. No network, no `Math.random`, no `Date` - every value is a
 * hand-authored constant so the UI renders identically on every load and the
 * resolution preview is fully reproducible.
 *
 * Phase 2 deletes this file (or keeps it only for vitest fixtures) and flips
 * `USE_POLICY_MOCKS` to false in `services/scmPolicyService.ts`, at which point
 * every function below is replaced by a real `lib/api-client` call to
 * `/api/v1/scm/policies*`. The mock CRUD mutates an in-memory array so the
 * prototype's add / edit / delete feel real within a session; a page reload
 * resets to this seed.
 *
 * The resolve() mock mirrors the ENGINE precedence (sku > abc_xyz_cell >
 * product_class > global, active-only, priority tiebreak) but is NOT the real
 * resolver - Phase 2's `/scm/policies/resolve` calls
 * `reorder_engine.resolve_policy_for_sku` verbatim (AC-PREV-2, Risk #1).
 * ============================================================================
 */
import type {
  AbcXyzPolicy,
  AbcXyzWrite,
  ReorderPolicyRow,
  ReorderPolicyWrite,
  ResolutionChainLink,
  ResolutionResult,
  ScopeType,
  SupplierScoringPolicy,
  SupplierScoringWrite,
} from '../types/policy.types';
import type { Option } from '../../services/scmOptionsService';
import { codeWithName } from '@/lib/product-display';

// ── Mock catalogs (drive the pickers + the resolve chain) ───────────────────

interface MockProduct {
  id: string; // stands in for products.id (never displayed)
  product_code: string;
  product_name: string;
  category_code: string;
  category_name: string;
  abc: 'A' | 'B' | 'C' | null;
  xyz: 'X' | 'Y' | 'Z' | null;
}

const MOCK_PRODUCTS: MockProduct[] = [
  { id: 'prd-001', product_code: 'BRK-450', product_name: 'Brake Disc 450mm', category_code: 'BRK', category_name: 'Braking', abc: 'A', xyz: 'X' },
  { id: 'prd-002', product_code: 'FLT-120', product_name: 'Oil Filter 120', category_code: 'FLT', category_name: 'Filtration', abc: 'B', xyz: 'Y' },
  { id: 'prd-003', product_code: 'GKT-088', product_name: 'Head Gasket 088', category_code: 'GKT', category_name: 'Gaskets', abc: 'C', xyz: 'Z' },
  { id: 'prd-004', product_code: 'BRK-620', product_name: 'Brake Pad Set 620', category_code: 'BRK', category_name: 'Braking', abc: 'A', xyz: 'X' },
  { id: 'prd-005', product_code: 'SPK-010', product_name: 'Spark Plug 010', category_code: 'ELE', category_name: 'Electrical', abc: null, xyz: null },
];

const MOCK_CATEGORIES = [
  { code: 'BRK', name: 'Braking' },
  { code: 'FLT', name: 'Filtration' },
  { code: 'GKT', name: 'Gaskets' },
  { code: 'ELE', name: 'Electrical' },
];

const MOCK_WAREHOUSES = [
  { code: 'WH-KL', name: 'Kuala Lumpur DC' },
  { code: 'WH-JB', name: 'Johor Bahru DC' },
  { code: 'WH-PG', name: 'Penang Hub' },
];

// ── Seed policy rows (global + class + cell + sku + one inactive sku) ────────

let reorderPolicies: ReorderPolicyRow[] = [
  {
    id: 'pol-global',
    scope_type: 'global',
    scope_ref: null,
    scope_label: '-',
    policy_type: 'reorder_point',
    service_level: null,
    safety_stock_method: 'fixed_days',
    safety_days: 7,
    review_period_days: null,
    forecast_window_days: 90,
    trajectory_window_retail_months: null,
    trajectory_window_project_months: null,
    price_stale_after_days: null,
    price_movement_threshold_pct: null,
    baseline_source: 'trailing_90d',
    spike_handling: 'clip',
    buy_scope: 'network',
    dead_stock_days: 180,
    overstock_days: 120,
    min_override: null,
    max_override: null,
    priority: 0,
    is_active: true,
    supplier_selection: 'best_score',
    lead_time_default_days: 14,
  },
  {
    id: 'pol-class-brk',
    scope_type: 'product_class',
    scope_ref: 'BRK',
    scope_label: 'Braking',
    policy_type: 'reorder_point',
    service_level: 0.95,
    safety_stock_method: 'statistical',
    safety_days: null,
    review_period_days: null,
    forecast_window_days: 90,
    trajectory_window_retail_months: null,
    trajectory_window_project_months: null,
    price_stale_after_days: null,
    price_movement_threshold_pct: null,
    baseline_source: 'trailing_90d',
    spike_handling: 'clip',
    buy_scope: 'network',
    dead_stock_days: 180,
    overstock_days: 120,
    min_override: null,
    max_override: null,
    priority: 10,
    is_active: true,
    supplier_selection: 'lowest_cost',
    lead_time_default_days: 21,
  },
  {
    id: 'pol-cell-ax',
    scope_type: 'abc_xyz_cell',
    scope_ref: 'A-X',
    scope_label: 'A·X',
    policy_type: 'periodic_review',
    service_level: null,
    safety_stock_method: 'fixed_days',
    safety_days: 10,
    review_period_days: 7,
    forecast_window_days: 60,
    trajectory_window_retail_months: null,
    trajectory_window_project_months: null,
    price_stale_after_days: null,
    price_movement_threshold_pct: null,
    baseline_source: 'trailing_60d',
    spike_handling: 'clip',
    buy_scope: 'network',
    dead_stock_days: 150,
    overstock_days: 90,
    min_override: null,
    max_override: null,
    priority: 20,
    is_active: true,
    supplier_selection: 'best_score',
    lead_time_default_days: 18,
  },
  {
    id: 'pol-sku-brk450',
    scope_type: 'sku',
    scope_ref: 'prd-001',
    scope_label: 'BRK-450 · Brake Disc 450mm',
    policy_type: 'min_max',
    service_level: null,
    safety_stock_method: 'manual',
    safety_days: null,
    review_period_days: null,
    forecast_window_days: 90,
    trajectory_window_retail_months: null,
    trajectory_window_project_months: null,
    price_stale_after_days: null,
    price_movement_threshold_pct: null,
    baseline_source: 'trailing_90d',
    spike_handling: 'clip',
    buy_scope: 'warehouse',
    dead_stock_days: 120,
    overstock_days: 90,
    min_override: 40,
    max_override: 200,
    priority: 30,
    is_active: true,
    supplier_selection: 'primary',
    lead_time_default_days: 14,
  },
  {
    id: 'pol-sku-flt120',
    scope_type: 'sku',
    scope_ref: 'prd-002',
    scope_label: 'FLT-120 · Oil Filter 120',
    policy_type: 'reorder_point',
    service_level: null,
    safety_stock_method: 'fixed_days',
    safety_days: 5,
    review_period_days: null,
    forecast_window_days: 90,
    trajectory_window_retail_months: null,
    trajectory_window_project_months: null,
    price_stale_after_days: null,
    price_movement_threshold_pct: null,
    baseline_source: 'trailing_90d',
    spike_handling: 'clip',
    buy_scope: 'network',
    dead_stock_days: 180,
    overstock_days: 120,
    min_override: null,
    max_override: null,
    priority: 5,
    is_active: false, // demonstrates the "inactive" state in grid + resolution chain
    supplier_selection: 'best_score',
    lead_time_default_days: 10,
  },
];

let abcXyzPolicy: AbcXyzPolicy = {
  abc_a_pct: 0.8,
  abc_b_pct: 0.15,
  xyz_x_max: 0.5,
  xyz_y_max: 1.0,
  exists: true,
};

let supplierScoringPolicy: SupplierScoringPolicy = {
  delivery_weight: 0.6,
  quality_weight: 0.4,
  grace_days: 2,
  min_sample_size: 5,
  exists: true,
};

// Deterministic id generator for mock-created rows (no Date/random).
let idCounter = 0;
const nextId = () => `pol-new-${(idCounter += 1)}`;

// ── Label resolution (no UUID ever surfaces) ────────────────────────────────

/** Human label for a scope + ref, resolved from the mock catalogs. */
export function mockScopeLabel(scopeType: ScopeType, scopeRef: string | null): string {
  if (scopeType === 'global' || !scopeRef) return '-';
  if (scopeType === 'sku') {
    const p = MOCK_PRODUCTS.find((x) => x.id === scopeRef);
    return p ? codeWithName(p.product_code, p.product_name, ' · ') : scopeRef;
  }
  if (scopeType === 'product_class') {
    const c = MOCK_CATEGORIES.find((x) => x.code === scopeRef);
    return c ? c.name : scopeRef;
  }
  // abc_xyz_cell "A-X" → "A·X"
  return scopeRef.replace('-', '·');
}

// ── Option sources for the pickers ──────────────────────────────────────────

export function mockProductOptions(): Option[] {
  // value = products.id (hidden resolver key); label carries the readable code+name.
  return MOCK_PRODUCTS.map((p) => ({
    value: p.id,
    label: codeWithName(p.product_code, p.product_name, ' · '),
    description: p.category_name,
  }));
}

export function mockCategoryOptions(): Option[] {
  // value = category_code (resolver key for product_class scope).
  return MOCK_CATEGORIES.map((c) => ({ value: c.code, label: c.name, description: c.code }));
}

export function mockWarehouseOptions(): Option[] {
  return MOCK_WAREHOUSES.map((w) => ({ value: w.code, label: w.name }));
}

// ── Mock CRUD (mutates the in-memory array) ─────────────────────────────────

export function mockListReorderPolicies(): ReorderPolicyRow[] {
  // Return a defensive copy; the grid never mutates it directly.
  return reorderPolicies.map((p) => ({ ...p }));
}

export function mockCreateReorderPolicy(body: ReorderPolicyWrite): ReorderPolicyRow {
  const row: ReorderPolicyRow = {
    ...body,
    id: nextId(),
    scope_label: mockScopeLabel(body.scope_type, body.scope_ref),
  };
  reorderPolicies = [...reorderPolicies, row];
  return { ...row };
}

export function mockUpdateReorderPolicy(id: string, body: ReorderPolicyWrite): ReorderPolicyRow {
  const idx = reorderPolicies.findIndex((p) => p.id === id);
  if (idx === -1) throw new Error('Policy not found');
  const row: ReorderPolicyRow = {
    ...body,
    id,
    scope_label: mockScopeLabel(body.scope_type, body.scope_ref),
  };
  reorderPolicies = reorderPolicies.map((p) => (p.id === id ? row : p));
  return { ...row };
}

export function mockDeleteReorderPolicy(id: string): void {
  const row = reorderPolicies.find((p) => p.id === id);
  if (row?.scope_type === 'global') {
    throw new Error('The global default policy cannot be deleted');
  }
  reorderPolicies = reorderPolicies.filter((p) => p.id !== id);
}

export function mockGetClassification(): AbcXyzPolicy {
  return { ...abcXyzPolicy };
}

export function mockSaveClassification(body: AbcXyzWrite): AbcXyzPolicy {
  abcXyzPolicy = { ...body, exists: true };
  return { ...abcXyzPolicy };
}

export function mockGetSupplierScoring(): SupplierScoringPolicy {
  return { ...supplierScoringPolicy };
}

export function mockSaveSupplierScoring(body: SupplierScoringWrite): SupplierScoringPolicy {
  supplierScoringPolicy = { ...body, exists: true };
  return { ...supplierScoringPolicy };
}

// ── Mock resolver (mirrors engine precedence; Phase 2 uses the real engine) ──

/**
 * Deterministically resolve which reorder policy wins for a product, and build
 * the full evaluation chain (sku → abc_xyz_cell → product_class → global).
 * Precedence is most-specific-active-wins; ties within a level break on higher
 * priority. Inactive rows at a level are surfaced as `inactive` (not winners).
 */
export function mockResolve(productId: string, warehouseCode: string | null): ResolutionResult {
  const product = MOCK_PRODUCTS.find((p) => p.id === productId);
  if (!product) throw new Error('Product not found');

  const cell = product.abc && product.xyz ? `${product.abc}-${product.xyz}` : null;
  const productClass = product.category_code;
  const warehouse = warehouseCode
    ? MOCK_WAREHOUSES.find((w) => w.code === warehouseCode) ?? null
    : null;

  // Candidate ref per scope level, in precedence order.
  const levels: { scope_type: ScopeType; ref: string | null }[] = [
    { scope_type: 'sku', ref: productId },
    { scope_type: 'abc_xyz_cell', ref: cell },
    { scope_type: 'product_class', ref: productClass },
    { scope_type: 'global', ref: null },
  ];

  // Winner = first (most specific) level with an ACTIVE matching policy.
  let winner: ReorderPolicyRow | null = null;
  let winningLevel: ScopeType | null = null;

  for (const level of levels) {
    const active = reorderPolicies.filter(
      (p) =>
        p.is_active &&
        p.scope_type === level.scope_type &&
        (level.scope_type === 'global' ? p.scope_ref == null : p.scope_ref === level.ref),
    );
    if (active.length && level.ref !== undefined) {
      // Global always matches (ref null); non-global needs a concrete ref.
      if (level.scope_type !== 'global' && level.ref == null) continue;
      // Priority tiebreak: highest priority wins, then scope_ref for stability.
      active.sort((a, b) => b.priority - a.priority || String(a.scope_ref).localeCompare(String(b.scope_ref)));
      winner = { ...active[0] };
      winningLevel = level.scope_type;
      break;
    }
  }

  const chain: ResolutionChainLink[] = levels.map((level) => {
    const isGlobal = level.scope_type === 'global';
    const hasRef = isGlobal || level.ref != null;
    const matches = reorderPolicies.filter(
      (p) =>
        p.scope_type === level.scope_type &&
        (isGlobal ? p.scope_ref == null : p.scope_ref === level.ref),
    );
    const active = matches.filter((p) => p.is_active);
    const isWinner = winningLevel === level.scope_type;
    const label =
      level.scope_type === 'sku'
        ? codeWithName(product.product_code, product.product_name, ' · ')
        : level.scope_type === 'abc_xyz_cell'
          ? cell
            ? cell.replace('-', '·')
            : 'No ABC-XYZ class'
          : level.scope_type === 'product_class'
            ? product.category_name
            : 'Global default';

    let matched = false;
    let reason: ResolutionChainLink['reason'];
    if (!hasRef) {
      reason = 'no-match'; // e.g. product has no classification cell
    } else if (active.length) {
      matched = true;
      reason = isWinner
        ? active.length > 1
          ? 'priority-tiebreak'
          : 'most-specific-active'
        : 'most-specific-active'; // a more specific active policy overrides this one
    } else if (matches.length) {
      reason = 'inactive'; // a policy exists here but is switched off
    } else {
      reason = 'no-match';
    }

    return {
      scope_type: level.scope_type,
      scope_ref: isGlobal ? null : level.ref,
      scope_label: label,
      matched,
      is_winner: isWinner,
      reason,
    };
  });

  return {
    product: { product_code: product.product_code, product_name: product.product_name },
    warehouse: warehouse ? { warehouse_code: warehouse.code, warehouse_name: warehouse.name } : null,
    abc_xyz_cell: cell,
    product_class: productClass,
    winner,
    chain,
    // affected_sku_count intentionally omitted - AC-PREV-5 DEFERRED in Phase 1.
  };
}

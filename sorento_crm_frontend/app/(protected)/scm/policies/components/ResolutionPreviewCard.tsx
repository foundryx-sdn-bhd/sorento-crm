'use client';

import { useState } from 'react';
import { ArrowDown, CheckCircle2, LoaderCircle, MinusCircle, Trophy } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardHeading, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { EM_DASH } from '../../lib/format';
import { useProductScopeOptions, useResolvePolicy, useWarehouseScopeOptions } from '../hooks/usePolicies';
import {
  POLICY_TYPE_LABEL,
  SCOPE_TYPE_LABEL,
  SUPPLIER_SELECTION_LABEL,
  safetyStockSummary,
} from '../lib/labels';
import type { ReorderPolicyRow, ResolutionChainLink } from '../types/policy.types';
import { distinctName } from '@/lib/product-display';

const REASON_LABEL: Record<ResolutionChainLink['reason'], string> = {
  'most-specific-active': 'Active policy',
  'priority-tiebreak': 'Won on priority',
  'no-match': 'No policy at this scope',
  inactive: 'Policy exists but inactive',
};

/** One rung of the resolution ladder - the winning rung is highlighted. */
function ChainRung({ link, isLast }: { link: ResolutionChainLink; isLast: boolean }) {
  return (
    <div className="relative">
      <div
        className={cn(
          'flex items-center justify-between gap-3 rounded-lg border p-3',
          link.is_winner
            ? 'border-primary bg-primary/5 ring-1 ring-primary'
            : 'border-border bg-background',
        )}
      >
        <div className="flex min-w-0 items-center gap-2">
          {link.is_winner ? (
            <Trophy className="size-4 shrink-0 text-primary" aria-hidden />
          ) : link.matched ? (
            <CheckCircle2 className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          ) : (
            <MinusCircle className="size-4 shrink-0 text-muted-foreground/50" aria-hidden />
          )}
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-medium">{SCOPE_TYPE_LABEL[link.scope_type]}</span>
              {link.is_winner ? (
                <Badge variant="primary" appearance="light" size="sm">
                  Winner
                </Badge>
              ) : null}
            </div>
            <span className="truncate text-xs text-muted-foreground" title={link.scope_label}>
              {link.scope_label}
            </span>
          </div>
        </div>
        <span
          className={cn(
            'shrink-0 text-2xs',
            link.reason === 'inactive' ? 'text-warning' : 'text-muted-foreground',
          )}
        >
          {REASON_LABEL[link.reason]}
        </span>
      </div>
      {!isLast ? (
        <div className="flex justify-center py-0.5">
          <ArrowDown className="size-3.5 text-muted-foreground/60" aria-hidden />
        </div>
      ) : null}
    </div>
  );
}

/** Effective values of the winning policy - what the run would actually use. */
function EffectiveValues({ policy }: { policy: ReorderPolicyRow }) {
  const rows: { label: string; value: string }[] = [
    { label: 'Scope', value: `${SCOPE_TYPE_LABEL[policy.scope_type]} - ${policy.scope_label}` },
    { label: 'Policy type', value: POLICY_TYPE_LABEL[policy.policy_type] },
    {
      label: 'Safety stock',
      value: safetyStockSummary(policy.safety_stock_method, policy.safety_days, policy.service_level),
    },
    {
      label: 'Default lead time',
      value: policy.lead_time_default_days != null ? `${policy.lead_time_default_days} days` : EM_DASH,
    },
    { label: 'Supplier selection', value: SUPPLIER_SELECTION_LABEL[policy.supplier_selection] },
    {
      label: 'Trigger',
      value:
        policy.policy_type === 'min_max'
          ? `Min ${policy.min_override ?? EM_DASH} / Max ${policy.max_override ?? EM_DASH}`
          : policy.policy_type === 'periodic_review'
            ? policy.review_period_days != null
              ? `Every ${policy.review_period_days} days`
              : EM_DASH
            : 'Reorder point',
    },
  ];
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
      {rows.map((r) => (
        <div key={r.label} className="flex justify-between gap-3 border-b border-dashed py-1.5 text-sm">
          <dt className="text-muted-foreground">{r.label}</dt>
          <dd className="text-right font-medium">{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ResolutionPreviewCard() {
  const [productId, setProductId] = useState('');
  const [warehouseCode, setWarehouseCode] = useState('');

  const { data: productOptions, isLoading: productsLoading } = useProductScopeOptions();
  const { data: warehouseOptions, isLoading: warehousesLoading } = useWarehouseScopeOptions();
  const resolve = useResolvePolicy();

  const result = resolve.data ?? null;

  const run = () => {
    if (!productId) return;
    resolve.mutate({ productId, warehouseCode: warehouseCode || null });
  };

  return (
    <Card>
      <CardHeader>
        <CardHeading>
          <CardTitle>Resolution preview</CardTitle>
        </CardHeading>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm text-muted-foreground">
          Pick a product to see <strong>which reorder policy the run would use</strong> and why. The
          engine checks the most specific scope first: SKU → ABC-XYZ cell → product class → global
          default; the first active match wins.
        </p>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <div>
            <Label className="mb-1 block">Product</Label>
            <SearchableSelect
              value={productId}
              onChange={setProductId}
              options={productOptions ?? []}
              disabled={productsLoading}
              placeholder={productsLoading ? 'Loading products…' : 'Search a product'}
              emptyMessage="No products found."
            />
          </div>
          <div>
            <Label className="mb-1 block">Warehouse (optional)</Label>
            <SearchableSelect
              value={warehouseCode}
              onChange={setWarehouseCode}
              options={warehouseOptions ?? []}
              disabled={warehousesLoading}
              placeholder={warehousesLoading ? 'Loading warehouses…' : 'All warehouses'}
              emptyMessage="No warehouses found."
            />
          </div>
          <Button onClick={run} disabled={!productId || resolve.isPending} className="sm:w-auto">
            {resolve.isPending ? <LoaderCircle className="size-4 animate-spin" /> : null}
            Resolve
          </Button>
        </div>

        {resolve.isError ? (
          <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            {resolve.error instanceof Error ? resolve.error.message : 'Failed to resolve policy.'}
          </div>
        ) : null}

        {/* Empty state before the first run. */}
        {!result && !resolve.isPending && !resolve.isError ? (
          <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
            Pick a product and select Resolve to preview the winning policy and the full resolution
            chain.
          </div>
        ) : null}

        {result ? (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium">{result.product.product_code}</span>
              {distinctName(result.product.product_code, result.product.product_name) ? (
                <span className="text-muted-foreground">{result.product.product_name}</span>
              ) : null}
              {result.abc_xyz_cell ? (
                <Badge variant="warning" appearance="light" size="sm">
                  {result.abc_xyz_cell.replace('-', '·')}
                </Badge>
              ) : (
                <Badge variant="secondary" appearance="light" size="sm">
                  No ABC-XYZ class
                </Badge>
              )}
              {result.product_class ? (
                <Badge variant="info" appearance="light" size="sm">
                  {result.product_class}
                </Badge>
              ) : null}
              {result.warehouse ? (
                <Badge variant="secondary" appearance="light" size="sm">
                  {result.warehouse.warehouse_code}
                </Badge>
              ) : null}
            </div>

            {/* No cell/class match → global wins, explained (never an error; AC-PREV-3). */}
            {result.winner?.scope_type === 'global' ? (
              <div className="rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
                This product has no matching SKU, ABC-XYZ cell, or product-class override, so the{' '}
                <strong>global default</strong> policy resolves.
              </div>
            ) : null}

            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Resolution chain
              </div>
              <div className="space-y-0">
                {result.chain.map((link, i) => (
                  <ChainRung key={link.scope_type} link={link} isLast={i === result.chain.length - 1} />
                ))}
              </div>
            </div>

            {result.winner ? (
              <div>
                <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Effective policy values
                </div>
                <EffectiveValues policy={result.winner} />
              </div>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

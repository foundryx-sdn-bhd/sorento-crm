'use client';

import { AlertTriangle } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import type { ResolvedBundle } from '@/lib/dealer-kit/types';
import { sameDisplayText } from '@/lib/product-display';

/**
 * A Bundle: ONE priced heading with its components listed beneath (AC-F12).
 *
 * The price shown is the bundle's own. The per-component figures are the
 * allocation, and they sum to it exactly - that is enforced in
 * `bundle_pricing.py`, not here, but showing both is what makes the allocation
 * legible to whoever has to explain an invoice.
 *
 * `available` arrives derived from the components and is never stored (AC-F10).
 * An unavailable bundle still renders - hiding it would leave a Designer
 * wondering where their block went - but it renders as plainly not orderable,
 * naming the component at fault.
 */
export function BundleCard({ bundle }: { bundle: ResolvedBundle }) {
  // With one component the allocation IS the bundle price, so printing it again
  // beside the part is the same number twice and reads as a mistake. The
  // allocation only earns its space once there is a split to explain.
  const showAllocation = bundle.components.length > 1;

  return (
    <section
      className="flex h-full min-w-0 flex-col gap-2 rounded-lg border border-border bg-background p-3"
      data-dk-bundle={bundle.id}
      data-dk-bundle-available={bundle.available ? 'true' : 'false'}
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="min-w-0 truncate text-sm font-semibold text-foreground" title={bundle.name}>
          {bundle.name}
        </h3>
        <span className="shrink-0 text-sm font-semibold text-foreground">{bundle.price}</span>
      </header>

      {!bundle.available && (
        <div className="flex items-start gap-2 rounded border border-amber-500/50 bg-amber-500/5 px-2 py-1.5">
          <AlertTriangle className="mt-px size-3.5 shrink-0 text-amber-600" aria-hidden />
          <p className="text-[11px] text-foreground">
            Not currently available
            {bundle.unavailableReason ? `: ${bundle.unavailableReason}` : '.'}
          </p>
        </div>
      )}

      <ul className="flex flex-col divide-y divide-border">
        {bundle.components.map((component) => (
          <li
            key={component.productId}
            className="flex flex-wrap items-baseline justify-between gap-x-3 py-1.5"
          >
            <div className="min-w-0">
              <p className="truncate text-xs text-foreground" title={component.productName}>
                {component.quantity > 1 && (
                  <span className="text-muted-foreground">{component.quantity} x </span>
                )}
                {component.productName}
              </p>
              {!sameDisplayText(component.productCode, component.productName) ? (
                <p className="truncate font-mono text-[10px] text-muted-foreground">
                  {component.productCode}
                </p>
              ) : null}
            </div>

            <div className="flex shrink-0 items-center gap-2">
              {!component.available && (
                <Badge variant="warning" className="text-[9px]">
                  Discontinued
                </Badge>
              )}
              {showAllocation && (
                <span className="text-[11px] text-muted-foreground">{component.allocated}</span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

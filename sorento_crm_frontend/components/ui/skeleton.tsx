import * as React from 'react';
import { Slot as SlotPrimitive } from 'radix-ui';
import { cn } from '@/lib/utils';

/**
 * `asChild` (M5-01/M5-02 review N1) - two callers need a shape `<div>` cannot take:
 * `DialogTitle` renders an `<h2>` and `SheetDescription` a `<p>`, both phrasing content
 * only, so the placeholder inside them has to be an inline element. Before this they
 * hand-rolled `animate-pulse rounded-md bg-accent` on a bare `span`, which drew the
 * right bar but carried none of `Skeleton`'s own `data-slot="skeleton"` marker - this
 * lets them render `<Skeleton asChild><span .../></Skeleton>` instead and pick that
 * marker (and this component's future changes) up for free.
 */
function Skeleton({
  className,
  asChild = false,
  ...props
}: React.ComponentProps<'div'> & { asChild?: boolean }) {
  const Comp = asChild ? SlotPrimitive.Slot : 'div';
  return (
    <Comp
      data-slot="skeleton"
      // The never-stuck smoke (e2e/never-stuck.smoke.spec.ts) fails a page that still
      // shows any `[data-loading]` element 15s after navigation. Placed before the
      // spread so it marks every skeleton without clobbering a caller's own attributes.
      data-loading=""
      className={cn('animate-pulse rounded-md bg-accent', className)}
      {...props}
    />
  );
}

export { Skeleton };

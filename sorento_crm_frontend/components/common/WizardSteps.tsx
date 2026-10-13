'use client';

import * as React from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface WizardStep {
  id: string;
  label: string;
}

/**
 * Where a multi-step dialog stands: numbered circles joined by a line, a check on the steps
 * already done, and the current step's label under the row.
 *
 * The shape is the checkout stepper's (`app/(protected)/store-client/checkout/steps.tsx`), cut
 * down to fit a dialog: only the current label is printed, so four steps fit at 375px without
 * wrapping into two rows, and the other labels stay reachable as each circle's `title`.
 */
export function WizardSteps({
  steps,
  currentId,
  className,
}: {
  steps: readonly WizardStep[];
  currentId: string;
  className?: string;
}) {
  const currentIndex = Math.max(
    steps.findIndex((step) => step.id === currentId),
    0,
  );

  return (
    <div className={cn('space-y-2', className)}>
      <ol aria-label="Steps" className="flex items-center">
        {steps.map((step, index) => {
          const done = index < currentIndex;
          const active = index === currentIndex;
          return (
            <li
              key={step.id}
              aria-current={active ? 'step' : undefined}
              className={cn('flex items-center', index < steps.length - 1 && 'flex-1')}
            >
              <span
                title={step.label}
                className={cn(
                  'flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium tabular-nums',
                  active && 'border-primary bg-primary text-primary-foreground',
                  done && 'border-primary bg-primary/10 text-primary',
                  !active && !done && 'border-border text-muted-foreground',
                )}
              >
                {done ? <Check className="size-3.5" aria-hidden /> : index + 1}
                <span className="sr-only">{step.label}</span>
              </span>
              {index < steps.length - 1 && (
                <span
                  aria-hidden
                  className={cn('mx-2 h-px flex-1', done ? 'bg-primary' : 'bg-border')}
                />
              )}
            </li>
          );
        })}
      </ol>
      <p className="text-sm font-medium" aria-hidden>
        {steps[currentIndex]?.label}
      </p>
    </div>
  );
}

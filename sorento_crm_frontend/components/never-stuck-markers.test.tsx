/**
 * The never-stuck smoke (`e2e/never-stuck.smoke.spec.ts`) finds loading placeholders by
 * `[data-loading]` and refusals by `[data-access-denied]`. Nothing else reads those
 * attributes, so a refactor that drops one keeps every other test green while the nightly
 * smoke silently stops seeing skeletons (passes everything) or refusals. This pins them.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Skeleton } from '@/components/ui/skeleton';
import { ScreenLoader } from '@/components/common/screen-loader';
import AccessDenied from '@/app/components/common/AccessDenied';

describe('never-stuck smoke markers', () => {
  it('Skeleton carries data-loading, also with asChild and a caller attribute', () => {
    const { container } = render(
      <>
        <Skeleton data-testid="caller" />
        <Skeleton asChild>
          <span />
        </Skeleton>
      </>,
    );
    const marked = container.querySelectorAll('[data-loading]');
    expect(marked).toHaveLength(2);
    expect(marked[0].getAttribute('data-testid')).toBe('caller');
  });

  it('ScreenLoader carries data-loading', () => {
    const { container } = render(<ScreenLoader />);
    expect(container.querySelector('[data-loading]')).not.toBeNull();
  });

  it('AccessDenied carries data-access-denied', () => {
    const { container } = render(<AccessDenied />);
    expect(container.querySelector('[data-access-denied]')).not.toBeNull();
  });
});

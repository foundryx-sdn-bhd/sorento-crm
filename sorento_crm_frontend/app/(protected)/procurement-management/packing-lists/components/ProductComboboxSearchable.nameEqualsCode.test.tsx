/**
 * AC-6: the saved row's trigger agrees with the shared rule, so name '11x11' under code
 * '11X11' prints the code once.
 */
import React from 'react';
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

import { ProductComboboxSearchable } from './ProductComboboxSearchable';

afterEach(() => cleanup());

function trigger(code: string, name: string) {
  const { container } = render(
    <ProductComboboxSearchable
      value="p1"
      onChange={() => {}}
      fetchProducts={vi.fn().mockResolvedValue({ data: [] })}
      productFallback={{ id: 'p1', product_code: code, product_name: name }}
    />,
  );
  return container.textContent ?? '';
}

describe('ProductComboboxSearchable trigger (name equals code)', () => {
  it('shows the code once when the name differs only by case', () => {
    expect(trigger('11X11', '11x11')).toContain('11X11');
    expect(trigger('11X11', '11x11')).not.toMatch(/11x11/);
  });

  it('keeps a differing name', () => {
    expect(trigger('SRT6641', 'FLOOR TRAP')).toContain('SRT6641 - FLOOR TRAP');
  });
});

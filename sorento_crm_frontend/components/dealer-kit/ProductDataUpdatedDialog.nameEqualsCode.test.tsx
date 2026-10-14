import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';

import ProductDataUpdatedDialog from './ProductDataUpdatedDialog';

function renderDialog(code: string, name: string) {
  render(
    <ProductDataUpdatedDialog
      open
      onOpenChange={() => {}}
      code={code}
      tagLabel=""
      name={name}
      changes={[]}
      version={null}
      onDismiss={vi.fn()}
      onRollBack={vi.fn()}
    />,
  );
  return document.body.textContent ?? '';
}

afterEach(() => cleanup());

describe('ProductDataUpdatedDialog description (AC-5)', () => {
  it('prints the code alone when the name equals the code', () => {
    const text = renderDialog('11X11', '11X11');
    expect(text).toContain('11X11');
    expect(text).not.toContain('11X11 / 11X11');
  });

  it('prints the code alone when the name differs only by case', () => {
    expect(renderDialog('11X11', '11x11')).not.toContain('11X11 / 11x11');
  });

  it('prints CODE / Name when they differ', () => {
    expect(renderDialog('SRT6641', 'FLOOR TRAP')).toContain('SRT6641 / FLOOR TRAP');
  });
});

/** S9: W/H refusals mark the offending box itself (W vs H is not ambiguous). */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { TagSizeControl } from './TagSizeControl';

beforeEach(() => {
  window.localStorage.clear();
});

function open() {
  fireEvent.click(screen.getByRole('button', { name: /Tag Size/ }));
}

describe('TagSizeControl field marks (S9)', () => {
  it('a width below the floor marks the width box invalid and leaves height unmarked', () => {
    render(<TagSizeControl width_mm={60} height_mm={40} presets={[]} onResize={vi.fn()} />);
    open();
    const w = screen.getByLabelText('Tag width (mm)');
    fireEvent.change(w, { target: { value: '5' } });
    fireEvent.blur(w);
    expect(w).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('Tag height (mm)')).not.toHaveAttribute('aria-invalid', 'true');
  });

  it('a height below the floor marks the height box invalid', () => {
    render(<TagSizeControl width_mm={60} height_mm={40} presets={[]} onResize={vi.fn()} />);
    open();
    const h = screen.getByLabelText('Tag height (mm)');
    fireEvent.change(h, { target: { value: '5' } });
    fireEvent.blur(h);
    expect(h).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('Tag width (mm)')).not.toHaveAttribute('aria-invalid', 'true');
  });

  it('a grid columns value of 0 marks the columns box invalid', () => {
    render(
      <TagSizeControl
        width_mm={60}
        height_mm={40}
        presets={[]}
        onResize={vi.fn()}
        onSheetGridChange={vi.fn()}
      />,
    );
    open();
    const cols = screen.getByLabelText('Grid columns per A4');
    fireEvent.change(cols, { target: { value: '0' } });
    fireEvent.blur(cols);
    expect(cols).toHaveAttribute('aria-invalid', 'true');
  });
});

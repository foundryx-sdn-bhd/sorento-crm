/**
 * `TagSizeControl` (S1, PLAN D1) - lifted out of `RequestTagDesigner.tsx`
 * so the template editor can show the same control (AC-S1-1). The request
 * designer's OWN "Template sizes"/"Saved sizes" grouping, delete-x and
 * "Save as size" coverage lives in `RequestTagDesigner.test.tsx` (that
 * suite already exercises this component end-to-end through the request
 * designer's react-query wiring); this file is the component in isolation
 * - preset pick, custom W/H commit, the floor clamp, and what disappears
 * when a caller (the template editor) hands it fewer props.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { TagSizeControl } from './TagSizeControl';
import type { TagSizePreset } from '@/lib/dealer-kit/request-tags';

const PRESETS: TagSizePreset[] = [
  { label: 'Ala carte (60 x 40 mm)', width_mm: 60, height_mm: 40 },
  { label: 'Sink combo (95 x 44.5 mm)', width_mm: 95, height_mm: 44.5 },
];

/** AC-S5-1/S5-2: the panel is collapsed by default - every case that reads
 *  the select or the W/H inputs has to open it first. */
function openTagSizePanel() {
  fireEvent.click(screen.getByRole('button', { name: /Tag Size/ }));
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('TagSizeControl - preset pick (AC-S1-1)', () => {
  it('picking a preset from the dropdown resizes to its exact size', async () => {
    const onResize = vi.fn();
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={onResize} />,
    );
    openTagSizePanel();

    fireEvent.click(screen.getByRole('combobox'));
    fireEvent.click(await screen.findByRole('option', { name: /Sink combo/ }));

    expect(onResize).toHaveBeenCalledWith(95, 44.5);
  });

  it('shows the current size\'s own preset selected, not "Custom"', () => {
    render(
      <TagSizeControl width_mm={95} height_mm={44.5} presets={PRESETS} onResize={vi.fn()} />,
    );
    openTagSizePanel();

    expect(screen.getByRole('combobox')).toHaveTextContent('Sink combo');
  });

  it('falls through to the "Custom" placeholder once the size matches no preset', () => {
    render(
      <TagSizeControl width_mm={70} height_mm={30} presets={PRESETS} onResize={vi.fn()} />,
    );
    openTagSizePanel();

    expect(screen.getByRole('combobox')).toHaveTextContent('Custom');
  });
});

describe('TagSizeControl - custom W/H commit (AC-S1-1)', () => {
  it('commits a typed width on blur, not per keystroke', () => {
    const onResize = vi.fn();
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={onResize} />,
    );
    openTagSizePanel();

    fireEvent.change(screen.getByLabelText('Tag width (mm)'), { target: { value: '77' } });
    expect(onResize).not.toHaveBeenCalled();

    fireEvent.blur(screen.getByLabelText('Tag width (mm)'));
    expect(onResize).toHaveBeenCalledWith(77, 40);
  });

  it('commits a typed height on Enter (blurs the field)', () => {
    const onResize = vi.fn();
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={onResize} />,
    );
    openTagSizePanel();

    const input = screen.getByLabelText('Tag height (mm)') as HTMLInputElement;
    // jsdom only fires a real `blur` from `element.blur()` (what the
    // component's own Enter handler calls) when the element is actually the
    // focused one (`document.activeElement`) - `fireEvent.focus` dispatches
    // the event but does not itself move focus, so this calls the native
    // DOM method instead.
    input.focus();
    fireEvent.change(input, { target: { value: '52' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(onResize).toHaveBeenCalledWith(60, 52);
  });

  it('a non-numeric draft stays as typed on blur, says why, and does not call onResize', () => {
    const onResize = vi.fn();
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={onResize} />,
    );
    openTagSizePanel();

    fireEvent.change(screen.getByLabelText('Tag width (mm)'), { target: { value: 'abc' } });
    fireEvent.blur(screen.getByLabelText('Tag width (mm)'));

    expect(onResize).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Tag width (mm)')).toHaveValue(null);
    expect(screen.getByText(/^Enter a number/)).toBeInTheDocument();
  });
});

describe('TagSizeControl - floor clamp (AC-S1-4)', () => {
  it('a value below the 10mm floor stays as typed and is refused with the reason', () => {
    const onResize = vi.fn();
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={onResize} />,
    );
    openTagSizePanel();

    fireEvent.change(screen.getByLabelText('Tag width (mm)'), { target: { value: '5' } });
    fireEvent.blur(screen.getByLabelText('Tag width (mm)'));

    expect(onResize).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Tag width (mm)')).toHaveValue(5);
    expect(screen.getByText('Enter a number of at least 10.')).toBeInTheDocument();
  });

  it('a bounds prop with a ceiling refuses a value above it, with the reason shown', () => {
    const onResize = vi.fn();
    render(
      <TagSizeControl
        width_mm={60}
        height_mm={40}
        presets={PRESETS}
        onResize={onResize}
        bounds={{ min_mm: 10, max_width_mm: 100, max_height_mm: 100 }}
      />,
    );
    openTagSizePanel();

    fireEvent.change(screen.getByLabelText('Tag width (mm)'), { target: { value: '400' } });
    fireEvent.blur(screen.getByLabelText('Tag width (mm)'));

    expect(onResize).not.toHaveBeenCalled();
    expect(screen.getByText(/Largest that fits this sheet/)).toBeInTheDocument();
  });

  it('an absent bounds prop means the floor only - no ceiling refusal, however large', () => {
    const onResize = vi.fn();
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={onResize} />,
    );
    openTagSizePanel();

    fireEvent.change(screen.getByLabelText('Tag width (mm)'), { target: { value: '900' } });
    fireEvent.blur(screen.getByLabelText('Tag width (mm)'));

    expect(onResize).toHaveBeenCalledWith(900, 40);
  });
});

describe('TagSizeControl - optional affordances (AC-S1-1)', () => {
  it('renders no "Apply to all lines" button when onResizeAll is absent (the template editor\'s own case)', () => {
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={vi.fn()} />,
    );
    openTagSizePanel();

    expect(
      screen.queryByRole('button', { name: 'Apply to all lines' }),
    ).not.toBeInTheDocument();
  });

  it('renders "Apply to all lines" when onResizeAll IS given, and it fires with the current size', () => {
    const onResizeAll = vi.fn();
    render(
      <TagSizeControl
        width_mm={60}
        height_mm={40}
        presets={PRESETS}
        onResize={vi.fn()}
        onResizeAll={onResizeAll}
      />,
    );
    openTagSizePanel();

    fireEvent.click(screen.getByRole('button', { name: 'Apply to all lines' }));

    expect(onResizeAll).toHaveBeenCalledWith(60, 40);
  });

  it('renders no delete affordance on a saved size row without onDeleteSavedSize', async () => {
    render(
      <TagSizeControl
        width_mm={60}
        height_mm={40}
        presets={PRESETS}
        savedSizes={[
          {
            id: 'size-1',
            name: 'My favourite',
            width_mm: 80,
            height_mm: 50,
            created_by: null,
            created_by_name: null,
            created_at: '2026-08-01T00:00:00Z',
            updated_at: '2026-08-01T00:00:00Z',
          },
        ]}
        onResize={vi.fn()}
      />,
    );
    openTagSizePanel();

    fireEvent.click(screen.getByRole('combobox'));
    await screen.findByRole('option', { name: /My favourite/ });

    expect(screen.queryByLabelText(/Delete saved size/)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// S5 - the panel is a Collapsible, collapsed by default (D9, AC-S5-1..S5-3).
//
// PLAN-price-tag-ai-extract-resolver.md D9 / price-tag-ai-extract-resolver-
// acceptance-criteria.md S5.
// ---------------------------------------------------------------------------

describe('TagSizeControl - collapsed by default (AC-S5-1)', () => {
  it('renders collapsed: the heading, the current size inline, and none of the controls', () => {
    render(
      <TagSizeControl width_mm={95} height_mm={44.5} presets={PRESETS} onResize={vi.fn()} />,
    );

    expect(screen.getByText(/Tag Size/)).toBeInTheDocument();
    expect(screen.getByText(/95 x 44\.5 mm/)).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Tag width (mm)')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Tag height (mm)')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Apply to all lines' }),
    ).not.toBeInTheDocument();
  });
});

describe('TagSizeControl - toggling the panel (AC-S5-2)', () => {
  it('clicking the heading opens it (controls present), aria-expanded flips true', () => {
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={vi.fn()} />,
    );

    const trigger = screen.getByRole('button', { name: /Tag Size/ });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(trigger);

    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('combobox')).toBeInTheDocument();
    expect(screen.getByLabelText('Tag width (mm)')).toBeInTheDocument();
    expect(screen.getByLabelText('Tag height (mm)')).toBeInTheDocument();
  });

  it('clicking it again collapses it', () => {
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={vi.fn()} />,
    );
    const trigger = screen.getByRole('button', { name: /Tag Size/ });

    fireEvent.click(trigger);
    expect(screen.getByRole('combobox')).toBeInTheDocument();

    fireEvent.click(trigger);

    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });
});

describe('TagSizeControl - open state persisted per browser (AC-S5-3)', () => {
  it('localStorage["dealer-kit.tag-size.open"] === "1" renders it already open', () => {
    window.localStorage.setItem('dealer-kit.tag-size.open', '1');

    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={vi.fn()} />,
    );

    expect(screen.getByRole('combobox')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Tag Size/ }),
    ).toHaveAttribute('aria-expanded', 'true');
  });

  it('opening writes the key so the next mount in this browser opens too', () => {
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Tag Size/ }));

    expect(window.localStorage.getItem('dealer-kit.tag-size.open')).toBe('1');
  });

  it('closing writes the key so the next mount stays collapsed', () => {
    window.localStorage.setItem('dealer-kit.tag-size.open', '1');
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Tag Size/ }));

    expect(window.localStorage.getItem('dealer-kit.tag-size.open')).not.toBe('1');
  });

  it('a throwing localStorage does not break the render - it just stays collapsed', () => {
    const getItemSpy = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => {
        throw new Error('ZZT localStorage unavailable');
      });

    try {
      render(
        <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={vi.fn()} />,
      );

      expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: /Tag Size/ }),
      ).toHaveAttribute('aria-expanded', 'false');
    } finally {
      getItemSpy.mockRestore();
    }
  });
});

// ---------------------------------------------------------------------------
// AC-S7-13/AC-S7-14 (PLAN-price-tag-r10.md S7): the "Per A4" row - derived
// values greyed until typed, typing commits on blur, Turn toggles rotation,
// Auto clears back to derived, a configured grid whose cell is too small for
// the tag shows the one-line refusal. Already wired (`TagSizeControl.tsx`'s
// own `resolvedGrid`/`commitGrid`/`setTurn`), so these are GREEN regression
// guards - Phase 1 shipped the real control.
// ---------------------------------------------------------------------------

describe('TagSizeControl - Per A4 grid (AC-S7-13/S7-14)', () => {
  it('shows the DERIVED cols x rows, greyed, when no grid is configured', () => {
    render(
      <TagSizeControl
        width_mm={66.7}
        height_mm={31.9}
        presets={PRESETS}
        onResize={vi.fn()}
        sheetGrid={null}
        onSheetGridChange={vi.fn()}
      />,
    );
    openTagSizePanel();

    expect(screen.getByLabelText('Grid columns per A4')).toHaveValue(3);
    expect(screen.getByLabelText('Grid rows per A4')).toHaveValue(9);
    expect(screen.getByLabelText('Grid columns per A4').closest('div')).toHaveClass(
      'text-muted-foreground',
    );
    // No configured grid - no Auto affordance to clear.
    expect(screen.queryByRole('button', { name: 'Auto' })).toBeNull();
  });

  it('typing 2 and 7 then blur calls onSheetGridChange({cols: 2, rows: 7, turn: false})', () => {
    // 66.7 x 31.9 derives 3 x 9 (AC-S7-1) - deliberately DIFFERENT from the
    // 2/7 typed below: a controlled input set to the value it already shows
    // fires no React onChange at all (a jsdom/React quirk), which would pass
    // this test for the wrong reason (0 calls looking like "nothing to do").
    const onSheetGridChange = vi.fn();
    render(
      <TagSizeControl
        width_mm={66.7}
        height_mm={31.9}
        presets={PRESETS}
        onResize={vi.fn()}
        sheetGrid={null}
        onSheetGridChange={onSheetGridChange}
      />,
    );
    openTagSizePanel();

    const cols = screen.getByLabelText('Grid columns per A4');
    fireEvent.change(cols, { target: { value: '2' } });
    fireEvent.blur(cols);

    expect(onSheetGridChange).toHaveBeenCalledWith({ cols: 2, rows: 9, turn: false });

    // The control is stateless/controlled - the caller owns `sheetGrid` and
    // this test does not re-render with the just-committed value, so the
    // rows commit reads `cols` back off the DERIVED grid (3), not the 2 the
    // first commit sent (that round trip is the caller's job, covered by
    // `RequestTagDesigner.test.tsx`'s own wiring).
    const rows = screen.getByLabelText('Grid rows per A4');
    fireEvent.change(rows, { target: { value: '7' } });
    fireEvent.blur(rows);

    expect(onSheetGridChange).toHaveBeenLastCalledWith({ cols: 3, rows: 7, turn: false });
  });

  it('the Turn checkbox toggles turn on the configured (or derived) grid', () => {
    const onSheetGridChange = vi.fn();
    render(
      <TagSizeControl
        width_mm={100}
        height_mm={41}
        presets={PRESETS}
        onResize={vi.fn()}
        sheetGrid={{ cols: 2, rows: 7, turn: false }}
        onSheetGridChange={onSheetGridChange}
      />,
    );
    openTagSizePanel();

    fireEvent.click(screen.getByLabelText('Turn 90 degrees'));

    expect(onSheetGridChange).toHaveBeenCalledWith({ cols: 2, rows: 7, turn: true });
  });

  it('Auto clears the configured grid back to derived (null)', () => {
    const onSheetGridChange = vi.fn();
    render(
      <TagSizeControl
        width_mm={100}
        height_mm={41}
        presets={PRESETS}
        onResize={vi.fn()}
        sheetGrid={{ cols: 2, rows: 7, turn: false }}
        onSheetGridChange={onSheetGridChange}
      />,
    );
    openTagSizePanel();

    fireEvent.click(screen.getByRole('button', { name: 'Auto' }));

    expect(onSheetGridChange).toHaveBeenCalledWith(null);
  });

  it('AC-S7-13: a configured grid too small for the tag shows the one-line refusal naming the cell size', () => {
    render(
      <TagSizeControl
        width_mm={66.7}
        height_mm={31.9}
        presets={PRESETS}
        onResize={vi.fn()}
        sheetGrid={{ cols: 5, rows: 5, turn: false }}
        onSheetGridChange={vi.fn()}
      />,
    );
    openTagSizePanel();

    expect(
      screen.getByText(/Cell is 40 x 57\.4 mm - too small for this tag/),
    ).toBeInTheDocument();
  });

  it('the row is absent when the caller gives no onSheetGridChange (the template editor with no print size yet)', () => {
    render(
      <TagSizeControl width_mm={60} height_mm={40} presets={PRESETS} onResize={vi.fn()} />,
    );
    openTagSizePanel();

    expect(screen.queryByText('Per A4')).toBeNull();
  });
});

/**
 * The barcode inspector's value override + Relink (D23, S9 review S7; S5).
 *
 * Mirrors the text layer's `text_override` pattern one-for-one: typing sets
 * the override, and Relink - the ONLY way back to the bound value - is
 * offered only while one exists.
 *
 * Clearing the field by hand writes an EMPTY STRING, not `null` (S5,
 * reversing S9 review S7's original call): `''` is still an override, so the
 * canvas draws no barcode rather than snapping back to the product's - "I
 * should be able to delete and write whatever I want; if I want to relink I
 * should just click the Relink button we already have" (user, 5 Sep). Writing
 * `null` here used to mean "no override, follow the product", so deleting the
 * text and typing something new silently reverted to the product's barcode on
 * every keystroke.
 */

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import type { TagLayer } from '@/lib/dealer-kit/tag-template-types';
import { InspectorPanel } from './InspectorPanel';
import { toast } from '@/lib/toast';

// The real select is a Radix popover + cmdk list, which jsdom cannot open.
// A native <select> carrying the same options is enough to choose one, and
// every select in this panel is static-option mode (no `fetchOptions`).
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
  }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
  }) => (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

function barcodeLayer(overrides: Partial<TagLayer> = {}): TagLayer {
  return {
    id: 'bc1',
    type: 'barcode',
    x_mm: 0,
    y_mm: 0,
    width_mm: 40,
    height_mm: 22,
    rotation_deg: 0,
    z_index: 1,
    locked: false,
    visible: true,
    slot_binding: 'barcode',
    text_override: null,
    props: { kind: 'barcode', show_code: true },
    ...overrides,
  } as TagLayer;
}

function textLayer(overrides: Record<string, unknown> = {}): TagLayer {
  return {
    id: 't1',
    type: 'text',
    x_mm: 0,
    y_mm: 0,
    width_mm: 40,
    height_mm: 12,
    rotation_deg: 0,
    z_index: 1,
    locked: false,
    visible: true,
    slot_binding: null,
    text_override: null,
    props: {
      kind: 'text',
      text: 'Hello',
      fontFamily: 'DM Sans',
      fontSize: 12,
      fontWeight: 400,
      color: '#000000',
      align: 'left',
      lineHeight: 1.2,
      letterSpacing: 0,
      ...overrides,
    },
  } as TagLayer;
}

describe('InspectorPanel - text B/I/U/S toggle group (S2, AC-S2-5)', () => {
  it('reflects an already-formatted layer as pressed', () => {
    render(
      <InspectorPanel
        layer={textLayer({ fontWeight: 700, italic: true, underline: true, strikethrough: true })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Bold' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Italic' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Underline' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Strikethrough' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('shows nothing pressed for a plain layer', () => {
    render(
      <InspectorPanel layer={textLayer()} onUpdate={vi.fn()} onUpdateProps={vi.fn()} />,
    );

    expect(screen.getByRole('button', { name: 'Bold' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Italic' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('pressing Bold sets fontWeight to 700', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel
        layer={textLayer({ fontWeight: 400 })}
        onUpdate={vi.fn()}
        onUpdateProps={onUpdateProps}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Bold' }));

    expect(onUpdateProps).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ fontWeight: 700 }),
    );
  });

  it('unpressing Bold drops fontWeight to 400 (D10)', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel
        layer={textLayer({ fontWeight: 900 })}
        onUpdate={vi.fn()}
        onUpdateProps={onUpdateProps}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Bold' }));

    expect(onUpdateProps).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ fontWeight: 400 }),
    );
  });

  it('pressing Italic sets the italic flag without touching the others', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel
        layer={textLayer({ underline: true })}
        onUpdate={vi.fn()}
        onUpdateProps={onUpdateProps}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Italic' }));

    expect(onUpdateProps).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ italic: true, underline: true }),
    );
  });

  it('pressing Italic on a 600-weight layer leaves fontWeight at 600, not collapsed to 700', () => {
    // Regression: a `type="multiple"` ToggleGroup reports every currently
    // pressed item (including Bold, since 600 >= 600 reads as pressed) on
    // every click - toggling an unrelated flag must not rewrite the weight.
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel
        layer={textLayer({ fontWeight: 600 })}
        onUpdate={vi.fn()}
        onUpdateProps={onUpdateProps}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Italic' }));

    expect(onUpdateProps).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ fontWeight: 600, italic: true }),
    );
  });

  it('pressing Bold on a 600-weight layer drops it to 400 (D10), since 600 already reads as bold', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel
        layer={textLayer({ fontWeight: 600 })}
        onUpdate={vi.fn()}
        onUpdateProps={onUpdateProps}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Bold' }));

    expect(onUpdateProps).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ fontWeight: 400 }),
    );
  });
});

// ---------------------------------------------------------------------------
// AC-S16-1/S16-2 (PLAN-price-tag-ai-extract-resolver.md D21): a preview of
// the rendered merge-field text, with a one-click copy - the canvas inline
// edit still shows the source, so this is the only copy surface.
// ---------------------------------------------------------------------------

describe('InspectorPanel - copy the rendered text (S16)', () => {
  it('AC-S16-1: a token layer shows the rendered text and a "Copy rendered text" button; clicking it copies and toasts "Copied"', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });

    render(
      <InspectorPanel
        layer={textLayer({ text: '{{product.code}} widget' })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
        resolvedText="CBF3612 widget"
      />,
    );

    expect(screen.getByText('CBF3612 widget')).toBeInTheDocument();
    const copyBtn = screen.getByRole('button', { name: 'Copy rendered text' });

    fireEvent.click(copyBtn);

    expect(writeText).toHaveBeenCalledWith('CBF3612 widget');
    expect(toast.success).toHaveBeenCalledWith('Copied');
  });

  it('AC-S16-2: a layer with no token in its content shows no preview line', () => {
    render(
      <InspectorPanel
        layer={textLayer({ text: 'Plain words' })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
        resolvedText="Plain words"
      />,
    );

    expect(screen.queryByRole('button', { name: 'Copy rendered text' })).toBeNull();
  });
});

describe('InspectorPanel - barcode value override (D23, S9 review S7)', () => {
  it('typing into the barcode value field writes text_override', () => {
    const onUpdate = vi.fn();
    render(
      <InspectorPanel
        layer={barcodeLayer()}
        onUpdate={onUpdate}
        onUpdateProps={vi.fn()}
        resolvedText="4006381333931"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText('4006381333931'), {
      target: { value: '110000526' },
    });

    expect(onUpdate).toHaveBeenCalledWith('bc1', { text_override: '110000526' });
  });

  it('offers Relink only while overridden, not on a plain bound layer', () => {
    const { rerender } = render(
      <InspectorPanel
        layer={barcodeLayer()}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
        resolvedText="4006381333931"
      />,
    );
    expect(screen.queryByRole('button', { name: /relink/i })).not.toBeInTheDocument();

    rerender(
      <InspectorPanel
        layer={barcodeLayer({ text_override: '110000526' })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
        resolvedText="4006381333931"
      />,
    );
    expect(screen.getByRole('button', { name: /relink/i })).toBeInTheDocument();
  });

  it('Relink clears the override back to null, so it falls back to the bound barcode', () => {
    const onUpdate = vi.fn();
    render(
      <InspectorPanel
        layer={barcodeLayer({ text_override: '110000526' })}
        onUpdate={onUpdate}
        onUpdateProps={vi.fn()}
        resolvedText="4006381333931"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /relink/i }));

    expect(onUpdate).toHaveBeenCalledWith('bc1', { text_override: null });
  });

  it('shows the resolved (bound) value while unoverridden', () => {
    render(
      <InspectorPanel
        layer={barcodeLayer()}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
        resolvedText="4006381333931"
      />,
    );

    expect(screen.getByDisplayValue('4006381333931')).toBeInTheDocument();
  });

  it('clearing the field by hand writes an empty string, not null (S5)', () => {
    const onUpdate = vi.fn();
    render(
      <InspectorPanel
        layer={barcodeLayer({ text_override: '110000526' })}
        onUpdate={onUpdate}
        onUpdateProps={vi.fn()}
        resolvedText="4006381333931"
      />,
    );

    fireEvent.change(screen.getByDisplayValue('110000526'), { target: { value: '' } });

    expect(onUpdate).toHaveBeenCalledWith('bc1', { text_override: '' });
  });

  it('the Relink button and the amber note stay while the override is an empty string (S5, AC-S5-1)', () => {
    render(
      <InspectorPanel
        layer={barcodeLayer({ text_override: '' })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
        resolvedText="4006381333931"
      />,
    );

    expect(screen.getByRole('button', { name: /relink/i })).toBeInTheDocument();
    expect(screen.getByText('Unlinked from product data')).toBeInTheDocument();
    // The box itself stays empty - it must not fall back to the resolved value.
    expect(screen.getByPlaceholderText('4006381333931')).toHaveValue('');
  });

  it('typing a new value after clearing writes that value, never reverting to the product barcode (S5, AC-S5-2)', () => {
    const onUpdate = vi.fn();
    render(
      <InspectorPanel
        layer={barcodeLayer({ text_override: '' })}
        onUpdate={onUpdate}
        onUpdateProps={vi.fn()}
        resolvedText="4006381333931"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText('4006381333931'), {
      target: { value: '999888777' },
    });

    expect(onUpdate).toHaveBeenCalledWith('bc1', { text_override: '999888777' });
  });
});

describe('InspectorPanel - polygon shape (S4, AC-S4-1)', () => {
  function shapeLayer(shape: string, points?: { x: number; y: number }[]): TagLayer {
    return {
      id: 'sh1',
      type: 'shape',
      x_mm: 0,
      y_mm: 0,
      width_mm: 40,
      height_mm: 20,
      rotation_deg: 0,
      z_index: 1,
      locked: false,
      visible: true,
      slot_binding: null,
      text_override: null,
      props: {
        kind: 'shape',
        shape,
        fill: '#e0e0e0',
        stroke: '#999999',
        strokeWidth: 0.5,
        cornerRadius: 0,
        ...(points ? { points } : {}),
      },
    } as TagLayer;
  }

  it('offers Polygon and seeds the four corners, so it still looks like the rectangle', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel layer={shapeLayer('rect')} onUpdate={vi.fn()} onUpdateProps={onUpdateProps} />,
    );

    const select = screen
      .getByRole('option', { name: 'Polygon' })
      .closest('select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'polygon' } });

    expect(onUpdateProps).toHaveBeenCalledWith('sh1', expect.objectContaining({
      shape: 'polygon',
      points: [
        { x: 0, y: 0 },
        { x: 1, y: 0 },
        { x: 1, y: 1 },
        { x: 0, y: 1 },
      ],
    }));
  });

  it('drops the points again when the shape goes back to a rectangle', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel
        layer={shapeLayer('polygon', [
          { x: 0.25, y: 0 },
          { x: 1, y: 0 },
          { x: 1, y: 1 },
          { x: 0, y: 1 },
        ])}
        onUpdate={vi.fn()}
        onUpdateProps={onUpdateProps}
      />,
    );

    const select = screen
      .getByRole('option', { name: 'Rectangle' })
      .closest('select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'rect' } });

    const changes = onUpdateProps.mock.calls.at(-1)?.[1] as Record<string, unknown>;
    expect(changes.shape).toBe('rect');
    expect(changes.points).toBeUndefined();
  });

  it('names the shape plainly - "Polygon", not a parenthetical (AC-S4-12)', () => {
    render(
      <InspectorPanel layer={shapeLayer('rect')} onUpdate={vi.fn()} onUpdateProps={vi.fn()} />,
    );

    expect(screen.getByRole('option', { name: 'Polygon' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /free corners/i })).toBeNull();
  });

  it('keeps the corner radius available on a polygon (AC-S4-4)', () => {
    render(
      <InspectorPanel layer={shapeLayer('polygon')} onUpdate={vi.fn()} onUpdateProps={vi.fn()} />,
    );

    expect(screen.getByText('Corner Radius')).toBeInTheDocument();
  });
});

describe('InspectorPanel - Edit points button eligibility (S5, AC-S5-5)', () => {
  function shapeLayer(overrides: Partial<TagLayer> = {}): TagLayer {
    return {
      id: 'sh1',
      type: 'shape',
      x_mm: 0,
      y_mm: 0,
      width_mm: 40,
      height_mm: 20,
      rotation_deg: 0,
      z_index: 1,
      locked: false,
      visible: true,
      slot_binding: null,
      text_override: null,
      props: {
        kind: 'shape',
        shape: 'polygon',
        fill: '#e0e0e0',
        stroke: '#999999',
        strokeWidth: 0.5,
        cornerRadius: 0,
      },
      ...overrides,
    } as TagLayer;
  }

  it('is enabled for an unlocked, visible polygon', () => {
    render(
      <InspectorPanel
        layer={shapeLayer()}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
        onToggleEditPoints={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Edit points' })).not.toBeDisabled();
  });

  // Same eligibility `cornerHandleLayer` gates the CANVAS side's own corner
  // handles on (r4b) - a locked or hidden layer has no corner handles to
  // edit points of, so the button disables rather than opening a mode with
  // nothing on the canvas for it to show.
  it('disables for a LOCKED polygon', () => {
    render(
      <InspectorPanel
        layer={shapeLayer({ locked: true })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
        onToggleEditPoints={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Edit points' })).toBeDisabled();
  });

  it('disables for a HIDDEN polygon', () => {
    render(
      <InspectorPanel
        layer={shapeLayer({ visible: false })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
        onToggleEditPoints={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Edit points' })).toBeDisabled();
  });
});

describe('InspectorPanel - price badge box and typography (r4b, AC-S6-1/4)', () => {
  function badgeLayer(props: Record<string, unknown> = {}): TagLayer {
    return {
      id: 'pb1',
      type: 'price_badge',
      x_mm: 0,
      y_mm: 0,
      width_mm: 40,
      height_mm: 20,
      rotation_deg: 0,
      z_index: 1,
      locked: false,
      visible: true,
      slot_binding: null,
      text_override: null,
      props: {
        kind: 'price_badge',
        variant: 'list_only',
        fill: '#ffffff',
        textColor: '#000000',
        cornerRadius: 2,
        showNett: true,
        ...props,
      },
    } as TagLayer;
  }

  it('offers a Box checkbox on the list-only variant, and ticking it writes showBox', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel layer={badgeLayer()} onUpdate={vi.fn()} onUpdateProps={onUpdateProps} />,
    );

    const box = screen.getByRole('checkbox', { name: 'Box' });
    expect(box).not.toBeChecked();
    fireEvent.click(box);

    expect(onUpdateProps).toHaveBeenCalledWith(
      'pb1',
      expect.objectContaining({ showBox: true }),
    );
  });

  it('does not offer it on the promo variant, which is always boxed (AC-S6-3)', () => {
    render(
      <InspectorPanel
        layer={badgeLayer({ variant: 'promo' })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
      />,
    );

    expect(screen.queryByRole('checkbox', { name: 'Box' })).toBeNull();
  });

  it('shows the same typography controls a text layer has, and writes fontSize (AC-S6-4)', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel layer={badgeLayer()} onUpdate={vi.fn()} onUpdateProps={onUpdateProps} />,
    );

    for (const label of [
      'Font Family',
      'Font Size',
      'Font Weight',
      'Text Colour',
      'Align',
      'Line Height',
      'Letter Spacing',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'Bold' })).toBeInTheDocument();

    const size = screen.getByLabelText('Font Size');
    fireEvent.change(size, { target: { value: '22' } });

    expect(onUpdateProps).toHaveBeenCalledWith(
      'pb1',
      expect.objectContaining({ fontSize: 22 }),
    );
  });

  it('leaves the size box empty until the badge names one, so nothing is invented', () => {
    render(
      <InspectorPanel layer={badgeLayer()} onUpdate={vi.fn()} onUpdateProps={vi.fn()} />,
    );

    expect(screen.getByLabelText('Font Size')).toHaveValue(null);
  });

  it('offers a Show currency checkbox, on by default, and writes the flag (S3c, AC-13)', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel layer={badgeLayer()} onUpdate={vi.fn()} onUpdateProps={onUpdateProps} />,
    );

    const checkbox = screen.getByRole('checkbox', { name: 'Show currency' });
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);

    expect(onUpdateProps).toHaveBeenCalledWith(
      'pb1',
      expect.objectContaining({ showCurrency: false }),
    );
  });

  it('shows the checkbox unchecked once the badge has switched it off', () => {
    render(
      <InspectorPanel
        layer={badgeLayer({ showCurrency: false })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
      />,
    );

    expect(screen.getByRole('checkbox', { name: 'Show currency' })).not.toBeChecked();
  });
});

describe('InspectorPanel - image fit (S3b, AC-1/5)', () => {
  function imageLayer(props: Record<string, unknown> = {}): TagLayer {
    return {
      id: 'img1',
      type: 'image',
      x_mm: 0,
      y_mm: 0,
      width_mm: 40,
      height_mm: 20,
      rotation_deg: 0,
      z_index: 1,
      locked: false,
      visible: true,
      slot_binding: null,
      text_override: null,
      props: { kind: 'image', source: null, fit: 'contain', ...props },
    } as TagLayer;
  }

  it('offers Cover, Contain and Stretch', () => {
    render(
      <InspectorPanel layer={imageLayer()} onUpdate={vi.fn()} onUpdateProps={vi.fn()} />,
    );

    expect(screen.getByRole('option', { name: 'Cover' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Contain' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Stretch' })).toBeInTheDocument();
  });

  it('writes stretch when chosen (AC-1)', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel layer={imageLayer()} onUpdate={vi.fn()} onUpdateProps={onUpdateProps} />,
    );

    const select = screen
      .getByRole('option', { name: 'Stretch' })
      .closest('select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'stretch' } });

    expect(onUpdateProps).toHaveBeenCalledWith(
      'img1',
      expect.objectContaining({ fit: 'stretch' }),
    );
  });
});

describe('InspectorPanel - padding row (S3, AC-S3-1/2)', () => {
  it('shows the Padding row empty (0) when the text layer names none, and writes a side', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel layer={textLayer()} onUpdate={vi.fn()} onUpdateProps={onUpdateProps} />,
    );

    expect(screen.getByText('Padding (mm)')).toBeInTheDocument();
    expect(screen.getByLabelText('Top')).toHaveValue(0);
    expect(screen.getByLabelText('Right')).toHaveValue(0);
    expect(screen.getByLabelText('Bottom')).toHaveValue(0);
    expect(screen.getByLabelText('Left')).toHaveValue(0);

    fireEvent.change(screen.getByLabelText('Left'), { target: { value: '3' } });

    expect(onUpdateProps).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ padding: { top: 0, right: 0, bottom: 0, left: 3 } }),
    );
  });

  it('shows the layer own padding once it has one', () => {
    render(
      <InspectorPanel
        layer={textLayer({ padding: { top: 1, right: 2, bottom: 3, left: 4 } })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Top')).toHaveValue(1);
    expect(screen.getByLabelText('Right')).toHaveValue(2);
    expect(screen.getByLabelText('Bottom')).toHaveValue(3);
    expect(screen.getByLabelText('Left')).toHaveValue(4);
  });

  it('shows the same Padding row on a price badge, and writes a side (AC-S3-2)', () => {
    const onUpdateProps = vi.fn();
    const badge: TagLayer = {
      id: 'pb1',
      type: 'price_badge',
      x_mm: 0,
      y_mm: 0,
      width_mm: 40,
      height_mm: 20,
      rotation_deg: 0,
      z_index: 1,
      locked: false,
      visible: true,
      slot_binding: null,
      text_override: null,
      props: {
        kind: 'price_badge',
        variant: 'list_only',
        fill: '#ffffff',
        textColor: '#000000',
        cornerRadius: 2,
        showNett: true,
      },
    } as TagLayer;

    render(<InspectorPanel layer={badge} onUpdate={vi.fn()} onUpdateProps={onUpdateProps} />);

    // A price badge shows Margin ABOVE Padding (S3b, AC-6), so "Top" exists
    // twice - scope to the Padding group's own section.
    const paddingSection = screen.getByText('Padding (mm)').closest('div') as HTMLElement;
    expect(paddingSection).toBeInTheDocument();
    fireEvent.change(within(paddingSection).getByLabelText('Top'), { target: { value: '2' } });

    // A fresh badge carries neither field, so this is also its first-edit
    // seeding (S3b, AC-10): `margin` is written as zeros alongside the edited
    // `padding`, not left for `objectContaining` to hide.
    expect(onUpdateProps).toHaveBeenCalledWith(
      'pb1',
      expect.objectContaining({
        margin: { top: 0, right: 0, bottom: 0, left: 0 },
        padding: { top: 2, right: 0, bottom: 0, left: 0 },
      }),
    );
  });
});

describe('InspectorPanel - price badge margin (S3b, AC-6/9/10)', () => {
  function badgeLayer(props: Record<string, unknown> = {}): TagLayer {
    return {
      id: 'pb1',
      type: 'price_badge',
      x_mm: 0,
      y_mm: 0,
      width_mm: 40,
      height_mm: 20,
      rotation_deg: 0,
      z_index: 1,
      locked: false,
      visible: true,
      slot_binding: null,
      text_override: null,
      props: {
        kind: 'price_badge',
        variant: 'list_only',
        fill: '#ffffff',
        textColor: '#000000',
        cornerRadius: 2,
        showNett: true,
        ...props,
      },
    } as TagLayer;
  }

  it('shows a Margin (mm) group above Padding (mm) (AC-6)', () => {
    render(
      <InspectorPanel layer={badgeLayer()} onUpdate={vi.fn()} onUpdateProps={vi.fn()} />,
    );

    const labels = screen.getAllByText(/^(Margin|Padding) \(mm\)$/).map((el) => el.textContent);
    expect(labels).toEqual(['Margin (mm)', 'Padding (mm)']);
  });

  it('shows a legacy badge (padding set, no margin) with margin = the old padding, padding = 0 (AC-9)', () => {
    render(
      <InspectorPanel
        layer={badgeLayer({ padding: { top: 1, right: 2, bottom: 3, left: 4 } })}
        onUpdate={vi.fn()}
        onUpdateProps={vi.fn()}
      />,
    );

    const marginSection = screen.getByText('Margin (mm)').closest('div') as HTMLElement;
    expect(within(marginSection).getByLabelText('Top')).toHaveValue(1);
    expect(within(marginSection).getByLabelText('Right')).toHaveValue(2);
    expect(within(marginSection).getByLabelText('Bottom')).toHaveValue(3);
    expect(within(marginSection).getByLabelText('Left')).toHaveValue(4);

    const paddingSection = screen.getByText('Padding (mm)').closest('div') as HTMLElement;
    expect(within(paddingSection).getByLabelText('Top')).toHaveValue(0);
  });

  it('seeds margin from the legacy padding and zeroes padding on the first Margin edit (AC-10)', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel
        layer={badgeLayer({ padding: { top: 1, right: 2, bottom: 3, left: 4 } })}
        onUpdate={vi.fn()}
        onUpdateProps={onUpdateProps}
      />,
    );

    const marginSection = screen.getByText('Margin (mm)').closest('div') as HTMLElement;
    fireEvent.change(within(marginSection).getByLabelText('Top'), { target: { value: '9' } });

    expect(onUpdateProps).toHaveBeenCalledWith(
      'pb1',
      expect.objectContaining({
        // The side just edited changes; the sides untouched keep the legacy
        // padding, so the callout does not jump (AC-10).
        margin: { top: 9, right: 2, bottom: 3, left: 4 },
        padding: { top: 0, right: 0, bottom: 0, left: 0 },
      }),
    );
  });

  it('seeds margin from the legacy padding and restarts padding on the first Padding edit (AC-10)', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel
        layer={badgeLayer({ padding: { top: 1, right: 2, bottom: 3, left: 4 } })}
        onUpdate={vi.fn()}
        onUpdateProps={onUpdateProps}
      />,
    );

    const paddingSection = screen.getByText('Padding (mm)').closest('div') as HTMLElement;
    fireEvent.change(within(paddingSection).getByLabelText('Left'), { target: { value: '5' } });

    expect(onUpdateProps).toHaveBeenCalledWith(
      'pb1',
      expect.objectContaining({
        margin: { top: 1, right: 2, bottom: 3, left: 4 },
        padding: { top: 0, right: 0, bottom: 0, left: 5 },
      }),
    );
  });

  it('edits margin and padding independently once the badge already carries a margin', () => {
    const onUpdateProps = vi.fn();
    render(
      <InspectorPanel
        layer={badgeLayer({
          margin: { top: 2, right: 2, bottom: 2, left: 2 },
          padding: { top: 1, right: 1, bottom: 1, left: 1 },
        })}
        onUpdate={vi.fn()}
        onUpdateProps={onUpdateProps}
      />,
    );

    const marginSection = screen.getByText('Margin (mm)').closest('div') as HTMLElement;
    fireEvent.change(within(marginSection).getByLabelText('Top'), { target: { value: '9' } });

    expect(onUpdateProps).toHaveBeenLastCalledWith(
      'pb1',
      expect.objectContaining({
        margin: { top: 9, right: 2, bottom: 2, left: 2 },
        // Padding is untouched by a Margin edit once the badge already has
        // one - the two are independent from here on (AC-7).
        padding: { top: 1, right: 1, bottom: 1, left: 1 },
      }),
    );
  });
});

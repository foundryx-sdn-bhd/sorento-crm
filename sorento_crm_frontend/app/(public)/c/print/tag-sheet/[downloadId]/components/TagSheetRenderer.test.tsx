/**
 * What the PDF actually says (AC-L.1, AC-L.6).
 *
 * The print page is DOM/CSS, so a jsdom render IS the thing Chromium prints.
 * The badge assertions here are the other half of `price-badge.test.ts`: that
 * file pins the composition, this one proves the DOM renderer uses it, so the
 * proof on screen and the PDF cannot state a price differently.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { TagLayer, TagSheetDoc } from '@/lib/dealer-kit/tag-template-types';
import { defaultPriceBadgeProps } from '@/lib/dealer-kit/tag-template-types';
import TagSheetRenderer, { type ResolvedLineData } from './TagSheetRenderer';

const TAG_ID = 'tag-1';
const LINE_ID = 'line-1';

function resolved(overrides: Partial<ResolvedLineData> = {}): ResolvedLineData {
  return {
    tag_id: TAG_ID,
    tag_label: '1a',
    line_id: LINE_ID,
    code: 'SK-1234',
    name: 'Kitchen Sink',
    dimensions: '800 x 500 x 220 mm',
    spec_lines: 'Stainless steel',
    set_members: '',
    list_price: 1599,
    sell_price: 599,
    show_promo_price: true,
    included_accessories: '',
    quantity: 1,
    ...overrides,
  };
}

function layer(partial: Partial<TagLayer> & Pick<TagLayer, 'props' | 'type'>): TagLayer {
  return {
    id: 'l1',
    x_mm: 0,
    y_mm: 0,
    width_mm: 45,
    height_mm: 17,
    rotation_deg: 0,
    z_index: 1,
    locked: false,
    visible: true,
    slot_binding: null,
    text_override: null,
    ...partial,
  } as TagLayer;
}

function docWith(layers: TagLayer[]): TagSheetDoc {
  return {
    kind: 'tag_sheet',
    imposition: {
      preset: 'a4_3up',
      page_width_mm: 210,
      page_height_mm: 297,
      bleed_mm: 0,
      gap_mm: 2,
    },
    sheets: [
      {
        id: 's1',
        tags: [
          {
            id: 't1',
            template_id: 'tpl-1',
            request_tag_id: TAG_ID,
            x_mm: 5,
            y_mm: 5,
            width_mm: 95,
            height_mm: 130,
            layers,
          },
        ],
      },
    ],
  };
}

describe('price_badge on the print page', () => {
  it('prints the list price alone in the list_only variant', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({ type: 'price_badge', props: defaultPriceBadgeProps('list_only') }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('RM 1,599')).toBeInTheDocument();
    expect(screen.queryByText('NETT')).not.toBeInTheDocument();
  });

  // AC-S17-1 (PLAN-price-tag-ai-extract-resolver.md D22, B1 security review):
  // the print page is the other half of `KonvaTagLayer`'s own honouring of
  // textColor - the canvas and the PDF must never disagree about the colour
  // an unboxed amount prints in.
  it('AC-S17-1: draws the amount with the layer textColor, matching the canvas', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            props: { ...defaultPriceBadgeProps('list_only'), textColor: '#FFFFFF' },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('RM 1,599')).toHaveStyle({ color: '#FFFFFF' });
  });

  it('AC-S17-1: the empty placeholder stays #999999 regardless of textColor', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            props: { ...defaultPriceBadgeProps('list_only'), textColor: '#FFFFFF' },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved({ list_price: null, sell_price: null }) }}
      />,
    );

    expect(screen.getByText('Price TBC')).toHaveStyle({ color: '#999999' });
  });

  // Blocker follow-up (B1/S17): a `promo` badge whose offer never resolved
  // (`show_promo_price` false, or no sell price) falls through to the SAME
  // unboxed branch a genuine `list_only` badge draws through - but its own
  // `textColor` default is white, meant for the boxed callout it normally
  // draws. Honouring it here would print white text on the tag's own
  // background.
  it('draws black, never its own white, when a promo badge falls through to the unboxed branch', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            props: { ...defaultPriceBadgeProps('promo'), textColor: '#ffffff' },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved({ show_promo_price: false }) }}
      />,
    );

    expect(screen.getByText('RM 1,599')).toHaveStyle({ color: '#000000' });
  });

  it('prints the struck list price above SP RM 599 NETT in the promo variant', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({ type: 'price_badge', props: defaultPriceBadgeProps('promo') }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const struck = screen.getByText('LP: RM 1,599');
    expect(struck).toBeInTheDocument();
    expect(struck).toHaveStyle({ textDecoration: 'line-through' });
    expect(screen.getByText('SP')).toBeInTheDocument();
    expect(screen.getByText('RM 599')).toBeInTheDocument();
    expect(screen.getByText('NETT')).toBeInTheDocument();
  });

  it('keeps the pre-r4b promo sizes when the layer names no typography (r4c, AC-S6-5)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({ type: 'price_badge', props: defaultPriceBadgeProps('promo') }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    // figureSize falls back to 16pt when the layer names no fontSize; the
    // struck list price and the SP/NETT labels keep their proportion to it
    // (0.5625 and 0.5), unchanged since before typography existed.
    expect(screen.getByText('RM 599')).toHaveStyle({ fontSize: '16pt' });
    expect(screen.getByText('LP: RM 1,599')).toHaveStyle({ fontSize: '9pt' });
    expect(screen.getByText('SP')).toHaveStyle({ fontSize: '8pt' });
    expect(screen.getByText('NETT')).toHaveStyle({ fontSize: '8pt' });
  });

  it('falls back to the list price when the line turns the promo price off', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({ type: 'price_badge', props: defaultPriceBadgeProps('promo') }),
        ])}
        resolvedData={{ [TAG_ID]: resolved({ show_promo_price: false }) }}
      />,
    );

    expect(screen.getByText('RM 1,599')).toBeInTheDocument();
    expect(screen.queryByText('LP: RM 1,599')).not.toBeInTheDocument();
  });
});

describe('the price badge box on the print page (r4b, AC-S6-1/2/5)', () => {
  it('prints no box for a list-only badge saved before the flag (AC-S6-5)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({ type: 'price_badge', props: defaultPriceBadgeProps('list_only') }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(container.querySelector('svg')).toBeNull();
    expect(screen.getByText('RM 1,599')).toBeInTheDocument();
  });

  it('prints the callout as an SVG path once the layer asks for a box (AC-S6-1)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            width_mm: 40,
            height_mm: 20,
            props: {
              ...defaultPriceBadgeProps('list_only'),
              showBox: true,
              fill: '#ffffff',
              textColor: '#111111',
              cornerRadius: 0,
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 40 20');
    const path = svg?.querySelector('path');
    expect(path?.getAttribute('d')).toBe('M 0 0 L 40 0 L 40 20 L 0 20 Z');
    expect(path?.getAttribute('fill')).toBe('#ffffff');
    expect(screen.getByText('RM 1,599')).toHaveStyle({ color: '#111111' });

    // The frame clips: a callout whose corners round inward must not bleed a
    // square edge past the badge's own box (r5 review).
    expect(svg?.parentElement).toHaveStyle({ overflow: 'hidden' });
  });

  it('follows the layer own corners, so the callout slants in the PDF too (AC-S6-2)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            width_mm: 40,
            height_mm: 20,
            props: {
              ...defaultPriceBadgeProps('list_only'),
              showBox: true,
              cornerRadius: 0,
              points: [
                { x: 0.25, y: 0 },
                { x: 1, y: 0 },
                { x: 1, y: 1 },
                { x: 0, y: 1 },
              ],
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(container.querySelector('svg path')?.getAttribute('d')).toBe(
      'M 10 0 L 40 0 L 40 20 L 0 20 Z',
    );
  });

  it('leaves the promotional block on its rounded rectangle (AC-S6-3)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({ type: 'price_badge', props: defaultPriceBadgeProps('promo') }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(container.querySelector('svg')).toBeNull();
    expect(screen.getByText('SP')).toBeInTheDocument();
  });
});

describe('the price badge figure typography on the print page (r4b, AC-S6-4/5)', () => {
  it('sets the figure in the layer own face, size and style', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            props: {
              ...defaultPriceBadgeProps('list_only'),
              fontFamily: 'Bebas Neue',
              fontSize: 22,
              fontWeight: 900,
              italic: true,
              strikethrough: true,
              align: 'left',
              letterSpacing: 0.5,
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const figure = screen.getByText('RM 1,599');
    expect(figure).toHaveStyle({
      fontSize: '22pt',
      fontWeight: '900',
      fontStyle: 'italic',
      textDecoration: 'line-through',
      textAlign: 'left',
      letterSpacing: '0.5px',
    });
    // The face is set on the frame, so every part of the badge shares it.
    expect(figure.parentElement).toHaveStyle({ fontFamily: 'Bebas Neue' });
  });

  it('keeps 13pt / 700 / DM Sans for a badge that names none (AC-S6-5)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({ type: 'price_badge', props: defaultPriceBadgeProps('list_only') }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const figure = screen.getByText('RM 1,599');
    expect(figure).toHaveStyle({ fontSize: '13pt', fontWeight: '700', textAlign: 'center' });
    expect(figure.parentElement).toHaveStyle({ fontFamily: 'DM Sans, sans-serif' });
  });

  it('scales the promotional block struck price, SP and NETT with the figure', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            props: { ...defaultPriceBadgeProps('promo'), fontSize: 32 },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('RM 599')).toHaveStyle({ fontSize: '32pt' });
    expect(screen.getByText('LP: RM 1,599')).toHaveStyle({ fontSize: '18pt' });
    expect(screen.getByText('NETT')).toHaveStyle({ fontSize: '16pt' });
  });
});

describe('bound text and pictures on the print page', () => {
  it('resolves a slot-bound text layer against the line', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'text-code',
            type: 'text',
            slot_binding: 'code',
            props: {
              kind: 'text',
              text: 'placeholder',
              fontFamily: 'DM Sans',
              fontSize: 10,
              fontWeight: 400,
              color: '#000',
              align: 'left',
              lineHeight: 1.2,
              letterSpacing: 0,
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('SK-1234')).toBeInTheDocument();
    expect(screen.queryByText('placeholder')).not.toBeInTheDocument();
  });

  it('prints the typed text when the layer was unlinked', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'text-code',
            type: 'text',
            slot_binding: 'code',
            text_override: 'SHOWROOM',
            props: {
              kind: 'text',
              text: 'placeholder',
              fontFamily: 'DM Sans',
              fontSize: 10,
              fontWeight: 400,
              color: '#000',
              align: 'left',
              lineHeight: 1.2,
              letterSpacing: 0,
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('SHOWROOM')).toBeInTheDocument();
  });

  it('renders italic, underline and strikethrough on the printed text (AC-S2-7)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'text-styled',
            type: 'text',
            props: {
              kind: 'text',
              text: 'Sale',
              fontFamily: 'DM Sans',
              fontSize: 10,
              fontWeight: 400,
              color: '#000',
              align: 'left',
              lineHeight: 1.2,
              letterSpacing: 0,
              italic: true,
              underline: true,
              strikethrough: true,
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const el = screen.getByText('Sale');
    expect(el).toHaveStyle({ fontStyle: 'italic' });
    expect(el).toHaveStyle({ textDecoration: 'underline line-through' });
  });

  it('renders normal style and no decoration when the flags are absent (old doc, AC-S2-9)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'text-plain',
            type: 'text',
            props: {
              kind: 'text',
              text: 'Plain',
              fontFamily: 'DM Sans',
              fontSize: 10,
              fontWeight: 400,
              color: '#000',
              align: 'left',
              lineHeight: 1.2,
              letterSpacing: 0,
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const el = screen.getByText('Plain');
    expect(el).toHaveStyle({ fontStyle: 'normal' });
    expect(el).toHaveStyle({ textDecoration: 'none' });
  });

  it('rotates a layer about its own top-left corner, not the CSS box centre (r4c)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'text-rotated',
            type: 'text',
            rotation_deg: 30,
            props: {
              kind: 'text',
              text: 'Rotated',
              fontFamily: 'DM Sans',
              fontSize: 10,
              fontWeight: 400,
              color: '#000',
              align: 'left',
              lineHeight: 1.2,
              letterSpacing: 0,
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const el = screen.getByText('Rotated');
    expect(el).toHaveStyle({ transform: 'rotate(30deg)' });
    expect(el.style.transformOrigin).toBe('0 0');
  });

  it('leaves an unrotated layer with no transform-origin', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'text-flat',
            type: 'text',
            rotation_deg: 0,
            props: {
              kind: 'text',
              text: 'Flat',
              fontFamily: 'DM Sans',
              fontSize: 10,
              fontWeight: 400,
              color: '#000',
              align: 'left',
              lineHeight: 1.2,
              letterSpacing: 0,
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const el = screen.getByText('Flat');
    expect(el.style.transform).toBe('');
    expect(el.style.transformOrigin).toBe('');
  });

  it('draws an asset image from the payload map', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            props: {
              kind: 'image',
              source: { type: 'asset', assetId: 'a1' },
              fit: 'contain',
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/a1.png' }}
      />,
    );

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      'https://cdn.test/a1.png',
    );
  });

  it('draws a product photo from the payload map', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            props: {
              kind: 'image',
              source: { type: 'product_attachment', attachmentId: 'att-1' },
              fit: 'cover',
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        images={{ 'att-1': 'https://cdn.test/photo.jpg' }}
      />,
    );

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      'https://cdn.test/photo.jpg',
    );
  });

  it('draws the primary photo for a slot-bound image layer the template left unset', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            slot_binding: 'product_image',
            props: { kind: 'image', source: null, fit: 'contain' },
          }),
        ])}
        resolvedData={{
          [TAG_ID]: resolved({
            images: [
              { attachment_id: 'att-1', url: 'https://cdn.test/other.jpg', is_primary: false },
              { attachment_id: 'att-2', url: 'https://cdn.test/primary.jpg', is_primary: true },
            ],
          }),
        }}
        images={{
          'att-1': 'https://cdn.test/other.jpg',
          'att-2': 'https://cdn.test/primary.jpg',
        }}
      />,
    );

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      'https://cdn.test/primary.jpg',
    );
  });

  it('draws the primary photo for a product_slot layer that holds the photo', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'slot',
            type: 'product_slot',
            props: { kind: 'product_slot', fieldKey: 'product_image' },
          }),
        ])}
        resolvedData={{
          [TAG_ID]: resolved({
            images: [
              { attachment_id: 'att-2', url: 'https://cdn.test/primary.jpg', is_primary: true },
            ],
          }),
        }}
        images={{ 'att-2': 'https://cdn.test/primary.jpg' }}
      />,
    );

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      'https://cdn.test/primary.jpg',
    );
  });

  // ---------------------------------------------------------------------
  // F8 (D7, AC-S4-3/S4-5): a part subject's photo resolves from the SAME
  // `images` payload map product_image slots already read for the parent -
  // the page passes `payload.images` straight through
  // (`app/(public)/c/print/tag-sheet/[downloadId]/page.tsx`), so the map
  // has to actually carry a part's attachment id for the print path to
  // agree with the canvas.
  // ---------------------------------------------------------------------
  it("draws a part subject's OWN photo from the payload map, not the parent's", () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'slot',
            type: 'product_slot',
            props: { kind: 'product_slot', fieldKey: 'product_image', subjectPart: 0 },
          }),
        ])}
        resolvedData={{
          [TAG_ID]: resolved({
            images: [
              { attachment_id: 'att-parent', url: 'https://cdn.test/parent.jpg', is_primary: true },
            ],
            parts: [
              {
                product_id: 'p-tap',
                code: 'SRTTAP100',
                name: 'ZZT Kitchen Tap',
                dimensions: '',
                images: [
                  { attachment_id: 'att-part-tap', url: 'https://cdn.test/tap.jpg', is_primary: true },
                ],
              },
            ],
          }),
        }}
        images={{
          'att-parent': 'https://cdn.test/parent.jpg',
          'att-part-tap': 'https://cdn.test/tap.jpg',
        }}
      />,
    );

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      'https://cdn.test/tap.jpg',
    );
  });

  it("shows the empty placeholder (no <img>) when the part subject's attachment id is missing from the payload's images map", () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'slot',
            type: 'product_slot',
            props: { kind: 'product_slot', fieldKey: 'product_image', subjectPart: 0 },
          }),
        ])}
        resolvedData={{
          [TAG_ID]: resolved({
            images: [
              { attachment_id: 'att-parent', url: 'https://cdn.test/parent.jpg', is_primary: true },
            ],
            parts: [
              {
                product_id: 'p-tap',
                code: 'SRTTAP100',
                name: 'ZZT Kitchen Tap',
                dimensions: '',
                images: [
                  { attachment_id: 'att-part-tap', url: 'https://cdn.test/tap.jpg', is_primary: true },
                ],
              },
            ],
          }),
        }}
        // The part's id ('att-part-tap') is missing - a gap between what the
        // resolved row's own `parts[].images` says and what the payload's
        // top-level `images` map actually carries.
        images={{ 'att-parent': 'https://cdn.test/parent.jpg' }}
      />,
    );

    expect(container.querySelector('img')).toBeNull();
  });

  it('still draws an image layer saved before the source discriminator existed', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            // A document written by the first version of the editor.
            props: { kind: 'image', source: null, assetId: 'a1', fit: 'contain' },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/legacy.png' }}
      />,
    );

    expect(container.querySelector('img')).toHaveAttribute(
      'src',
      'https://cdn.test/legacy.png',
    );
  });
});

// ---------------------------------------------------------------------------
// Image fit on the print page (S3b, AC-3/5)
// ---------------------------------------------------------------------------

describe('image fit on the print page (S3b, AC-3/5)', () => {
  it('draws stretch as object-fit: fill (AC-3)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            props: {
              kind: 'image',
              source: { type: 'asset', assetId: 'a1' },
              fit: 'stretch',
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/photo.png' }}
      />,
    );

    expect(container.querySelector('img')).toHaveStyle({ objectFit: 'fill' });
  });

  it('keeps cover and contain exactly as before (AC-5)', () => {
    const { container: coverContainer } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            props: { kind: 'image', source: { type: 'asset', assetId: 'a1' }, fit: 'cover' },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/photo.png' }}
      />,
    );
    expect(coverContainer.querySelector('img')).toHaveStyle({ objectFit: 'cover' });

    const { container: containContainer } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            props: { kind: 'image', source: { type: 'asset', assetId: 'a1' }, fit: 'contain' },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/photo.png' }}
      />,
    );
    expect(containContainer.querySelector('img')).toHaveStyle({ objectFit: 'contain' });
  });
});

// ---------------------------------------------------------------------------
// Image crop on the print page (S8, PLAN D8, AC-S8-4/6)
//
// jsdom never actually decodes an image, so `CroppedImage`'s own `onLoad`
// (which is what tells it the SOURCE's real pixel size) never fires on its
// own - `fireImageLoad` below dispatches it by hand with a known
// naturalWidth/naturalHeight, the same event the real browser would raise.
// ---------------------------------------------------------------------------

function fireImageLoad(img: HTMLImageElement, natural: { width: number; height: number }) {
  Object.defineProperty(img, 'naturalWidth', { value: natural.width, configurable: true });
  Object.defineProperty(img, 'naturalHeight', { value: natural.height, configurable: true });
  fireEvent.load(img);
}

describe('image crop on the print page (S8, AC-S8-4)', () => {
  it('before the natural size loads, falls back to the plain (uncropped) fit render', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            width_mm: 60,
            height_mm: 20,
            props: {
              kind: 'image',
              source: { type: 'asset', assetId: 'a1' },
              fit: 'contain',
              cropRect: { x: 0.25, y: 0, width: 0.5, height: 1 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/photo.png' }}
      />,
    );

    const images = container.querySelectorAll('img');
    expect(images).toHaveLength(1);
    expect(images[0]).toHaveStyle({ objectFit: 'contain' });
  });

  it('once loaded, shows only the cropped region - the img scaled and offset inside a locked-aspect wrapper (AC-S8-4)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            width_mm: 60,
            height_mm: 20,
            props: {
              kind: 'image',
              source: { type: 'asset', assetId: 'a1' },
              fit: 'contain',
              cropRect: { x: 0.25, y: 0, width: 0.5, height: 1 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/photo.png' }}
      />,
    );

    fireImageLoad(container.querySelector('img')!, { width: 300, height: 150 });

    // Cropped to half the source width - the img is drawn at 200% and
    // shifted left 50% so only that half ever shows through the wrapper's
    // own `overflow: hidden`.
    const cropped = container.querySelector('img')!;
    expect(cropped).toHaveStyle({ width: '200%', height: '100%', left: '-50%', top: '0%' });
    const wrapper = cropped.parentElement!;
    expect(wrapper).toHaveStyle({ overflow: 'hidden', aspectRatio: '150 / 150' });
  });

  it('never swaps in a second <img> once the natural size loads (S3 review)', () => {
    // The print page's own readiness effect snapshots `document.images`
    // ONCE, synchronously - a second `<img>` appearing only after the
    // FIRST one's own `load` had already settled that snapshot would never
    // be in it, so `data-dk-print-ready` could flip true before the
    // actually-cropped element had painted and Chromium could capture the
    // uncropped picture instead.
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            width_mm: 60,
            height_mm: 20,
            props: {
              kind: 'image',
              source: { type: 'asset', assetId: 'a1' },
              fit: 'contain',
              cropRect: { x: 0.25, y: 0, width: 0.5, height: 1 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/photo.png' }}
      />,
    );

    const before = container.querySelectorAll('img');
    expect(before).toHaveLength(1);
    const node = before[0];

    fireImageLoad(node, { width: 300, height: 150 });

    const after = container.querySelectorAll('img');
    expect(after).toHaveLength(1);
    // The SAME DOM node, not a different one at the same query position -
    // this is what actually rules out an unmount/remount.
    expect(after[0]).toBe(node);
  });

  it('centers the CONTAIN window in its own box, the same way Konva centers it (S3 review)', () => {
    // A wider-than-tall crop (0.5 wide, full height) inside a 60x20mm box:
    // `contain` sizes the window to the box's own height and leaves it
    // narrower than the box - a block layout would pin that auto-sized
    // window to the left edge instead of centering it the way Konva's
    // `x={(w - drawW) / 2}` (`KonvaTagLayer.tsx`) always does.
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            width_mm: 60,
            height_mm: 20,
            props: {
              kind: 'image',
              source: { type: 'asset', assetId: 'a1' },
              fit: 'contain',
              cropRect: { x: 0.25, y: 0, width: 0.5, height: 1 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/photo.png' }}
      />,
    );

    fireImageLoad(container.querySelector('img')!, { width: 300, height: 150 });

    const cropped = container.querySelector('img')!;
    const windowWrapper = cropped.parentElement!;
    const outerWrapper = windowWrapper.parentElement!;
    expect(outerWrapper).toHaveStyle({
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    });
  });

  it('works the same for stretch and cover fits, not just contain (AC-S8-4)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            width_mm: 60,
            height_mm: 20,
            props: {
              kind: 'image',
              source: { type: 'asset', assetId: 'a1' },
              fit: 'cover',
              cropRect: { x: 0, y: 0.25, width: 1, height: 0.5 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/photo.png' }}
      />,
    );

    fireImageLoad(container.querySelector('img')!, { width: 300, height: 150 });

    const cropped = container.querySelector('img')!;
    expect(cropped).toHaveStyle({ width: '100%', height: '200%', left: '0%', top: '-50%' });
  });

  it('keeps the circle mask on the OUTER box, unaffected by cropping', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            width_mm: 60,
            height_mm: 20,
            props: {
              kind: 'image',
              source: { type: 'asset', assetId: 'a1' },
              fit: 'contain',
              maskShape: 'circle',
              cropRect: { x: 0.25, y: 0, width: 0.5, height: 1 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/photo.png' }}
      />,
    );

    fireImageLoad(container.querySelector('img')!, { width: 300, height: 150 });

    // The outer per-layer box carries the mask, same as the uncropped path -
    // cropping only ever touches the img/wrapper nested inside it.
    const outer = container.querySelector('img')!.closest('div[style*="border-radius"]');
    expect(outer).toHaveStyle({ borderRadius: '50%' });
  });

  it('a template saved before S8 (no cropRect) renders exactly as before (AC-S8-6)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'img',
            type: 'image',
            width_mm: 60,
            height_mm: 20,
            props: { kind: 'image', source: { type: 'asset', assetId: 'a1' }, fit: 'contain' },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{ a1: 'https://cdn.test/photo.png' }}
      />,
    );

    // No cropRect at all: exactly one plain <img>, `isCropped` is false, so
    // this never even enters `CroppedImage` - no `onLoad` needed to prove it.
    const images = container.querySelectorAll('img');
    expect(images).toHaveLength(1);
    expect(images[0]).toHaveStyle({ objectFit: 'contain', width: '100%', height: '100%' });
  });
});

// ---------------------------------------------------------------------------
// Barcode layer on the print page (AC-S7-3, AC-S7-4, AC-S7-6)
// ---------------------------------------------------------------------------

describe('barcode on the print page', () => {
  // Checksum-valid, same value the browser-verification run seeds onto a
  // real ZZT- test product.
  const VALID_EAN13 = '4006381333931';

  function barcodeLayer(showCode = true): TagLayer {
    return layer({
      id: 'bc1',
      type: 'barcode',
      slot_binding: 'barcode',
      width_mm: 40,
      height_mm: 22,
      props: { kind: 'barcode', show_code: showCode },
    });
  }

  it('draws the label plate: product-code strip and guard-split human-readable digits', () => {
    render(
      <TagSheetRenderer
        doc={docWith([barcodeLayer()])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: VALID_EAN13 }) }}
        assets={{}}
        images={{}}
      />,
    );

    expect(screen.getByText('SK-1234')).toBeInTheDocument();
    // Guard-split: digit, group of 6, group of 6 - the same shape
    // `humanReadableBarcode` pins in lib/dealer-kit/barcode.test.ts, so the
    // print DOM cannot drift from the editor's own preview.
    expect(screen.getByText('4 006381 333931')).toBeInTheDocument();
  });

  it('draws the bars themselves as a real data: URL image (jsdom canvas via the `canvas` package)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([barcodeLayer()])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: VALID_EAN13 }) }}
        assets={{}}
        images={{}}
      />,
    );

    // Only one <img> on this plate: the bars. `jsbarcode` throws in stock
    // jsdom (no <canvas> 2d context); the `canvas` devDependency is what
    // makes this assertion possible instead of a permanently-untested path.
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img?.getAttribute('src')).toMatch(/^data:image\/png;base64,/);
  });

  it('omits the product-code strip when show_code is off', () => {
    render(
      <TagSheetRenderer
        doc={docWith([barcodeLayer(false)])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: VALID_EAN13 }) }}
        assets={{}}
        images={{}}
      />,
    );

    expect(screen.queryByText('SK-1234')).not.toBeInTheDocument();
    expect(screen.getByText('4 006381 333931')).toBeInTheDocument();
  });

  it('prints a Code128 value plain, with no guard split', () => {
    render(
      <TagSheetRenderer
        doc={docWith([barcodeLayer()])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: 'SKU-NOT-EAN' }) }}
        assets={{}}
        images={{}}
      />,
    );

    expect(screen.getByText('SKU-NOT-EAN')).toBeInTheDocument();
  });

  it('draws nothing at all when the line carries no barcode (AC-S7-3)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([barcodeLayer()])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: null }) }}
        assets={{}}
        images={{}}
      />,
    );

    // Not the editor's dashed placeholder - nothing, because a physical tag
    // has no business printing "no data yet" language.
    expect(container.querySelector('[style*="border-radius"]')).toBeNull();
    expect(container.textContent).not.toContain('SK-1234');
  });

  it('draws nothing for an empty-string barcode either', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([barcodeLayer()])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: '' }) }}
        assets={{}}
        images={{}}
      />,
    );

    expect(container.querySelector('[style*="border-radius"]')).toBeNull();
  });

  // ---------------------------------------------------------------------------
  // Barcode value override (D23, S9, AC-S9-2) - the print page prints the
  // designer's typed override, not the bound line's barcode, using the SAME
  // `resolveBarcodeValue` the Konva editor calls (product-block.ts).
  // ---------------------------------------------------------------------------

  it('prints the override instead of the bound barcode (AC-S9-2)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([{ ...barcodeLayer(), text_override: '110000526' }])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: VALID_EAN13 }) }}
        assets={{}}
        images={{}}
      />,
    );

    // Code128 (non-EAN), so no guard split - plain override text.
    expect(screen.getByText('110000526')).toBeInTheDocument();
    expect(screen.queryByText('4 006381 333931')).not.toBeInTheDocument();
  });

  it('prints the override even when the line carries no barcode of its own', () => {
    render(
      <TagSheetRenderer
        doc={docWith([{ ...barcodeLayer(), text_override: VALID_EAN13 }])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: null }) }}
        assets={{}}
        images={{}}
      />,
    );

    expect(screen.getByText('4 006381 333931')).toBeInTheDocument();
  });

  it('an empty override draws nothing, never falling back to a real product barcode (S5)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([{ ...barcodeLayer(), text_override: '' }])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: VALID_EAN13 }) }}
        assets={{}}
        images={{}}
      />,
    );

    expect(container.querySelector('[style*="border-radius"]')).toBeNull();
    expect(screen.queryByText('4 006381 333931')).not.toBeInTheDocument();
  });

  it('sizes the code-strip and human-readable text in pt, proportional to the plate (AC-S7-4/6)', () => {
    // A 40x22mm plate - the toolbar's default insert size. Against the BUG
    // (`Math.max(6, strip * 0.6)}mm`) this reads as a 6mm floor (~17pt): a
    // fixed, oversized font that does not move with the plate. The fix reads
    // it in pt (`MM_TO_PT`), well under 17pt and different for a differently
    // proportioned plate, which is what "proportional" asserts below.
    render(
      <TagSheetRenderer
        doc={docWith([barcodeLayer()])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: VALID_EAN13 }) }}
        assets={{}}
        images={{}}
      />,
    );

    const strip = screen.getByText('SK-1234');
    const stripFontSize = strip.style.fontSize;
    expect(stripFontSize).toMatch(/pt$/);
    expect(parseFloat(stripFontSize)).toBeLessThan(17);

    const human = screen.getByText('4 006381 333931');
    const humanFontSize = human.style.fontSize;
    expect(humanFontSize).toMatch(/pt$/);
    expect(parseFloat(humanFontSize)).toBeLessThan(17);

    // A taller plate at the same width grows the strip band, and the font
    // with it - pinning that the size is DERIVED from the plate, not a
    // constant every plate happens to clip to the same floor.
    const { container: tallerContainer } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'bc-tall',
            type: 'barcode',
            slot_binding: 'barcode',
            width_mm: 40,
            height_mm: 44,
            props: { kind: 'barcode', show_code: true },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved({ barcode: VALID_EAN13 }) }}
        assets={{}}
        images={{}}
      />,
    );
    const tallerStrip = tallerContainer.querySelectorAll('[style*="font-weight: 700"]')[0] as HTMLElement;
    expect(parseFloat(tallerStrip.style.fontSize)).toBeGreaterThan(
      parseFloat(stripFontSize),
    );
  });
});

describe('polygon shape on the print page (S4, AC-S4-6)', () => {
  it('prints an SVG path built by the same helper the canvas draws with', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'poly-1',
            type: 'shape',
            width_mm: 40,
            height_mm: 20,
            props: {
              kind: 'shape',
              shape: 'polygon',
              fill: '#ff0000',
              stroke: '#000000',
              strokeWidth: 0.5,
              cornerRadius: 0,
              points: [
                { x: 0.25, y: 0 },
                { x: 1, y: 0 },
                { x: 1, y: 1 },
                { x: 0, y: 1 },
              ],
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{}}
        images={{}}
      />,
    );

    const svg = container.querySelector('svg');
    expect(svg).toBeTruthy();
    // The viewBox is the layer's own mm box, so the path data is in mm and
    // `strokeWidth` means the same thing here as on every other shape.
    expect(svg?.getAttribute('viewBox')).toBe('0 0 40 20');
    const path = svg?.querySelector('path');
    expect(path?.getAttribute('d')).toBe('M 10 0 L 40 0 L 40 20 L 0 20 Z');
    expect(path?.getAttribute('fill')).toBe('#ff0000');
    expect(path?.getAttribute('stroke')).toBe('#000000');
    expect(path?.getAttribute('stroke-width')).toBe('0.5');
  });

  it('draws the four corners for a polygon saved without points (AC-S4-8)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'poly-2',
            type: 'shape',
            width_mm: 40,
            height_mm: 20,
            props: {
              kind: 'shape',
              shape: 'polygon',
              fill: '#ff0000',
              stroke: 'transparent',
              strokeWidth: 0,
              cornerRadius: 0,
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
        assets={{}}
        images={{}}
      />,
    );

    expect(container.querySelector('path')?.getAttribute('d')).toBe(
      'M 0 0 L 40 0 L 40 20 L 0 20 Z',
    );
  });
});

describe('padding on text and price badge layers (S3, AC-S3-1/2/3/4)', () => {
  it('adds no padding to a text layer when it is absent (AC-S3-3)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'text-1',
            type: 'text',
            props: {
              kind: 'text',
              text: 'Sale',
              fontFamily: 'DM Sans',
              fontSize: 10,
              fontWeight: 400,
              color: '#000',
              align: 'left',
              lineHeight: 1.2,
              letterSpacing: 0,
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('Sale')).toHaveStyle({
      padding: '0mm 0mm 0mm 0mm',
      boxSizing: 'border-box',
    });
  });

  it('prints the padding as one CSS shorthand, T R B L (AC-S3-1)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            id: 'text-1',
            type: 'text',
            props: {
              kind: 'text',
              text: 'Sale',
              fontFamily: 'DM Sans',
              fontSize: 10,
              fontWeight: 400,
              color: '#000',
              align: 'left',
              lineHeight: 1.2,
              letterSpacing: 0,
              padding: { top: 2, right: 4, bottom: 6, left: 8 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('Sale')).toHaveStyle({
      padding: '2mm 4mm 6mm 8mm',
      boxSizing: 'border-box',
    });
  });

  it('pads the badge text container the same way, and the callout shrinks with it (AC-S3-2)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            width_mm: 40,
            height_mm: 20,
            props: {
              ...defaultPriceBadgeProps('list_only'),
              showBox: true,
              cornerRadius: 0,
              padding: { top: 0, right: 0, bottom: 0, left: 10 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    // 40mm less the 10mm left pad leaves 30mm of drawable width, and the
    // callout - the badge is the box (r4b, AC-S6-2) - shrinks with it.
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 30 20');
    expect(svg?.querySelector('path')?.getAttribute('d')).toBe('M 0 0 L 30 0 L 30 20 L 0 20 Z');

    const figure = screen.getByText('RM 1,599');
    expect(figure.parentElement).toHaveStyle({
      padding: '0mm 0mm 0mm 10mm',
      boxSizing: 'border-box',
    });
  });

  it('positions the callout SVG at the padded inset, not the full unpadded box (r5 review)', () => {
    // An asymmetric pad on both axes - the bug this pins collapsed the SVG
    // back to the layer's full 40x20 box (`width`/`height: 100%` resolves
    // against the padding box of its `position: absolute` ancestor, not the
    // content box padding shrinks), so the viewBox alone could not catch it:
    // it still read the padded numbers while CSS drew the SVG somewhere else.
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            width_mm: 40,
            height_mm: 20,
            props: {
              ...defaultPriceBadgeProps('list_only'),
              showBox: true,
              cornerRadius: 0,
              padding: { top: 10, right: 0, bottom: 0, left: 10 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 30 10');
    expect(svg).toHaveStyle({
      left: '10mm',
      top: '10mm',
      width: '30mm',
      height: '10mm',
    });
  });

  it('pads the unboxed list-only badge the same way (AC-S3-2)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            props: {
              ...defaultPriceBadgeProps('list_only'),
              padding: { top: 2, right: 2, bottom: 2, left: 2 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('RM 1,599').parentElement).toHaveStyle({
      padding: '2mm 2mm 2mm 2mm',
      boxSizing: 'border-box',
    });
  });

  it('pads the promotional badge too, its filled box included (AC-S3-2)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            props: {
              ...defaultPriceBadgeProps('promo'),
              padding: { top: 1, right: 1, bottom: 1, left: 1 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    // amount span -> filled box div -> the padded text container.
    const content = screen.getByText('RM 599').parentElement?.parentElement;
    expect(content).toHaveStyle({ padding: '1mm 1mm 1mm 1mm', boxSizing: 'border-box' });
  });
});

// ---------------------------------------------------------------------------
// Price badge margin (S3b, AC-7/8/9)
// ---------------------------------------------------------------------------

describe('price badge margin on the print page (S3b, AC-7/8)', () => {
  it('insets the callout by margin and the figure further by padding, independently', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            width_mm: 40,
            height_mm: 20,
            props: {
              ...defaultPriceBadgeProps('list_only'),
              showBox: true,
              cornerRadius: 0,
              margin: { top: 2, right: 2, bottom: 2, left: 2 },
              padding: { top: 1, right: 1, bottom: 1, left: 1 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    // The callout sits 2mm in from the 40x20 layer box on every side.
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 36 16');
    expect(svg).toHaveStyle({ left: '2mm', top: '2mm', width: '36mm', height: '16mm' });

    // The figure sits a further 1mm in from the callout's own edge.
    const figure = screen.getByText('RM 1,599');
    expect(figure).toHaveStyle({ padding: '1mm 1mm 1mm 1mm', boxSizing: 'border-box' });
    expect(figure.parentElement).toHaveStyle({
      padding: '2mm 2mm 2mm 2mm',
      boxSizing: 'border-box',
    });
  });

  it('reads a legacy padding-only badge as margin, figure flush inside (AC-9)', () => {
    const { container } = render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            width_mm: 40,
            height_mm: 20,
            props: {
              ...defaultPriceBadgeProps('list_only'),
              showBox: true,
              cornerRadius: 0,
              padding: { top: 2, right: 2, bottom: 2, left: 2 },
            },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 36 16');

    const figure = screen.getByText('RM 1,599');
    // padding resolves to 0 under the legacy rule - the figure has no inset
    // of its own beyond the margin the callout already carries.
    expect(figure).toHaveStyle({ padding: '0mm 0mm 0mm 0mm', boxSizing: 'border-box' });
  });
});

// ---------------------------------------------------------------------------
// Price badge currency (S3c, AC-14/15)
// ---------------------------------------------------------------------------

describe('price badge currency on the print page (S3c, AC-14/15)', () => {
  it('keeps RM when the badge names no currency flag (AC-15)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({ type: 'price_badge', props: defaultPriceBadgeProps('list_only') }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('RM 1,599')).toBeInTheDocument();
  });

  it('drops RM when the badge switches currency off (AC-14)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            props: { ...defaultPriceBadgeProps('list_only'), showCurrency: false },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('1,599')).toBeInTheDocument();
    expect(screen.queryByText('RM 1,599')).not.toBeInTheDocument();
  });

  it('drops RM from both promo lines the same way (AC-14)', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'price_badge',
            props: { ...defaultPriceBadgeProps('promo'), showCurrency: false },
          }),
        ])}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    expect(screen.getByText('LP: 1,599')).toBeInTheDocument();
    expect(screen.getByText('599')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// D7 - a layer's subjectPart follows it onto the print path (S4/S12-4,
// AC-S4-3). Already wired in `renderProductSlotLayer`/`imageUrlFor` through
// `subjectOf` - this pins the PDF path stays in lockstep with the canvas.
// ---------------------------------------------------------------------------

describe('subjectPart on the print page (D7, AC-S4-3)', () => {
  it('a product_slot layer with subjectPart 0 renders the PART\'s own image and code, not the parent\'s', () => {
    render(
      <TagSheetRenderer
        doc={docWith([
          layer({
            type: 'product_slot',
            props: { kind: 'product_slot', fieldKey: 'product_image', subjectPart: 0 },
          }),
          layer({
            type: 'product_slot',
            slot_binding: 'code',
            props: { kind: 'product_slot', fieldKey: 'code', subjectPart: 0 },
          }),
        ])}
        resolvedData={{
          [TAG_ID]: resolved({
            parts: [
              {
                product_id: 'part-1',
                code: 'TAP-1',
                name: 'Kitchen Tap',
                dimensions: '120 x 45 x 300 mm',
                images: [
                  { attachment_id: 'att-part-1', url: 'https://cdn/part-1.jpg', is_primary: true },
                ],
              },
            ],
          }),
        }}
        images={{ 'att-part-1': 'https://signed.example/part-1.jpg' }}
      />,
    );

    // `alt=""` (the renderer's own choice, decorative) drops the "img" role
    // from the accessibility tree, so this queries the DOM directly rather
    // than through `getByRole`.
    const images = Array.from(document.querySelectorAll('img'));
    expect(images.map((img) => img.src)).toContain('https://signed.example/part-1.jpg');
    expect(screen.getByText('TAP-1')).toBeInTheDocument();
    // The parent's own code never prints on either layer.
    expect(screen.queryByText('SK-1234')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC-S7-8 (PLAN-price-tag-r10.md S7): a placed tag with `rotation: 90` draws
// its inner (natural, unrotated) box with `transform: translate(<height_mm>
// mm, 0) rotate(90deg)`; rotation 0 or absent draws no transform on it at
// all - the print page must agree with the Konva arrange preview's own
// "rotate then translate" (`ArrangeSheetView.tsx`'s `TagOnCanvas`). Already
// wired (`TagRenderer`'s own `naturalBox`), so these are GREEN regression
// guards - Phase 1 shipped the real renderer.
// ---------------------------------------------------------------------------

function docWithRotation(rotation: 0 | 90 | undefined, text: string): TagSheetDoc {
  return {
    kind: 'tag_sheet',
    imposition: {
      preset: 'auto',
      page_width_mm: 210,
      page_height_mm: 297,
      bleed_mm: 5,
      gap_mm: 0,
    },
    sheets: [
      {
        id: 's1',
        tags: [
          {
            id: 't1',
            template_id: 'tpl-sink',
            request_tag_id: TAG_ID,
            x_mm: 10,
            y_mm: 10,
            // Natural (unrotated) footprint - the tag's own layers are laid
            // out against 143.5 x 100, regardless of rotation.
            width_mm: 143.5,
            height_mm: 100,
            rotation,
            layers: [
              layer({
                type: 'text',
                props: { kind: 'text', text, align: 'left', color: '#000', fontSize: 10, fontFamily: 'Jost', fontWeight: 400, lineHeight: 1.2, letterSpacing: 0 },
              }),
            ],
          },
        ],
      },
    ],
  };
}

/** Climbs from a text node to the nearest ancestor carrying an inline
 *  `transform`/`transformOrigin` pair - `naturalBox`'s own div, whichever
 *  DOM depth the text landed at inside it. */
function transformAncestorOf(el: HTMLElement): HTMLElement {
  let node: HTMLElement | null = el;
  while (node && node.style.transformOrigin !== '0 0' && node.parentElement) {
    node = node.parentElement;
  }
  if (!node) throw new Error('no ancestor carries transformOrigin: 0 0');
  return node;
}

describe('TagSheetRenderer - placed tag rotation (AC-S7-8)', () => {
  it('rotation: 90 draws translate(<height_mm>mm, 0) rotate(90deg) on the natural box', () => {
    render(
      <TagSheetRenderer
        doc={docWithRotation(90, 'ZZT-ROT-90')}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const box = transformAncestorOf(screen.getByText('ZZT-ROT-90'));
    expect(box.style.transform).toBe('translate(100mm, 0) rotate(90deg)');
  });

  it('rotation: 0 draws no transform on the natural box', () => {
    render(
      <TagSheetRenderer
        doc={docWithRotation(0, 'ZZT-ROT-0')}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const textEl = screen.getByText('ZZT-ROT-0');
    // No rotation - nothing to climb to; the immediate naturalBox ancestor
    // (transformOrigin still set, but transform absent) is found the same
    // structural way and must carry no transform.
    let node: HTMLElement | null = textEl;
    while (node && node.style.width !== '143.5mm') node = node.parentElement;
    expect(node).not.toBeNull();
    expect((node as HTMLElement).style.transform).toBe('');
  });

  it('rotation absent (undefined) draws no transform either', () => {
    render(
      <TagSheetRenderer
        doc={docWithRotation(undefined, 'ZZT-ROT-ABSENT')}
        resolvedData={{ [TAG_ID]: resolved() }}
      />,
    );

    const textEl = screen.getByText('ZZT-ROT-ABSENT');
    let node: HTMLElement | null = textEl;
    while (node && node.style.width !== '143.5mm') node = node.parentElement;
    expect(node).not.toBeNull();
    expect((node as HTMLElement).style.transform).toBe('');
  });
});

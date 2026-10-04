/**
 * Product blocks, bindings and the two presets (AC-L.3, L.4, L.5).
 *
 * The builders are pure, so this is where the binding rules are pinned: a
 * dropped block carries slot bindings and no values, typing over a bound layer
 * unlinks it without losing the binding, and "Relink" is one field going back
 * to null.
 */

import { describe, expect, it } from 'vitest';

import {
  defaultTextProps,
  type LineTagData,
  type ProductSetTagData,
  type ProductTagData,
  type TagBindingData,
  type TagLayer,
  type TagPartData,
} from './tag-template-types';
import {
  bindTemplateLayers,
  buildAccessoriesStrip,
  buildAlternativesRow,
  buildProductBlock,
  buildSetBlock,
  formatSetMemberLine,
  imagesOf,
  isUnlinked,
  layerDisplay,
  layerText,
  ownPartsOf,
  priceBadgeInput,
  primaryImageOf,
  resolveBarcodeValue,
  resolveSlotText,
  slotImageAttachmentId,
  subjectOf,
} from './product-block';

let seq = 0;
const newId = () => `l${(seq += 1)}`;

function product(overrides: Partial<ProductTagData> = {}): ProductTagData {
  return {
    id: 'p1',
    code: 'SK-1234',
    name: 'Kitchen Sink',
    dimensions: '800 x 500 x 220 mm',
    spec_lines: ['Stainless steel', 'Overflow included'],
    specs: [],
    images: [
      { attachment_id: 'att-primary', url: 'https://cdn/1.jpg', is_primary: true },
      { attachment_id: 'att-other', url: 'https://cdn/2.jpg', is_primary: false },
    ],
    list_price: 1599,
    offer_price: null,
    promotion_id: null,
    barcode: null,
    ...overrides,
  };
}

function productSet(): ProductSetTagData {
  return {
    id: 's1',
    set_code: 'BF-SET-01',
    name: 'Bathroom Furniture Set',
    members: [
      {
        product_id: 'p1',
        code: 'CAB-01',
        name: 'Cabinet',
        dimensions: '800 x 460 x 550 mm',
        quantity: 1,
      },
      {
        product_id: 'p2',
        code: 'BAS-01',
        name: 'Basin',
        dimensions: '800 x 460 x 180 mm',
        quantity: 1,
      },
    ],
    list_price: 2400,
    offer_price: 1800,
    promotion_id: 'promo-1',
  };
}

const OPTS = { newId, x_mm: 10, y_mm: 20, z_index: 1 };

function findBySlot(layers: TagLayer[], slot: string) {
  return layers.find((layer) => layer.slot_binding === slot);
}

// ---------------------------------------------------------------------------
// Dropping a product (AC-L.3)
// ---------------------------------------------------------------------------

describe('buildProductBlock', () => {
  it('creates a group bound to the product', () => {
    const layers = buildProductBlock(product(), OPTS);
    const group = layers.find((layer) => layer.props.kind === 'group');

    expect(group).toBeDefined();
    expect(group!.props).toMatchObject({ kind: 'group', binding: { product_id: 'p1' } });
    expect((group!.props as { children: string[] }).children).toHaveLength(
      layers.length - 1,
    );
  });

  it('gives every child its slot binding', () => {
    const layers = buildProductBlock(product(), OPTS);
    const slots = layers.map((layer) => layer.slot_binding);

    expect(slots).toEqual(
      expect.arrayContaining([
        'product_image',
        'code',
        'name',
        'dimensions',
        'spec_lines',
      ]),
    );
  });

  it('leaves every text layer linked - no overrides on a fresh block', () => {
    const layers = buildProductBlock(product(), OPTS);

    expect(layers.every((layer) => layer.text_override === null)).toBe(true);
  });

  it('points the image layer at the primary photo', () => {
    const layers = buildProductBlock(product(), OPTS);
    const image = findBySlot(layers, 'product_image');

    expect(image!.props).toMatchObject({
      source: { type: 'product_attachment', attachmentId: 'att-primary' },
    });
  });

  it('drops a promo badge when the product is on offer, a list badge otherwise', () => {
    const plain = buildProductBlock(product(), OPTS);
    const offered = buildProductBlock(product({ offer_price: 599 }), OPTS);

    expect(
      plain.find((layer) => layer.props.kind === 'price_badge')!.props,
    ).toMatchObject({ variant: 'list_only' });
    expect(
      offered.find((layer) => layer.props.kind === 'price_badge')!.props,
    ).toMatchObject({ variant: 'promo' });
  });

  it('places the block where it was asked to', () => {
    const layers = buildProductBlock(product(), OPTS);

    expect(Math.min(...layers.map((layer) => layer.x_mm))).toBe(10);
    expect(Math.min(...layers.map((layer) => layer.y_mm))).toBe(20);
  });
});

// ---------------------------------------------------------------------------
// Unlink and relink (AC-L.3)
// ---------------------------------------------------------------------------

describe('unlink and relink', () => {
  const data = { kind: 'product' as const, product: product() };

  it('shows the resolved value while the layer is linked', () => {
    const layers = buildProductBlock(product(), OPTS);
    const name = findBySlot(layers, 'name')!;

    expect(layerText(name, data)).toBe('Kitchen Sink');
    expect(isUnlinked(name)).toBe(false);
  });

  it('shows the typed text once somebody types over it', () => {
    const layers = buildProductBlock(product(), OPTS);
    const name = { ...findBySlot(layers, 'name')!, text_override: 'Showroom Special' };

    expect(layerText(name, data)).toBe('Showroom Special');
    expect(isUnlinked(name)).toBe(true);
  });

  it('restores the resolved value when the override is cleared', () => {
    const layers = buildProductBlock(product(), OPTS);
    const overridden = {
      ...findBySlot(layers, 'name')!,
      text_override: 'Showroom Special',
    };
    const relinked = { ...overridden, text_override: null };

    expect(layerText(relinked, data)).toBe('Kitchen Sink');
    expect(isUnlinked(relinked)).toBe(false);
  });

  it('keeps the binding through the override, so a rebind still reaches it', () => {
    const layers = buildProductBlock(product(), OPTS);
    const overridden = { ...findBySlot(layers, 'name')!, text_override: 'Typed' };

    expect(overridden.slot_binding).toBe('name');
    expect(
      layerText({ ...overridden, text_override: null }, {
        kind: 'product',
        product: product({ name: 'Another Sink' }),
      }),
    ).toBe('Another Sink');
  });

  it('resolves spec lines as one text block', () => {
    const layers = buildProductBlock(product(), OPTS);

    expect(layerText(findBySlot(layers, 'spec_lines')!, data)).toBe(
      'Stainless steel\nOverflow included',
    );
  });
});

// ---------------------------------------------------------------------------
// Sets (AC-L.4)
// ---------------------------------------------------------------------------

describe('buildSetBlock', () => {
  it('formats one line per member', () => {
    expect(
      formatSetMemberLine({
        product_id: 'p1',
        code: 'CAB-01',
        name: 'Cabinet',
        dimensions: '800 x 460 x 550 mm',
        quantity: 1,
      }),
    ).toBe('- CAB-01 (Cabinet) 800 x 460 x 550 mm');
  });

  it('drops the parenthetical when a member name only repeats its code (S2, r5 review)', () => {
    expect(
      formatSetMemberLine({
        product_id: 'p1',
        code: 'CAB-01',
        name: 'cab-01',
        dimensions: '800 x 460 x 550 mm',
        quantity: 1,
      }),
    ).toBe('- CAB-01 800 x 460 x 550 mm');
  });

  it('binds the group to the set and fills the members slot', () => {
    const set = productSet();
    const layers = buildSetBlock(set, OPTS);
    const group = layers.find((layer) => layer.props.kind === 'group');

    expect(group!.props).toMatchObject({ binding: { product_set_id: 's1' } });
    expect(
      resolveSlotText(findBySlot(layers, 'set_members')!, { kind: 'set', set }),
    ).toBe(
      '- CAB-01 (Cabinet) 800 x 460 x 550 mm\n- BAS-01 (Basin) 800 x 460 x 180 mm',
    );
  });

  it('takes its prices off the set', () => {
    const set = productSet();

    expect(priceBadgeInput({ kind: 'set', set })).toEqual({
      listPrice: 2400,
      offerPrice: 1800,
    });
  });
});

// ---------------------------------------------------------------------------
// resolveSlotText - name equal to code is redundant (S2, AC-S2-2)
// ---------------------------------------------------------------------------

describe('resolveSlotText name slot (S2, AC-S2-2)', () => {
  const nameLayer = { slot_binding: 'name' as const };

  it('blanks the name for a product whose name is its code, case- and space-insensitive', () => {
    expect(
      resolveSlotText(nameLayer, { kind: 'product', product: product({ name: 'SK-1234' }) }),
    ).toBe('');
    expect(
      resolveSlotText(nameLayer, {
        kind: 'product',
        product: product({ code: 'SK-1234', name: ' sk-1234 ' }),
      }),
    ).toBe('');
  });

  it('keeps the name for a product whose name differs from its code (AC-S2-3)', () => {
    expect(
      resolveSlotText(nameLayer, { kind: 'product', product: product({ name: 'Kitchen Sink' }) }),
    ).toBe('Kitchen Sink');
  });

  it('blanks the name for a set whose name is its set code', () => {
    const set = { ...productSet(), set_code: 'BF-SET-01', name: 'bf-set-01' };
    expect(resolveSlotText(nameLayer, { kind: 'set', set })).toBe('');
  });

  it('keeps the name for a set whose name differs from its set code', () => {
    const set = productSet();
    expect(resolveSlotText(nameLayer, { kind: 'set', set })).toBe('Bathroom Furniture Set');
  });

  it('blanks the name for a line whose name is its code', () => {
    const line = {
      kind: 'line' as const,
      line: {
        tag_id: 't1',
        tag_label: '1a',
        open_groups: [],
        parts: [],
        line_id: 'l1',
        code: 'SK-1234',
        name: 'SK-1234',
        dimensions: '800 x 500 x 220 mm',
        spec_lines: '',
        specs: [],
        set_members: '',
        images: [],
        list_price: 1599,
        sell_price: null,
        show_promo_price: false,
        included_accessories: '',
        quantity: 1,
        barcode: null,
      },
    };
    expect(resolveSlotText(nameLayer, line)).toBe('');
  });
});

// ---------------------------------------------------------------------------
// resolveSlotText price slots print a bare figure, no `RM` (AC-A1, AC-A2).
// The badge's own `showCurrency` toggle is untouched - this is only the TEXT
// slot path, `price-badge.test.ts` covers the badge default staying `RM 760`.
// ---------------------------------------------------------------------------

describe('resolveSlotText price slots (AC-A1, AC-A2)', () => {
  it('AC-A1: sell_price slot renders the bare offer figure, no RM prefix', () => {
    const data = {
      kind: 'product' as const,
      product: product({ list_price: 1599, offer_price: 535 }),
    };
    expect(resolveSlotText({ slot_binding: 'sell_price' }, data)).toBe('535');
  });

  it('AC-A2: list_price slot renders the bare list figure, grouped, no RM prefix', () => {
    const data = { kind: 'product' as const, product: product({ list_price: 1599 }) };
    expect(resolveSlotText({ slot_binding: 'list_price' }, data)).toBe('1,599');
  });
});

// ---------------------------------------------------------------------------
// Presets (AC-L.5)
// ---------------------------------------------------------------------------

describe('buildAlternativesRow', () => {
  it('places N blocks with N-1 OR connectors and one leading plus', () => {
    const products = [
      product({ id: 'a', code: 'TAP-1' }),
      product({ id: 'b', code: 'TAP-2' }),
      product({ id: 'c', code: 'TAP-3' }),
    ];
    const layers = buildAlternativesRow(products, OPTS);

    const texts = layers
      .filter((layer) => layer.props.kind === 'text')
      .map((layer) => (layer.props as { text: string }).text);

    expect(texts.filter((text) => text === '+')).toHaveLength(1);
    expect(texts.filter((text) => text === 'OR')).toHaveLength(2);
    expect(layers.filter((layer) => layer.props.kind === 'image')).toHaveLength(3);
    expect(layers.filter((layer) => layer.props.kind === 'price_badge')).toHaveLength(
      3,
    );
  });

  it('needs no connector for a single alternative', () => {
    const layers = buildAlternativesRow([product()], OPTS);
    const texts = layers
      .filter((layer) => layer.props.kind === 'text')
      .map((layer) => (layer.props as { text: string }).text);

    expect(texts.filter((text) => text === 'OR')).toHaveLength(0);
    expect(texts.filter((text) => text === '+')).toHaveLength(1);
  });

  it('leaves the row as ordinary layers - nothing is bound to a group', () => {
    const layers = buildAlternativesRow([product(), product({ id: 'b' })], OPTS);

    expect(layers.some((layer) => layer.props.kind === 'group')).toBe(false);
  });

  it('blanks a duplicate name at build time instead of slot-binding it to the tag (r5 fix)', () => {
    const layers = buildAlternativesRow(
      [
        product({ id: 'a', code: 'TAP-1', name: 'Tap One' }),
        product({ id: 'b', code: 'TAP-2', name: 'tap-2' }),
      ],
      OPTS,
    );

    const textValues = layers
      .filter((layer) => layer.props.kind === 'text')
      .map((layer) => (layer.props as { text: string }).text);

    expect(textValues).toContain('TAP-1');
    expect(textValues).toContain('Tap One');
    expect(textValues).toContain('TAP-2');
    // name === code (case-insensitive) blanks to '' rather than repeating the code
    expect(textValues.filter((text) => text === '')).toHaveLength(1);

    // neither layer is bound to a slot - an ungrouped slot resolves against the
    // WHOLE tag's binding, so a slot here would make every alternative print
    // the tag's own product instead of its own (D28/r5 review)
    expect(layers.every((layer) => layer.slot_binding === null)).toBe(true);
  });
});

describe('buildAccessoriesStrip', () => {
  it('titles the strip and gives every item a picture and a caption', () => {
    const layers = buildAccessoriesStrip(
      [
        { caption: 'WASTE-1', source: { type: 'asset', assetId: 'a1' } },
        {
          caption: 'TRAP-1',
          source: { type: 'product_attachment', attachmentId: 'att-1' },
        },
      ],
      OPTS,
    );

    const texts = layers
      .filter((layer) => layer.props.kind === 'text')
      .map((layer) => (layer.props as { text: string }).text);

    expect(texts[0]).toBe('Accessories Included');
    expect(texts).toContain('WASTE-1');
    expect(texts).toContain('TRAP-1');
    expect(layers.filter((layer) => layer.props.kind === 'image')).toHaveLength(2);
  });

  it('takes a title of its own', () => {
    const layers = buildAccessoriesStrip([], { ...OPTS, title: 'Free Gifts' });

    expect((layers[0].props as { text: string }).text).toBe('Free Gifts');
  });
});

// ---------------------------------------------------------------------------
// A product-photo slot follows the bound product (D42, AC-M.11)
// ---------------------------------------------------------------------------

function imageLayer(overrides: Partial<TagLayer> = {}): TagLayer {
  return {
    id: 'img',
    type: 'image',
    x_mm: 0,
    y_mm: 0,
    width_mm: 38,
    height_mm: 38,
    rotation_deg: 0,
    z_index: 1,
    locked: false,
    visible: true,
    slot_binding: 'product_image',
    text_override: null,
    props: { kind: 'image', source: null, fit: 'contain', maskShape: 'none' },
    ...overrides,
  } as TagLayer;
}

function slotLayer(fieldKey: string): TagLayer {
  return {
    ...imageLayer(),
    id: 'slot',
    type: 'product_slot',
    slot_binding: null,
    props: { kind: 'product_slot', fieldKey },
  } as TagLayer;
}

function barcodeLayer(overrides: Partial<TagLayer> = {}): TagLayer {
  return {
    ...imageLayer(),
    id: 'bc',
    type: 'barcode',
    slot_binding: 'barcode',
    props: { kind: 'barcode', show_code: true },
    ...overrides,
  } as TagLayer;
}

const IMAGES = product().images;

describe('primaryImageOf', () => {
  it('takes the primary photo, or the first when none is marked', () => {
    expect(primaryImageOf(IMAGES)?.attachment_id).toBe('att-primary');
    expect(
      primaryImageOf([{ attachment_id: 'att-other', url: 'u', is_primary: false }])
        ?.attachment_id,
    ).toBe('att-other');
    expect(primaryImageOf([])).toBeUndefined();
  });
});

describe('slotImageAttachmentId', () => {
  it('lets the designer pinned photo win', () => {
    const layer = imageLayer({
      props: {
        kind: 'image',
        source: { type: 'product_attachment', attachmentId: 'att-other' },
        fit: 'contain',
      },
    });

    expect(slotImageAttachmentId(layer, IMAGES)).toBe('att-other');
  });

  it('follows the primary photo when the template pinned nothing', () => {
    expect(slotImageAttachmentId(imageLayer(), IMAGES)).toBe('att-primary');
  });

  it('falls back to the primary when the pinned photo is another product\'s', () => {
    const layer = imageLayer({
      props: {
        kind: 'image',
        source: { type: 'product_attachment', attachmentId: 'att-gone' },
        fit: 'contain',
      },
    });

    expect(slotImageAttachmentId(layer, IMAGES)).toBe('att-primary');
  });

  it('leaves a decorative image layer empty', () => {
    expect(slotImageAttachmentId(imageLayer({ slot_binding: null }), IMAGES)).toBeNull();
  });

  it('leaves an asset source to the asset map', () => {
    const layer = imageLayer({
      props: { kind: 'image', source: { type: 'asset', assetId: 'a1' }, fit: 'contain' },
    });

    expect(slotImageAttachmentId(layer, IMAGES)).toBeNull();
  });

  it('answers for a product_slot layer that holds the photo', () => {
    expect(slotImageAttachmentId(slotLayer('product_image'), IMAGES)).toBe('att-primary');
    expect(slotImageAttachmentId(slotLayer('code'), IMAGES)).toBeNull();
  });
});

describe('layerDisplay', () => {
  const data = { kind: 'product' as const, product: product() };

  it('draws the primary photo for a slot-bound image layer with no source', () => {
    expect(layerDisplay(imageLayer(), data, {})).toEqual({
      imageUrl: 'https://cdn/1.jpg',
    });
  });

  it('draws the pinned photo when the designer chose one', () => {
    const layer = imageLayer({
      props: {
        kind: 'image',
        source: { type: 'product_attachment', attachmentId: 'att-other' },
        fit: 'contain',
      },
    });

    expect(layerDisplay(layer, data, {})).toEqual({ imageUrl: 'https://cdn/2.jpg' });
  });

  it('shows nothing while no product is bound', () => {
    expect(layerDisplay(imageLayer(), null, {})).toEqual({ imageUrl: null });
  });

  it('shows nothing for an image layer bound to no slot', () => {
    expect(layerDisplay(imageLayer({ slot_binding: null }), data, {})).toEqual({
      imageUrl: null,
    });
  });

  it('resolves a line the same way a product resolves', () => {
    const line = {
      kind: 'line' as const,
      line: {
        tag_id: 't1',
        tag_label: '1a',
        open_groups: [],
        parts: [],
        line_id: 'l1',
        code: 'SK-1234',
        name: 'Kitchen Sink',
        dimensions: '800 x 500 x 220 mm',
        spec_lines: 'Stainless steel',
        specs: [],
        set_members: '',
        images: IMAGES,
        list_price: 1599,
        sell_price: 599,
        show_promo_price: true,
        included_accessories: '',
        quantity: 1,
        barcode: null,
      },
    };

    expect(layerDisplay(imageLayer(), line, {})).toEqual({
      imageUrl: 'https://cdn/1.jpg',
    });
  });

  it('draws the photo for a product_slot layer, and the text for its other keys', () => {
    expect(layerDisplay(slotLayer('product_image'), data, {})).toEqual({
      imageUrl: 'https://cdn/1.jpg',
    });
    expect(layerDisplay(slotLayer('code'), data, {})).toEqual({ text: 'SK-1234' });
    expect(layerDisplay(slotLayer('dimensions'), data, {})).toEqual({
      text: '800 x 500 x 220 mm',
    });
  });

  it('leaves a product_slot layer on its placeholder while nothing is bound', () => {
    expect(layerDisplay(slotLayer('code'), null, {})).toBeUndefined();
    expect(layerDisplay(slotLayer('product_image'), null, {})).toEqual({
      imageUrl: null,
    });
  });

  // -------------------------------------------------------------------------
  // Barcode value override (D23, S9) - the text layer's text_override pattern
  // -------------------------------------------------------------------------

  it('draws the bound product barcode when no override is set', () => {
    const barcodeData = { kind: 'product' as const, product: product({ barcode: '4006381333931' }) };
    expect(layerDisplay(barcodeLayer(), barcodeData, {})).toEqual({
      text: '4006381333931',
      code: 'SK-1234',
    });
  });

  it('draws the override instead of the bound barcode when both exist', () => {
    const barcodeData = { kind: 'product' as const, product: product({ barcode: '4006381333931' }) };
    const layer = barcodeLayer({ text_override: '110000526' });
    expect(layerDisplay(layer, barcodeData, {})).toEqual({
      text: '110000526',
      code: 'SK-1234',
    });
  });

  it('draws the override even while no product is bound', () => {
    const layer = barcodeLayer({ text_override: '110000526' });
    expect(layerDisplay(layer, null, {})).toEqual({
      text: '110000526',
      code: null,
    });
  });
});

// ---------------------------------------------------------------------------
// resolveBarcodeValue (D23, S9): the ONE resolver the Konva editor and the
// print page both call, so an override cannot draw differently in the two
// places - override wins, then the bound product/line's barcode, else null.
// ---------------------------------------------------------------------------

describe('resolveBarcodeValue', () => {
  const barcodeData = { kind: 'product' as const, product: product({ barcode: '4006381333931' }) };

  it('resolves the bound product barcode when unoverridden', () => {
    expect(resolveBarcodeValue(barcodeLayer(), barcodeData)).toBe('4006381333931');
  });

  it('prefers the override over the bound barcode', () => {
    expect(
      resolveBarcodeValue(barcodeLayer({ text_override: '110000526' }), barcodeData),
    ).toBe('110000526');
  });

  it('returns the override even while unbound', () => {
    expect(resolveBarcodeValue(barcodeLayer({ text_override: '110000526' }), null)).toBe(
      '110000526',
    );
  });

  it('returns null with no override and nothing bound', () => {
    expect(resolveBarcodeValue(barcodeLayer(), null)).toBeNull();
  });

  it('clearing the override (Relink) falls back to the bound barcode again', () => {
    const overridden = barcodeLayer({ text_override: '110000526' });
    const relinked = { ...overridden, text_override: null };
    expect(resolveBarcodeValue(relinked, barcodeData)).toBe('4006381333931');
  });
});

// ---------------------------------------------------------------------------
// bindTemplateLayers clears a template-level barcode override on clone
// (S9 review S5): a template author may type an override into the draft
// canvas to preview a symbology (D23), but that string must not survive
// into a request line's clone and print on every product the line's tag
// ever binds to - same guard class as rebindImageLayers clearing a stale
// attachment id above.
// ---------------------------------------------------------------------------

describe('bindTemplateLayers clears a barcode override on clone (S9 review S5)', () => {
  it('drops a barcode layer\'s text_override when binding the clone to a line', () => {
    const layers = [barcodeLayer({ text_override: '110000526' })];
    const bound = bindTemplateLayers(layers, { product_id: 'p1' });

    expect(bound[0].text_override).toBeNull();
  });

  it('leaves an unoverridden barcode layer alone', () => {
    const layers = [barcodeLayer()];
    const bound = bindTemplateLayers(layers, { product_id: 'p1' });

    expect(bound[0].text_override).toBeNull();
    expect(bound[0]).toBe(layers[0]);
  });

  it('still writes the group binding, unaffected by the barcode guard', () => {
    const group = {
      id: 'g1',
      type: 'group' as const,
      x_mm: 0,
      y_mm: 0,
      width_mm: 10,
      height_mm: 10,
      rotation_deg: 0,
      z_index: 1,
      locked: false,
      visible: true,
      slot_binding: null,
      text_override: null,
      props: { kind: 'group' as const, children: [], binding: undefined },
    };
    const bound = bindTemplateLayers([group], { product_id: 'p1' });

    expect(bound[0].props).toMatchObject({ binding: { product_id: 'p1' } });
  });

  it('a template-level override does not survive into a fresh clone (end-to-end guard)', () => {
    // The scenario the review flagged: a designer types an override into
    // the TEMPLATE draft to see it render, saves, and every future request
    // line that clones this template must start unoverridden.
    const overriddenTemplateLayers = [barcodeLayer({ text_override: '4006381333931' })];
    const clone = bindTemplateLayers(structuredClone(overriddenTemplateLayers), {
      product_id: 'p2',
    });
    expect(clone[0].text_override).toBeNull();
    // The template's own layers (what the editor still shows) are untouched -
    // bindTemplateLayers is only ever called on a COPY (tagForTag clones
    // via structuredClone before calling it).
    expect(overriddenTemplateLayers[0].text_override).toBe('4006381333931');
  });
});

// ---------------------------------------------------------------------------
// D7 - subjectOf, the combo subject picker (S4/S12-3, AC-S4-3/S4-4/S4-6)
// ---------------------------------------------------------------------------

function part(overrides: Partial<TagPartData> = {}): TagPartData {
  return {
    product_id: 'part-1',
    code: 'TAP-1',
    name: 'Kitchen Tap',
    dimensions: '120 x 45 x 300 mm',
    spec_lines: ['Chrome finish'],
    specs: [],
    images: [{ attachment_id: 'att-part-1', url: 'https://cdn/part-1.jpg', is_primary: true }],
    barcode: '4009999999991',
    list_price: 99,
    sell_price: null,
    ...overrides,
  };
}

function line(overrides: Partial<LineTagData> = {}): LineTagData {
  return {
    tag_id: 'tag-1',
    line_id: 'line-1',
    tag_label: '1a',
    open_groups: [],
    parts: [part()],
    code: 'CAB-01',
    name: 'Cabinet',
    dimensions: '800 x 500 x 220 mm',
    spec_lines: 'Stainless steel',
    specs: [],
    set_members: '',
    images: [{ attachment_id: 'att-parent', url: 'https://cdn/parent.jpg', is_primary: true }],
    list_price: 1698,
    sell_price: null,
    show_promo_price: false,
    included_accessories: '',
    quantity: 1,
    barcode: '4008888888882',
    ...overrides,
  };
}

function lineData(overrides: Partial<LineTagData> = {}): TagBindingData {
  return { kind: 'line', line: line(overrides) };
}

describe('subjectOf (D7, AC-S4-3/S4-4/S4-6)', () => {
  it('a layer with no subjectPart reads the parent - an existing design renders unchanged', () => {
    const data = lineData();
    const textLayer = { props: { ...defaultTextProps(), subjectPart: undefined } };

    expect(resolveSlotText({ slot_binding: 'code', ...textLayer }, data)).toBe('CAB-01');
    expect(
      resolveBarcodeValue({ text_override: null, props: textLayer.props }, data),
    ).toBe('4008888888882');
  });

  it('subjectPart: n resolves that PART, for text, image and barcode alike', () => {
    const data = lineData({
      parts: [part({ code: 'TAP-1', barcode: '4009999999991' }), part({ product_id: 'part-2', code: 'BASIN-1', barcode: '4009999999992', images: [{ attachment_id: 'att-part-2', url: 'https://cdn/part-2.jpg', is_primary: true }] })],
    });
    const textLayer = { slot_binding: 'code' as const, props: { ...defaultTextProps(), subjectPart: 1 } };
    const barcodeLayerRef = { text_override: null, props: { kind: 'barcode' as const, show_code: true, subjectPart: 1 } };
    const imageLayer = {
      slot_binding: 'product_image' as const,
      props: { kind: 'product_slot' as const, fieldKey: 'product_image', subjectPart: 1 },
    };

    expect(resolveSlotText(textLayer, data)).toBe('BASIN-1');
    expect(resolveBarcodeValue(barcodeLayerRef, data)).toBe('4009999999992');
    expect(
      slotImageAttachmentId(imageLayer, imagesOf(subjectOf(data, imageLayer))),
    ).toBe('att-part-2');
  });

  it('a price badge set to a part shows that part\'s own list price and offer', () => {
    const data = lineData({ parts: [part({ list_price: 99, sell_price: 79 })] });
    const badgeLayer = {
      props: { kind: 'price_badge' as const, subjectPart: 0 } as never,
    };

    expect(priceBadgeInput(data, badgeLayer)).toEqual({ listPrice: 99, offerPrice: 79 });
  });

  it('a price badge with NO subjectPart reads Tag total - the roll-up the badge always printed', () => {
    const data = lineData({ list_price: 1698, sell_price: 1500, show_promo_price: true });
    const badgeLayer = { props: { kind: 'price_badge' as const } as never };

    expect(priceBadgeInput(data, badgeLayer)).toEqual({ listPrice: 1698, offerPrice: 1500 });
  });

  it('a price badge set to the parent (-1) shows the parent alone, NOT the roll-up', () => {
    // AC-S4-4: "-1 is the parent's own price alone" - distinct from Tag
    // total once a combo actually has priced parts. The parent's OWN price
    // (599) is less than the combo's summed total (698 = 599 + 99 tap), so a
    // badge reading -1 must show 599, never 698.
    const data = lineData({
      list_price: 698,
      sell_price: null,
      parts: [part({ list_price: 99, sell_price: null })],
    });
    const badgeLayer = { props: { kind: 'price_badge' as const, subjectPart: -1 } as never };

    expect(priceBadgeInput(data, badgeLayer).listPrice).toBe(599);
  });

  it('an out-of-range subjectPart falls back to the parent, same as -1 (AC-S4-6)', () => {
    const data = lineData({ parts: [part()] });
    const textLayer = { slot_binding: 'code' as const, props: { ...defaultTextProps(), subjectPart: 7 } };

    expect(resolveSlotText(textLayer, data)).toBe('CAB-01');
  });

  it('a part missing D7\'s optional fields (a Phase 1 part) fails soft - no photo, no price, never the parent\'s', () => {
    const bareLine = lineData({
      parts: [
        {
          product_id: 'part-1',
          code: 'TAP-1',
          name: 'Kitchen Tap',
          dimensions: '120 x 45 x 300 mm',
        },
      ],
    });
    const imageLayer = {
      slot_binding: 'product_image' as const,
      props: { kind: 'product_slot' as const, fieldKey: 'product_image', subjectPart: 0 },
    };
    const badgeLayer = { props: { kind: 'price_badge' as const, subjectPart: 0 } as never };

    expect(
      slotImageAttachmentId(imageLayer, imagesOf(subjectOf(bareLine, imageLayer))),
    ).toBeNull();
    expect(priceBadgeInput(bareLine, badgeLayer)).toEqual({ listPrice: null, offerPrice: null });
  });

  it('a single-product tag (no parts) has nothing for subjectOf to change', () => {
    const data: TagBindingData = { kind: 'product', product: product() };
    const layer = { props: { ...defaultTextProps(), subjectPart: 0 } };

    expect(subjectOf(data, layer)).toBe(data);
  });

  // -------------------------------------------------------------------------
  // F6 (Reviewer S8): parentAlonePrice's subtraction fallback mistreats a
  // part with NO offer (sell_price null, "at list") as contributing ZERO to
  // the roll-up, instead of the list price it actually contributed
  // (AC-S1-8/S7-3: an uncovered part prints at list, which means it IS in
  // the roll-up total at its list price - subtracting `?? 0` for it
  // therefore under-subtracts).
  // -------------------------------------------------------------------------
  it("a price badge set to the parent, with no parent_sell_price pinned and a part at list, shows the parent's own offer (roll-up minus what the part actually contributed), not the parent's list price", () => {
    // Parent's own real figures: list 900, offer 700 (covered by the line's
    // promotion). The part (list 200) is NOT covered, so it contributes its
    // OWN LIST PRICE (200) to the roll-up, not zero.
    // Roll-up sell_price (line.sell_price, the tag total) = 700 + 200 = 900.
    // Roll-up list_price (line.list_price) = 900 + 200 = 1100.
    const data = lineData({
      list_price: 1100,
      sell_price: 900,
      show_promo_price: true,
      // Old-pin shape: no `parent_sell_price` carried, forcing the
      // subtraction fallback.
      parent_sell_price: undefined,
      parts: [part({ list_price: 200, sell_price: null })],
    });
    const badgeLayer = { props: { kind: 'price_badge' as const, subjectPart: -1 } as never };

    // Correct: 900 (roll-up) - 200 (the part's actual, list-price
    // contribution) = 700, the parent's real offer.
    // Today's subtraction does 900 - (null ?? 0) = 900, which is the
    // parent's OWN LIST PRICE by coincidence of this construction - the bug
    // this test is named for.
    expect(priceBadgeInput(data, badgeLayer).offerPrice).toBe(700);
  });
});

// ---------------------------------------------------------------------------
// AC-S6-3/AC-S6-11 (PLAN-price-tag-r10.md S6): `ownPartsOf` reads `own_parts`
// when the row carries it, else falls back to `parts` (a row pinned before
// r10, where `parts` was already the narrow list).
// ---------------------------------------------------------------------------

describe('ownPartsOf', () => {
  function part(code: string): TagPartData {
    return {
      product_id: `p-${code}`,
      code,
      name: code,
      dimensions: '',
      spec_lines: [],
      specs: [],
      images: [],
      barcode: null,
      list_price: 100,
      sell_price: null,
      currency: 'MYR',
    };
  }

  it('AC-S6-3: reads own_parts when present, not the wider parts list', () => {
    const line = {
      parts: [part('A'), part('B'), part('C')],
      own_parts: [part('A')],
    };
    expect(ownPartsOf(line).map((p) => p.code)).toEqual(['A']);
  });

  it('AC-S6-11: falls back to parts when own_parts is absent (a pre-r10 pinned row)', () => {
    const line = { parts: [part('A'), part('B')] } as {
      parts: TagPartData[];
      own_parts?: TagPartData[];
    };
    expect(ownPartsOf(line).map((p) => p.code)).toEqual(['A', 'B']);
  });
});

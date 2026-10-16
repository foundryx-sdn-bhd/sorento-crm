'use client';

/**
 * Right sidebar property inspector for the selected layer.
 *
 * Common props: position (x/y mm), size (w/h mm), rotation, locked, visible.
 * Type-specific sections render below based on `layer.props.kind`.
 */

import { useCallback, useId, useMemo } from 'react';
import {
  Bold,
  Braces,
  Copy,
  Eye,
  Italic,
  LayoutTemplate,
  Link2,
  Link2Off,
  Strikethrough,
  Underline,
  X,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { LiveNumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import type {
  ImageFit,
  ImageMaskShape,
  LayerPadding,
  PriceBadgeVariant,
  ShapeType,
  SlotBinding,
  TagLayer,
  TagLayerProps,
  TagPartData,
} from '@/lib/dealer-kit/tag-template-types';
import { imageSourceOf } from '@/lib/dealer-kit/tag-template-types';
import { isCropped } from '@/lib/dealer-kit/image-crop';
import { defaultPolygonPoints } from '@/lib/dealer-kit/polygon-path';
import { priceBadgeInsets, priceBadgeTypography } from '@/lib/dealer-kit/price-badge';
import { isDynamic } from '@/lib/dealer-kit/product-block';
import { hasSubjectAwareToken } from '@/lib/dealer-kit/merge-fields';
import { tagColours } from '@/lib/dealer-kit/colour';
import { ColorPicker } from './ColorPicker';

// ---------------------------------------------------------------------------
// Slot binding options
// ---------------------------------------------------------------------------

const SLOT_BINDING_OPTIONS = [
  { value: '__none__', label: '(None)' },
  { value: 'product_image', label: 'Product Image' },
  { value: 'code', label: 'Code' },
  { value: 'name', label: 'Name' },
  { value: 'dimensions', label: 'Dimensions' },
  { value: 'spec_lines', label: 'Spec Lines' },
  { value: 'included_accessories', label: 'Accessories' },
  { value: 'list_price', label: 'List Price' },
  { value: 'sell_price', label: 'Sell Price' },
  { value: 'badges', label: 'Badges' },
  { value: 'alternatives', label: 'Alternatives' },
  { value: 'set_members', label: 'Set Members' },
  { value: 'barcode', label: 'Barcode' },
];

/**
 * The fonts every install has. Brand fonts uploaded as `Asset.kind='font'` are
 * appended to this list by the editor, so the inspector offers both without the
 * two lists having to know about each other.
 *
 * `Bebas Neue` and `Jost` are the seeded templates' stand-ins for the flyer's
 * licensed Century Gothic and Myriad Pro (D32). They are here because a select
 * that cannot display its own current value reads as a bug: without them the
 * font box on every seeded text layer would show blank, and picking anything
 * would silently restyle the tag. They are loaded by `TAG_FONT_STYLESHEET`.
 */
export const STATIC_FONT_OPTIONS: SearchableSelectOption[] = [
  { value: 'DM Sans', label: 'DM Sans' },
  { value: 'Inter', label: 'Inter' },
  { value: 'Bebas Neue', label: 'Bebas Neue' },
  { value: 'Jost', label: 'Jost' },
  { value: 'Arial', label: 'Arial' },
  { value: 'Georgia', label: 'Georgia' },
  { value: 'Times New Roman', label: 'Times New Roman' },
];

const FONT_WEIGHT_OPTIONS = [
  { value: '400', label: '400 Regular' },
  { value: '500', label: '500 Medium' },
  { value: '600', label: '600 Semi-bold' },
  { value: '700', label: '700 Bold' },
  { value: '800', label: '800 Extra-bold' },
  { value: '900', label: '900 Black' },
];

const SHAPE_TYPE_OPTIONS = [
  { value: 'rect', label: 'Rectangle' },
  { value: 'rounded_rect', label: 'Rounded Rect' },
  { value: 'ellipse', label: 'Ellipse' },
  { value: 'line', label: 'Line' },
  { value: 'polygon', label: 'Polygon' },
];

const FIELD_KEY_OPTIONS = [
  { value: 'product_image', label: 'Product Image' },
  { value: 'code', label: 'Code' },
  { value: 'name', label: 'Name' },
  { value: 'dimensions', label: 'Dimensions' },
  { value: 'spec_lines', label: 'Spec Lines' },
];

const IMAGE_FIT_OPTIONS = [
  { value: 'cover', label: 'Cover' },
  { value: 'contain', label: 'Contain' },
  { value: 'stretch', label: 'Stretch' },
];

const MASK_SHAPE_OPTIONS = [
  { value: 'none', label: 'None' },
  { value: 'circle', label: 'Circle' },
];

const PRICE_BADGE_VARIANT_OPTIONS = [
  { value: 'list_only', label: 'List price only' },
  { value: 'promo', label: 'Promotion (LP struck + SP)' },
];

// ---------------------------------------------------------------------------
// Number input helper
// ---------------------------------------------------------------------------

function NumberInput({
  label,
  value,
  onChange,
  step = 1,
  min,
  max,
}: {
  label: string;
  /** Null draws an EMPTY box: the layer has not named a value for this. */
  value: number | null;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  max?: number;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </Label>
      <LiveNumberInput
        id={id}
        className="h-7 px-2 text-xs"
        value={value}
        step={step}
        min={min}
        max={max}
        rules={{ min, max }}
        onCommit={onChange}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface InspectorPanelProps {
  layer: TagLayer | null;
  onUpdate: (id: string, changes: Partial<TagLayer>) => void;
  onUpdateProps: (id: string, changes: Partial<TagLayerProps>) => void;
  /** The whole tag, so the colour picker can derive "This tag" (S3, AC-S3-5). */
  layers?: TagLayer[];
  /** What this layer's slot currently resolves to, for the unlink/relink pair. */
  resolvedText?: string | null;
  /** Human-readable name of what the selected group is bound to. No UUIDs. */
  bindingLabel?: string | null;
  /** Static Google fonts plus the company's uploaded brand fonts. */
  fontOptions?: SearchableSelectOption[];
  /** Open the picture picker for an image layer. */
  onChooseImage?: (layerId: string) => void;
  /** Open the asset picker for a badge layer. */
  onChooseBadge?: (layerId: string) => void;
  /** Add a brand font without leaving the canvas. */
  onUploadFont?: () => void;
  /** Open the merge-field catalogue for this text layer (D59). */
  onInsertField?: () => void;
  /** Point this group's block at a different product or set. */
  onRebind?: (groupId: string) => void;
  /** Clear every text override inside this group. */
  onRelinkGroup?: (groupId: string) => void;
  /** Re-clone this whole tag from another template (D51, the request designer). */
  onUseTemplate?: () => void;
  /** The previewable block the selection sits in, if any (D53). */
  previewBlockId?: string | null;
  /** What that block is previewing with, `CODE - name`, or null. */
  previewBlockLabel?: string | null;
  onPreviewBlock?: (groupId: string) => void;
  onClearBlockPreview?: (groupId: string) => void;
  /** Whether this layer is currently in edit-points mode (S5). */
  editingPoints?: boolean;
  /** Toggle edit-points mode for this layer (S5); a polygon or a boxed
   * list-only price badge only. */
  onToggleEditPoints?: (layerId: string) => void;
  /**
   * D7 (AC-S4-1/S4-2): the combo tag's own parts, in package order - the
   * Product picker's option list. Absent or empty means a single-product
   * tag, so no picker shows on ANY layer.
   */
  subjectParts?: TagPartData[];
  /** The parent's own code, for the picker's first entry. */
  subjectParentCode?: string | null;
}

export function InspectorPanel({
  layer,
  onUpdate,
  onUpdateProps,
  layers,
  resolvedText,
  bindingLabel,
  fontOptions,
  onUploadFont,
  onInsertField,
  onChooseImage,
  onChooseBadge,
  onRebind,
  onRelinkGroup,
  onUseTemplate,
  previewBlockId,
  previewBlockLabel,
  onPreviewBlock,
  onClearBlockPreview,
  editingPoints,
  onToggleEditPoints,
  subjectParts,
  subjectParentCode,
}: InspectorPanelProps) {
  const usedColours = useMemo(() => tagColours(layers ?? []), [layers]);

  const update = useCallback(
    (changes: Partial<TagLayer>) => {
      if (layer) onUpdate(layer.id, changes);
    },
    [layer, onUpdate],
  );

  const updateProps = useCallback(
    (changes: Partial<TagLayerProps>) => {
      if (layer) onUpdateProps(layer.id, changes);
    },
    [layer, onUpdateProps],
  );

  if (!layer) {
    return (
      <div className="flex h-full flex-col border-l">
        <div className="flex h-10 shrink-0 items-center border-b px-3">
          <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Inspector
          </span>
        </div>
        <div className="flex flex-1 items-center justify-center">
          <p className="text-xs text-muted-foreground">Select a layer</p>
        </div>
      </div>
    );
  }

  // D7/AC-S4-1/S4-2: the Product picker shows on any layer that reads
  // product data - a combo tag only (single-product tags have no parts to
  // point at, so `subjectParts` arrives empty and the section never renders).
  //
  // Browser finding: the designer's own photo slots are `image` layers
  // carrying `slot_binding: 'product_image'`, not `product_slot` - the
  // Product select never appeared on the image slot the walk selected. A
  // text layer can carry a slot_binding too (`code`, `name`, ...), same
  // gap. Keyed on "does this layer read product data" generally now:
  // the four kinds that always do, a text layer with a `{{product.*}}` /
  // `{{spec.*}}` token, OR ANY layer whose own `slot_binding` names a
  // product field.
  //
  // Review round 2: `slot_binding != null` on its own let a SHAPE, a plain
  // `badge` or a `group` offer the picker - none of those three carry
  // `subjectPart` in `tag-template-types.ts`, so the pick was written onto
  // props no resolver ever reads and the layer drew the parent regardless.
  // Both halves have to hold: a kind that can carry the subject, AND a
  // layer that actually reads product data.
  const carriesSubjectPart =
    layer.props.kind === 'product_slot' ||
    layer.props.kind === 'price_badge' ||
    layer.props.kind === 'barcode' ||
    layer.props.kind === 'text' ||
    layer.props.kind === 'image';
  const readsProductData =
    layer.props.kind === 'product_slot' ||
    layer.props.kind === 'price_badge' ||
    layer.props.kind === 'barcode' ||
    (layer.props.kind === 'text' &&
      hasSubjectAwareToken(layer.text_override ?? layer.props.text)) ||
    layer.slot_binding != null;
  const isSubjectAwareKind = carriesSubjectPart && readsProductData;
  const showSubjectPicker = isSubjectAwareKind && (subjectParts?.length ?? 0) > 0;

  // The same eligibility r4b's `cornerHandleLayer` already checks on the
  // canvas side (S5): a polygon shape, or a price badge whose box IS its
  // own shape (a plain rectangle badge has no corners of its own to edit).
  const canEditPoints =
    (layer.props.kind === 'shape' && layer.props.shape === 'polygon') ||
    (layer.props.kind === 'price_badge' &&
      layer.props.variant === 'list_only' &&
      layer.props.showBox === true);

  return (
    <div className="flex h-full flex-col border-l">
      <div className="flex h-10 shrink-0 items-center border-b px-3">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Inspector
        </span>
      </div>
      <ScrollArea className="flex-1">
        <div className="flex flex-col gap-4 p-3">
          {/* -- Position & size -- */}
          <section>
            <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Transform
            </h4>
            <div className="grid grid-cols-2 gap-2">
              <NumberInput
                label="X (mm)"
                value={Math.round(layer.x_mm * 100) / 100}
                onChange={(v) => update({ x_mm: v })}
                step={0.25}
              />
              <NumberInput
                label="Y (mm)"
                value={Math.round(layer.y_mm * 100) / 100}
                onChange={(v) => update({ y_mm: v })}
                step={0.25}
              />
              <NumberInput
                label="W (mm)"
                value={Math.round(layer.width_mm * 100) / 100}
                onChange={(v) => update({ width_mm: v })}
                step={0.25}
                min={1}
              />
              <NumberInput
                label="H (mm)"
                value={Math.round(layer.height_mm * 100) / 100}
                onChange={(v) => update({ height_mm: v })}
                step={0.25}
                min={1}
              />
              <NumberInput
                label="Rotation"
                value={layer.rotation_deg}
                onChange={(v) => update({ rotation_deg: v })}
                step={1}
              />
              <NumberInput
                label="Z-Index"
                value={layer.z_index}
                onChange={(v) => update({ z_index: v })}
                step={1}
                min={0}
              />
            </div>
          </section>

          {/* -- Flags -- */}
          <section className="flex gap-4">
            <label className="flex items-center gap-1.5 text-xs">
              <Checkbox
                checked={layer.locked}
                onCheckedChange={(v) => update({ locked: !!v })}
              />
              Locked
            </label>
            <label className="flex items-center gap-1.5 text-xs">
              <Checkbox
                checked={layer.visible}
                onCheckedChange={(v) => update({ visible: !!v })}
              />
              Visible
            </label>
          </section>

          {/* -- Edit points (S5): a polygon, or a boxed list-only price
              badge - the same shape by another name - toggles between the
              Transformer's resize anchors (select mode) and its own vertex
              + edge handles (edit-points mode). Double-click the shape or
              Enter does the same thing; this is the discoverable path for
              anyone who never finds either. */}
          {canEditPoints && onToggleEditPoints && (
            <section>
              <Button
                type="button"
                variant={editingPoints ? 'primary' : 'secondary'}
                size="sm"
                className="w-full"
                // Same eligibility `cornerHandleLayer` gates the canvas
                // side on (AC-S5-5 review): a locked or hidden layer has
                // no corner handles to edit points OF, so the button
                // disables rather than staying clickable into a mode with
                // nothing for it to show.
                disabled={layer.locked || !layer.visible}
                onClick={() => onToggleEditPoints(layer.id)}
              >
                {editingPoints ? 'Done editing points' : 'Edit points'}
              </Button>
            </section>
          )}

          {/* -- Slot binding -- */}
          <section>
            <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Slot Binding
            </h4>
            <SearchableSelect
              value={layer.slot_binding ?? '__none__'}
              onChange={(v: string) =>
                update({ slot_binding: (v === '__none__' ? null : v) as SlotBinding })
              }
              options={SLOT_BINDING_OPTIONS}
            />
          </section>

          {/* -- Subject (D7/AC-S4-1): which of a combo tag's products this
              layer reads. Parent first and default, so an existing design
              renders unchanged (AC-S4-4); a price badge's list also offers
              Tag total, the roll-up it always printed. */}
          {showSubjectPicker && (
            <SubjectInspector
              props={layer.props}
              parts={subjectParts ?? []}
              parentCode={subjectParentCode ?? ''}
              onChange={updateProps}
            />
          )}

          {/* -- Binding (a bound block, re-bound or relinked in one action) -- */}
          {layer.props.kind === 'group' && (
            <GroupBindingInspector
              layer={layer}
              bindingLabel={bindingLabel ?? null}
              onRebind={onRebind}
              onRelinkGroup={onRelinkGroup}
              onUseTemplate={onUseTemplate}
            />
          )}

          {/* -- Preview (D53): this block alone, against a real product -- */}
          {previewBlockId && onPreviewBlock && (
            <PreviewBlockInspector
              groupId={previewBlockId}
              label={previewBlockLabel ?? null}
              onPreview={onPreviewBlock}
              onClear={onClearBlockPreview}
            />
          )}

          {/* -- Type-specific props -- */}
          {layer.props.kind === 'text' && (
            <TextInspector
              layer={layer}
              props={layer.props}
              onChange={updateProps}
              onUpdate={update}
              resolvedText={resolvedText ?? null}
              fontOptions={fontOptions ?? STATIC_FONT_OPTIONS}
              onUploadFont={onUploadFont}
              onInsertField={onInsertField}
              usedColours={usedColours}
            />
          )}
          {layer.props.kind === 'price_badge' && (
            <PriceBadgeInspector
              props={layer.props}
              onChange={updateProps}
              usedColours={usedColours}
              fontOptions={fontOptions ?? STATIC_FONT_OPTIONS}
              onUploadFont={onUploadFont}
            />
          )}
          {layer.props.kind === 'shape' && (
            <ShapeInspector props={layer.props} onChange={updateProps} usedColours={usedColours} />
          )}
          {layer.props.kind === 'image' && (
            <ImageInspector
              layerId={layer.id}
              props={layer.props}
              onChange={updateProps}
              onChooseImage={onChooseImage}
            />
          )}
          {layer.props.kind === 'product_slot' && (
            <ProductSlotInspector props={layer.props} onChange={updateProps} />
          )}
          {layer.props.kind === 'badge' && (
            <BadgeInspector
              layerId={layer.id}
              props={layer.props}
              onChooseBadge={onChooseBadge}
            />
          )}
          {layer.props.kind === 'barcode' && (
            <BarcodeInspector
              layer={layer}
              props={layer.props}
              onChange={updateProps}
              onUpdate={update}
              resolvedText={resolvedText ?? null}
            />
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Text inspector
// ---------------------------------------------------------------------------

function TextInspector({
  layer,
  props,
  onChange,
  onUpdate,
  resolvedText,
  fontOptions,
  onUploadFont,
  onInsertField,
  usedColours,
}: {
  layer: TagLayer;
  props: Extract<TagLayerProps, { kind: 'text' }>;
  onChange: (changes: Partial<TagLayerProps>) => void;
  onUpdate: (changes: Partial<TagLayer>) => void;
  resolvedText: string | null;
  fontOptions: SearchableSelectOption[];
  onUploadFont?: () => void;
  onInsertField?: () => void;
  usedColours: string[];
}) {
  // A slot-bound layer is edited through `text_override`, never through
  // `props.text`: the binding survives the edit, so "Relink" is simply clearing
  // the override again rather than remembering what the product used to say.
  const bound = Boolean(layer.slot_binding);
  const overridden = bound && layer.text_override != null;
  // A typed-over layer holding a merge field is still following the product,
  // so it says so rather than showing the amber broken-link note (D57).
  const dynamic = isDynamic(layer);
  const shown = bound
    ? layer.text_override ?? resolvedText ?? props.text
    : props.text;

  const handleContent = (value: string) => {
    if (bound) onUpdate({ text_override: value });
    else onChange({ ...props, text: value });
  };

  return (
    <section>
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Text
      </h4>
      <div className="flex flex-col gap-2">
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between">
            <Label className="text-xs text-muted-foreground">Content</Label>
            <div className="flex items-center gap-1">
              {overridden && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-6 px-1.5 text-[10px]"
                  onClick={() => onUpdate({ text_override: null })}
                >
                  <Link2 className="mr-1 size-3" />
                  Relink
                </Button>
              )}
              {onInsertField && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-6 px-1.5 text-[10px]"
                  onClick={onInsertField}
                >
                  <Braces className="mr-1 size-3" />
                  Insert field
                </Button>
              )}
            </div>
          </div>
          <textarea
            className="min-h-[60px] w-full rounded-md border bg-background px-2 py-1 text-xs"
            value={shown}
            onChange={(e) => handleContent(e.target.value)}
          />
          {overridden && !dynamic && (
            <span className="flex items-center gap-1 text-[10px] text-amber-600">
              <Link2Off className="size-3" />
              Unlinked from product data
            </span>
          )}
          {dynamic && (
            <span className="flex items-center gap-1 text-[10px] text-sky-600">
              <Braces className="size-3" />
              Draws from product data
            </span>
          )}
          {/* D21 (AC-S16-1/S16-2): the inline canvas edit still shows the
              SOURCE (the token) - this read-only line is the only copy
              surface for what will actually print. */}
          {dynamic && resolvedText != null && (
            <div className="flex items-center justify-between gap-2 rounded-md border bg-muted/40 px-2 py-1">
              <span className="truncate text-xs" title={resolvedText}>
                {resolvedText}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 shrink-0 px-1.5 text-[10px]"
                onClick={() => {
                  void navigator.clipboard.writeText(resolvedText);
                  toast.success('Copied');
                }}
              >
                <Copy className="mr-1 size-3" />
                Copy rendered text
              </Button>
            </div>
          )}
        </div>
        <TypographyControls
          values={{
            fontFamily: props.fontFamily,
            fontSize: props.fontSize,
            fontWeight: props.fontWeight,
            italic: props.italic ?? false,
            underline: props.underline ?? false,
            strikethrough: props.strikethrough ?? false,
            align: props.align,
            lineHeight: props.lineHeight,
            letterSpacing: props.letterSpacing,
            padding: props.padding ?? ZERO_PADDING,
          }}
          onChange={(changes) => onChange({ ...props, ...changes })}
          colour={props.color}
          onColourChange={(v) => onChange({ ...props, color: v })}
          usedColours={usedColours}
          fontOptions={fontOptions}
          onUploadFont={onUploadFont}
        />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Typography controls, shared by the text and price badge sections (S6b)
// ---------------------------------------------------------------------------

/** Every side at 0mm - an absent `padding` reads as this (S3). */
const ZERO_PADDING: LayerPadding = { top: 0, right: 0, bottom: 0, left: 0 };

/**
 * Top/Right/Bottom/Left, one `NumberInput` each (S3, S3b).
 *
 * Shared by the Padding row every layer that has one shows, and the Margin
 * row a price badge shows above it (S3b, AC-6) - the same four fields either
 * way, only which `LayerPadding` they read and write differs.
 */
function SidesInput({
  value,
  onChange,
}: {
  value: LayerPadding;
  onChange: (value: LayerPadding) => void;
}) {
  return (
    <div className="grid grid-cols-4 gap-2">
      <NumberInput
        label="Top"
        value={value.top}
        onChange={(v) => onChange({ ...value, top: v })}
        step={0.5}
        min={0}
      />
      <NumberInput
        label="Right"
        value={value.right}
        onChange={(v) => onChange({ ...value, right: v })}
        step={0.5}
        min={0}
      />
      <NumberInput
        label="Bottom"
        value={value.bottom}
        onChange={(v) => onChange({ ...value, bottom: v })}
        step={0.5}
        min={0}
      />
      <NumberInput
        label="Left"
        value={value.left}
        onChange={(v) => onChange({ ...value, left: v })}
        step={0.5}
        min={0}
      />
    </div>
  );
}

/** What the controls SHOW. Null = the layer has not named one. */
interface TypographyValues {
  fontFamily: string;
  fontSize: number | null;
  fontWeight: number;
  italic: boolean;
  underline: boolean;
  strikethrough: boolean;
  align: 'left' | 'center' | 'right';
  lineHeight: number | null;
  letterSpacing: number;
  /** Absent on the layer reads as `ZERO_PADDING` (S3) - never null here. */
  padding: LayerPadding;
}

/**
 * What a control WRITES. Never null: a box the user typed in has a number in
 * it, and nothing here can put a field back to "unnamed".
 */
type TypographyChanges = Partial<Omit<TypographyValues, 'fontSize' | 'lineHeight'>> & {
  fontSize?: number;
  lineHeight?: number;
};

/**
 * Font family, size, B/I/U/S, weight, colour, alignment and spacing.
 *
 * One component for the text layer and the price badge (S6b, AC-S6-4): the
 * badge's figure is text a designer wants to set like any other, and a second
 * copy of these nine controls is where the two would start behaving
 * differently. The colour field is passed in rather than read off a fixed
 * key, because a badge keeps its figure's colour in `textColor`.
 */
function TypographyControls({
  values,
  onChange,
  colour,
  colourLabel = 'Colour',
  onColourChange,
  usedColours,
  fontOptions,
  onUploadFont,
  margin,
  onMarginChange,
}: {
  values: TypographyValues;
  onChange: (changes: TypographyChanges) => void;
  colour: string;
  colourLabel?: string;
  onColourChange: (value: string) => void;
  usedColours: string[];
  fontOptions: SearchableSelectOption[];
  onUploadFont?: () => void;
  /**
   * Present only for a price badge (S3b, AC-6): drawn as its own `Margin
   * (mm)` group ABOVE Padding. Absent renders no Margin row at all - a text
   * layer keeps Padding only (AC-11).
   */
  margin?: LayerPadding;
  onMarginChange?: (margin: LayerPadding) => void;
}) {
  return (
    <>
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <Label className="text-xs text-muted-foreground">Font Family</Label>
          {onUploadFont && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-1.5 text-[10px]"
              onClick={onUploadFont}
            >
              Manage fonts
            </Button>
          )}
        </div>
        <SearchableSelect
          value={values.fontFamily}
          onChange={(v: string) => onChange({ fontFamily: v })}
          options={fontOptions}
        />
      </div>
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <NumberInput
            label="Font Size"
            value={values.fontSize}
            onChange={(v) => onChange({ fontSize: v })}
            step={0.5}
            min={4}
          />
        </div>
        <ToggleGroup
          type="multiple"
          variant="outline"
          size="sm"
          value={[
            values.fontWeight >= 600 && 'bold',
            values.italic && 'italic',
            values.underline && 'underline',
            values.strikethrough && 'strikethrough',
          ].filter((v): v is string => Boolean(v))}
          onValueChange={(next: string[]) => {
            const on = new Set(next);
            // Only rewrite fontWeight when Bold's OWN pressed state actually
            // changed - a `type="multiple"` group reports every currently
            // pressed item on every click, so toggling Italic while a 600+
            // weight reads Bold as pressed must not collapse that weight to
            // the canonical 700 as a side effect of a click nowhere near it.
            const wasBold = values.fontWeight >= 600;
            const willBeBold = on.has('bold');
            onChange({
              fontWeight:
                willBeBold === wasBold ? values.fontWeight : willBeBold ? 700 : 400,
              italic: on.has('italic'),
              underline: on.has('underline'),
              strikethrough: on.has('strikethrough'),
            });
          }}
        >
          <ToggleGroupItem value="bold" aria-label="Bold" title="Bold">
            <Bold className="size-3.5" />
          </ToggleGroupItem>
          <ToggleGroupItem value="italic" aria-label="Italic" title="Italic">
            <Italic className="size-3.5" />
          </ToggleGroupItem>
          <ToggleGroupItem value="underline" aria-label="Underline" title="Underline">
            <Underline className="size-3.5" />
          </ToggleGroupItem>
          <ToggleGroupItem value="strikethrough" aria-label="Strikethrough" title="Strikethrough">
            <Strikethrough className="size-3.5" />
          </ToggleGroupItem>
        </ToggleGroup>
      </div>
      <div className="flex flex-col gap-1">
        <Label className="text-xs text-muted-foreground">Font Weight</Label>
        <SearchableSelect
          value={String(values.fontWeight)}
          onChange={(v: string) => onChange({ fontWeight: parseInt(v, 10) })}
          options={FONT_WEIGHT_OPTIONS}
        />
      </div>
      <ColorPicker
        label={colourLabel}
        value={colour}
        onChange={onColourChange}
        usedColours={usedColours}
      />
      <div className="flex flex-col gap-1">
        <Label className="text-xs text-muted-foreground">Align</Label>
        <div className="flex gap-1">
          {(['left', 'center', 'right'] as const).map((a) => (
            <button
              key={a}
              type="button"
              className={`flex-1 rounded border px-2 py-0.5 text-xs ${
                values.align === a
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-input hover:bg-accent'
              }`}
              onClick={() => onChange({ align: a })}
            >
              {a.charAt(0).toUpperCase() + a.slice(1)}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <NumberInput
          label="Line Height"
          value={values.lineHeight}
          onChange={(v) => onChange({ lineHeight: v })}
          step={0.1}
          min={0.5}
        />
        <NumberInput
          label="Letter Spacing"
          value={values.letterSpacing}
          onChange={(v) => onChange({ letterSpacing: v })}
          step={0.1}
        />
      </div>
      {margin && onMarginChange && (
        <div className="flex flex-col gap-1">
          <Label className="text-xs text-muted-foreground">Margin (mm)</Label>
          <SidesInput value={margin} onChange={onMarginChange} />
        </div>
      )}
      <div className="flex flex-col gap-1">
        <Label className="text-xs text-muted-foreground">Padding (mm)</Label>
        <SidesInput
          value={values.padding}
          onChange={(padding) => onChange({ padding })}
        />
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Shape inspector
// ---------------------------------------------------------------------------

/**
 * The shape select's write, with the polygon's corners seeded and dropped
 * alongside it (S4, AC-S4-1).
 *
 * Turning a rectangle into a polygon seeds the box's own four corners, so it
 * keeps looking exactly like the rectangle until a corner is moved; turning
 * it back drops them, so a later switch to polygon starts from the box again
 * rather than from corners the user cannot see.
 */
function withShape(
  props: Extract<TagLayerProps, { kind: 'shape' }>,
  shape: ShapeType,
): Extract<TagLayerProps, { kind: 'shape' }> {
  return {
    ...props,
    shape,
    // Explicitly undefined, not omitted: the caller MERGES this over the
    // layer's current props, so an omitted key would leave the old corners
    // in place.
    points: shape === 'polygon' ? (props.points ?? defaultPolygonPoints()) : undefined,
  };
}

function ShapeInspector({
  props,
  onChange,
  usedColours,
}: {
  props: Extract<TagLayerProps, { kind: 'shape' }>;
  onChange: (changes: Partial<TagLayerProps>) => void;
  usedColours: string[];
}) {
  return (
    <section>
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Shape
      </h4>
      <div className="flex flex-col gap-2">
        <div className="flex flex-col gap-1">
          <Label className="text-xs text-muted-foreground">Shape Type</Label>
          <SearchableSelect
            value={props.shape}
            onChange={(v: string) => onChange(withShape(props, v as ShapeType))}
            options={SHAPE_TYPE_OPTIONS}
          />
        </div>
        <ColorPicker
          label="Fill"
          value={props.fill}
          onChange={(v) => onChange({ ...props, fill: v })}
          usedColours={usedColours}
        />
        <ColorPicker
          label="Stroke"
          value={props.stroke}
          onChange={(v) => onChange({ ...props, stroke: v })}
          usedColours={usedColours}
        />
        <div className="grid grid-cols-2 gap-2">
          <NumberInput
            label="Stroke Width"
            value={props.strokeWidth}
            onChange={(v) => onChange({ ...props, strokeWidth: v })}
            step={0.25}
            min={0}
          />
          <NumberInput
            label="Corner Radius"
            value={props.cornerRadius}
            onChange={(v) => onChange({ ...props, cornerRadius: v })}
            step={0.5}
            min={0}
          />
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Image inspector
// ---------------------------------------------------------------------------

function ImageInspector({
  layerId,
  props,
  onChange,
  onChooseImage,
}: {
  layerId: string;
  props: Extract<TagLayerProps, { kind: 'image' }>;
  onChange: (changes: Partial<TagLayerProps>) => void;
  onChooseImage?: (layerId: string) => void;
}) {
  const source = imageSourceOf(props);
  // Never the id itself: a UUID has no business on a screen.
  const sourceLabel =
    source == null
      ? 'None chosen'
      : source.type === 'product_attachment'
        ? 'Product photo'
        : 'Asset library';

  return (
    <section>
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Image
      </h4>
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] text-muted-foreground">{sourceLabel}</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => onChooseImage?.(layerId)}
            disabled={!onChooseImage}
          >
            Choose image
          </Button>
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs text-muted-foreground">Fit</Label>
          <SearchableSelect
            value={props.fit}
            onChange={(v: string) => onChange({ ...props, fit: v as ImageFit })}
            options={IMAGE_FIT_OPTIONS}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="text-xs text-muted-foreground">Mask</Label>
          <SearchableSelect
            value={props.maskShape ?? 'none'}
            onChange={(v: string) =>
              onChange({ ...props, maskShape: v as ImageMaskShape })
            }
            options={MASK_SHAPE_OPTIONS}
          />
        </div>
        {/* Only when a crop is actually set (S8, AC-S8-5) - entering crop
            mode itself is the context menu's "Crop image", not this panel. */}
        {isCropped(props.cropRect) && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => onChange({ ...props, cropRect: undefined })}
          >
            Reset crop
          </Button>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Price badge inspector (D26)
// ---------------------------------------------------------------------------

function PriceBadgeInspector({
  props,
  onChange,
  usedColours,
  fontOptions,
  onUploadFont,
}: {
  props: Extract<TagLayerProps, { kind: 'price_badge' }>;
  onChange: (changes: Partial<TagLayerProps>) => void;
  usedColours: string[];
  fontOptions: SearchableSelectOption[];
  onUploadFont?: () => void;
}) {
  const typo = priceBadgeTypography(props);
  const insets = priceBadgeInsets(props);
  const hasMargin = props.margin != null;

  /**
   * Write a Margin edit (S3b, AC-6/10).
   *
   * The FIRST edit on a legacy badge (one with no `margin` yet) is what moves
   * it onto the new semantics: `padding` used to double as the callout's own
   * inset, so it resets to zero in the SAME update that writes `margin` - the
   * value the SidesInput just edited already carries the legacy padding on
   * every side it did not touch (it started from `insets.margin`), so the
   * callout does not jump (AC-10).
   */
  const writeMargin = (margin: LayerPadding) => {
    onChange(hasMargin ? { ...props, margin } : { ...props, margin, padding: ZERO_PADDING });
  };

  /**
   * Write a Padding edit (S3b, AC-6/10). Same first-edit rule as Margin: a
   * legacy badge seeds `margin` from whatever `padding` used to be, so the
   * callout stays put while the figure inset restarts at the value just typed.
   */
  const writePadding = (padding: LayerPadding) => {
    onChange(
      hasMargin ? { ...props, padding } : { ...props, margin: props.padding ?? ZERO_PADDING, padding },
    );
  };

  return (
    <section>
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Price Badge
      </h4>
      <div className="flex flex-col gap-2">
        <div className="flex flex-col gap-1">
          <Label className="text-xs text-muted-foreground">Variant</Label>
          <SearchableSelect
            value={props.variant}
            onChange={(v: string) =>
              onChange({ ...props, variant: v as PriceBadgeVariant })
            }
            options={PRICE_BADGE_VARIANT_OPTIONS}
          />
        </div>
        {/* The promotional block is always boxed - the struck price above a
            filled box is what it IS (D26) - so the choice only exists for a
            list-only badge (AC-S6-1/3). */}
        {props.variant === 'list_only' && (
          <label className="flex items-center gap-1.5 text-xs">
            <Checkbox
              checked={props.showBox === true}
              onCheckedChange={(v) => onChange({ ...props, showBox: !!v })}
            />
            Box
          </label>
        )}
        <ColorPicker
          label="Box Fill"
          value={props.fill}
          onChange={(v) => onChange({ ...props, fill: v })}
          usedColours={usedColours}
        />
        <NumberInput
          label="Corner Radius"
          value={props.cornerRadius}
          onChange={(v) => onChange({ ...props, cornerRadius: v })}
          step={0.5}
          min={0}
        />
        <label className="flex items-center gap-1.5 text-xs">
          <Checkbox
            checked={props.showNett}
            onCheckedChange={(v) => onChange({ ...props, showNett: !!v })}
          />
          Show NETT
        </label>
        {/* Off, the figure and the promo lines drop `RM` (S3c, AC-13/14). On
            by default so a badge saved before this reads exactly as it did
            (AC-15). */}
        <label className="flex items-center gap-1.5 text-xs">
          <Checkbox
            checked={props.showCurrency ?? true}
            onCheckedChange={(v) => onChange({ ...props, showCurrency: !!v })}
          />
          Show currency
        </label>
        {/* The figure is text, and a designer sets text the same way wherever
            it is (S6b, AC-S6-4). An empty box means the badge has not named
            that field, so it keeps drawing the way it always did. */}
        <TypographyControls
          values={{
            fontFamily: typo.fontFamily ?? '',
            fontSize: typo.fontSize,
            // The figure has always been bold, so that is what an unnamed
            // weight reads as rather than a blank select.
            fontWeight: typo.fontWeight ?? 700,
            italic: typo.italic,
            underline: typo.underline,
            strikethrough: typo.strikethrough,
            align: typo.align,
            lineHeight: typo.lineHeight,
            letterSpacing: typo.letterSpacing,
            padding: insets.padding,
          }}
          onChange={(changes) => {
            if (changes.padding) {
              writePadding(changes.padding);
              return;
            }
            onChange({ ...props, ...changes });
          }}
          margin={insets.margin}
          onMarginChange={writeMargin}
          colour={props.textColor}
          colourLabel="Text Colour"
          onColourChange={(v) => onChange({ ...props, textColor: v })}
          usedColours={usedColours}
          fontOptions={fontOptions}
          onUploadFont={onUploadFont}
        />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Preview inspector (D53)
// ---------------------------------------------------------------------------

/**
 * Preview THIS block, from wherever the selection is inside it.
 *
 * A block also carries its own eye chip on the canvas itself, shown on hover
 * or select (D10, S6); this is the same action from the sidebar, for a
 * designer who already has the block selected and does not want to go
 * hunting for its outline.
 */
function PreviewBlockInspector({
  groupId,
  label,
  onPreview,
  onClear,
}: {
  groupId: string;
  label: string | null;
  onPreview: (groupId: string) => void;
  onClear?: (groupId: string) => void;
}) {
  return (
    <section>
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Preview
      </h4>
      {label ? (
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="min-w-0 flex-1 truncate text-left text-[11px] font-medium hover:underline"
            onClick={() => onPreview(groupId)}
            title={label}
          >
            {label}
          </button>
          <button
            type="button"
            className="shrink-0 rounded-sm p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            onClick={() => onClear?.(groupId)}
            title="Stop previewing this block"
          >
            <X className="size-3" />
          </button>
        </div>
      ) : (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 w-full text-xs"
          onClick={() => onPreview(groupId)}
        >
          <Eye className="mr-1 size-3" />
          Preview this block with...
        </Button>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Group binding inspector
// ---------------------------------------------------------------------------

function GroupBindingInspector({
  layer,
  bindingLabel,
  onRebind,
  onRelinkGroup,
  onUseTemplate,
}: {
  layer: TagLayer;
  bindingLabel: string | null;
  onRebind?: (groupId: string) => void;
  onRelinkGroup?: (groupId: string) => void;
  onUseTemplate?: () => void;
}) {
  const props = layer.props as Extract<TagLayerProps, { kind: 'group' }>;
  const binding = props.binding;
  const isSet = Boolean(binding?.product_set_id);

  return (
    <section>
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Bound To
      </h4>
      {binding ? (
        <div className="flex flex-col gap-2">
          <p className="text-[11px] font-medium">
            {bindingLabel ?? (isSet ? 'Product set' : 'Product')}
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 flex-1 text-xs"
              onClick={() => onRebind?.(layer.id)}
              disabled={!onRebind}
            >
              Change {isSet ? 'set' : 'product'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => onRelinkGroup?.(layer.id)}
              disabled={!onRelinkGroup}
            >
              <Link2 className="mr-1 size-3" />
              Relink all
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-[10px] text-muted-foreground">
          This group is not bound to a product or set.
        </p>
      )}
      {onUseTemplate && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="mt-2 h-7 w-full text-xs"
          onClick={onUseTemplate}
        >
          <LayoutTemplate className="mr-1 size-3" />
          Use template...
        </Button>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Subject picker (D7/AC-S4-1)
// ---------------------------------------------------------------------------

/**
 * Which of a combo tag's products a layer reads. `-1` is the parent alone;
 * `0..n` is that part, in package order (`subjectParts`' own order). A price
 * badge alone also offers `Tag total` - absent `subjectPart` - since that is
 * what it always printed before this field existed (AC-S4-4).
 */
function SubjectInspector({
  props,
  parts,
  parentCode,
  onChange,
}: {
  props: TagLayerProps;
  parts: TagPartData[];
  parentCode: string;
  onChange: (changes: Partial<TagLayerProps>) => void;
}) {
  const isPriceBadge = props.kind === 'price_badge';
  // Two inspectors can be mounted at once (a template editor beside the
  // request designer), and a hard-coded id makes the second `<label>` point
  // at the first one's select.
  const selectId = useId();
  const subjectPart =
    'subjectPart' in props && typeof props.subjectPart === 'number'
      ? props.subjectPart
      : undefined;
  // r10 S6 (AC-S6-4): a tag's parts now carry every candidate of every
  // choice group, not only the one this tag chose, so the list is grouped
  // under the group's role ("Kitchen Tap") once any part carries one, and
  // this tag's own candidate says so. A row without roles - a fixed part, or
  // a row pinned before r10 - renders the same flat list it always did.
  const grouped = parts.some((part) => Boolean(part.role));
  const options: SearchableSelectOption[] = [
    ...(isPriceBadge ? [{ value: 'default', label: 'Tag total' }] : []),
    { value: '-1', label: parentCode || 'Parent' },
    ...parts.map((part, index) => ({
      value: String(index),
      label: `${part.code || part.name}${part.chosen ? ' (this tag)' : ''}`,
      ...(grouped && part.role ? { group: part.role } : {}),
    })),
  ];
  const value =
    subjectPart === undefined ? (isPriceBadge ? 'default' : '-1') : String(subjectPart);
  return (
    <section>
      <h4 className="mb-2 text-2xs font-semibold uppercase tracking-wider text-muted-foreground">
        Product
      </h4>
      {/* `htmlFor`/`id` is what actually pairs a `<label>` with its control
       *  (unlike the `<h4>` heading above, a section title every sibling
       *  inspector section uses) - `sr-only` since the heading already
       *  says "Product" visually, one word away. */}
      <Label htmlFor={selectId} className="sr-only">
        Product
      </Label>
      <SearchableSelect
        id={selectId}
        size="sm"
        value={value}
        onChange={(v: string) =>
          onChange({
            subjectPart: v === 'default' ? undefined : Number(v),
          } as Partial<TagLayerProps>)
        }
        options={options}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Product slot inspector
// ---------------------------------------------------------------------------

function ProductSlotInspector({
  props,
  onChange,
}: {
  props: Extract<TagLayerProps, { kind: 'product_slot' }>;
  onChange: (changes: Partial<TagLayerProps>) => void;
}) {
  return (
    <section>
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Product Slot
      </h4>
      <div className="flex flex-col gap-1">
        <Label className="text-xs text-muted-foreground">Field Key</Label>
        <SearchableSelect
          value={props.fieldKey}
          onChange={(v: string) => onChange({ ...props, fieldKey: v })}
          options={FIELD_KEY_OPTIONS}
        />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Badge inspector
// ---------------------------------------------------------------------------

function BadgeInspector({
  layerId,
  props,
  onChooseBadge,
}: {
  layerId: string;
  props: Extract<TagLayerProps, { kind: 'badge' }>;
  onChooseBadge?: (layerId: string) => void;
}) {
  return (
    <section>
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Badge
      </h4>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-muted-foreground">
          {props.assetId ? 'Chosen' : 'None chosen'}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={() => onChooseBadge?.(layerId)}
          disabled={!onChooseBadge}
        >
          Choose badge
        </Button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Barcode inspector (D18, S7)
// ---------------------------------------------------------------------------

function BarcodeInspector({
  layer,
  props,
  onChange,
  onUpdate,
  resolvedText,
}: {
  layer: TagLayer;
  props: Extract<TagLayerProps, { kind: 'barcode' }>;
  onChange: (changes: Partial<TagLayerProps>) => void;
  onUpdate: (changes: Partial<TagLayer>) => void;
  resolvedText: string | null;
}) {
  // Same pattern as the text layer's `text_override` (D23): the barcode
  // layer is always bound to slot 'barcode', so an override here works the
  // same way - value in, binding survives, Relink clears it again. The
  // product master stays the source of truth; the override lives in the doc
  // only.
  //
  // An EMPTY override is still an override (S5): `''` is not `null`, so
  // clearing the box does not fall back to the product's barcode the way
  // `null` does - only Relink does that. Before this an empty string was
  // coerced to `null` on the way out, so deleting the text and typing
  // something new snapped straight back to the product value every time.
  const overridden = layer.text_override != null;
  const shown = layer.text_override ?? resolvedText ?? '';

  return (
    <section>
      <h4 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Barcode
      </h4>
      <div className="flex flex-col gap-2">
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between">
            <Label className="text-xs text-muted-foreground">Barcode value</Label>
            {overridden && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 px-1.5 text-2xs"
                onClick={() => onUpdate({ text_override: null })}
              >
                <Link2 className="mr-1 size-3" />
                Relink
              </Button>
            )}
          </div>
          <Input
            value={shown}
            placeholder={resolvedText ?? 'No barcode on the product'}
            onChange={(e) => onUpdate({ text_override: e.target.value })}
          />
          {overridden && (
            <span className="flex items-center gap-1 text-2xs text-amber-600">
              <Link2Off className="size-3" />
              Unlinked from product data
            </span>
          )}
        </div>
        <label className="flex items-center gap-1.5 text-xs">
          <Checkbox
            checked={props.show_code}
            onCheckedChange={(v) => onChange({ ...props, show_code: !!v })}
          />
          Show product code
        </label>
      </div>
    </section>
  );
}

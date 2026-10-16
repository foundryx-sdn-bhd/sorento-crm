'use client';

/**
 * Main tag template canvas editor shell.
 *
 * Left sidebar: layers panel. Centre: a Konva Stage that FILLS the workspace,
 * with the artboard drawn at a pan offset inside it (D33). Right sidebar:
 * inspector panel. Top: toolbar.
 *
 * All layer positions and sizes are in mm; the canvas converts to pixels with a
 * zoom-dependent scale. State is local React state, saved on Save.
 *
 * The interaction model is Illustrator's, and the rules behind it are pure
 * functions in `lib/dealer-kit/canvas-geometry.ts`: wheel zooms about the
 * cursor, a select drag on empty space is a marquee, the hand tool (or held
 * Space) pans, a click takes a group and a double-click goes inside it, and a
 * group carries its descendants through every move, transform and clipboard
 * action.
 *
 * The MOUSE BUTTON decides which of those runs (D44). Left marquees, drags and
 * selects; the middle button pans for as long as it is held, whatever tool is
 * active; the right button starts nothing, because the context menu owns it.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';

import type Konva from 'konva';
import { Konva as KonvaGlobal } from 'konva/lib/Global';
import type {
  GroupBinding,
  ImageSource,
  PolygonPoint,
  TagBindingData,
  TagLayer,
  TagLayerProps,
  TagTemplateDoc,
} from '@/lib/dealer-kit/tag-template-types';
import {
  defaultTextProps,
  defaultShapeProps,
  defaultImageProps,
  defaultProductSlotProps,
  defaultPriceBadgeProps,
  defaultBadgeProps,
  defaultBarcodeProps,
} from '@/lib/dealer-kit/tag-template-types';
import {
  buildAccessoriesStrip,
  buildAlternativesRow,
  buildProductBlock,
  buildSetBlock,
  isDynamic,
  layerDisplay,
  layerText,
  rebindImageLayers,
  resolveSlotText,
  PRODUCT_BLOCK_SIZE,
  SET_BLOCK_SIZE,
} from '@/lib/dealer-kit/product-block';
import { renderMergeFields, soleMergeField } from '@/lib/dealer-kit/merge-fields';
import {
  moveEdge,
  movePoint,
  polygonPoints,
  refitPolygon,
  scalePolygonPoints,
  snapDelta,
} from '@/lib/dealer-kit/polygon-path';
import {
  actualSizeView,
  ancestorsOf,
  bandBetween,
  cloneLayers,
  descendantsOf,
  fitView,
  hitLayerAt,
  layerOverflowsArtboard,
  marqueeHits,
  moveLayers,
  refitAncestors,
  removeLayers,
  renameFontFamilyInLayers,
  reorderZ,
  reparentLayer,
  stageToMm,
  topmostChildAt,
  transformGroup,
  ungroupLayers,
  zoomAt,
  CANVAS_MAX_ZOOM,
  CANVAS_MIN_ZOOM,
  CANVAS_PX_PER_MM,
  type CanvasView,
  type ReorderDirection,
  type ReparentTarget,
} from '@/lib/dealer-kit/canvas-geometry';
import {
  WHOLE_TAG_BLOCK_ID,
  previewBindingFor,
  previewBlockOf,
  previewableBlocks,
  wholeTagBlock,
  type PreviewMap,
} from '@/lib/dealer-kit/preview';
import { paddedBox, reflowedTextSize } from '@/lib/dealer-kit/text-reflow';
import {
  guideCrossedIntoRuler,
  guideForAxis,
  moveGuide,
  newGuideId,
  placeOrMoveGuide,
  removeGuide,
  type RulerGuide,
} from '@/lib/dealer-kit/ruler-guides';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuShortcut,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import {
  ArrowDown,
  ArrowDownToLine,
  ArrowUp,
  ArrowUpToLine,
  Check,
  ChevronLeft,
  ChevronRight,
  ClipboardPaste,
  Copy,
  CornerLeftUp,
  CornerRightDown,
  Crop,
  Expand,
  Eye,
  EyeOff,
  Group as GroupIcon,
  Lock,
  Percent,
  Scissors,
  SquareDashed,
  Trash2,
  Undo2,
  Ungroup,
  Unlock,
  X,
} from 'lucide-react';
import type { ImperativePanelHandle } from 'react-resizable-panels';
import { AssetPickerDialog } from './AssetPickerDialog';
import { FontUploadDialog } from './FontUploadDialog';
import { ProductPickDialog, type PickMode } from './ProductPickDialog';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useKitLibrary, useTagBindings } from './useTagBindings';
import { getProductTagData } from '../../services/tagDataService';
import { CanvasToolbar, type CanvasTool, type ToolbarTrailingAction } from './CanvasToolbar';
import { CanvasRulers, RULER_THICKNESS } from './CanvasRulers';
import { LayersPanel } from './LayersPanel';
import { InspectorPanel } from './InspectorPanel';
import { InsertFieldDialog } from './InsertFieldDialog';
import { useCanvasHistory } from './useCanvasHistory';
import { useSnapGuides } from './useSnapGuides';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable';
import {
  DEFAULT_PANEL_LAYOUT,
  LEFT_MAX_PX,
  LEFT_MIN_PX,
  RAIL_MIN_PX,
  RIGHT_MAX_PX,
  RIGHT_MIN_PX,
  clampLeft,
  clampRailSplit,
  clampRight,
  readPanelLayout,
  writePanelLayout,
  type PanelLayout,
} from '@/lib/dealer-kit/canvas-panels';
import { toggleBold, toggleTextFlag, type TextFormatFlag } from '@/lib/dealer-kit/text-format';
import {
  getTagClipboard,
  setTagClipboard,
  subscribeTagClipboard,
} from '@/lib/dealer-kit/tag-clipboard';
import { InlineTextEditor } from './InlineTextEditor';

/** What a previewed block is showing, named the way a person reads it. */
interface PreviewChoice {
  id: string;
  label: string;
}

// This component is loaded with ssr:false by the page, so direct imports are safe.
import {
  Stage,
  Layer as KonvaLayer,
  Circle,
  Group,
  Rect,
  Line,
  Transformer,
  Label as KonvaLabel,
  Tag as KonvaLabelTag,
  Text as KonvaText,
  Image as KonvaImage,
} from 'react-konva';
import { KonvaTagLayer } from './KonvaTagLayer';
import type { CanvasReviewPin } from '@/lib/dealer-kit/review-comments';
import { useHtmlImage } from './useHtmlImage';
import {
  CROP_HANDLE_ANCHORS,
  cropOverlayLayout,
  cropRectFromDrag,
  panCropRect,
  resolvedCropRect,
  type CropRect,
} from '@/lib/dealer-kit/image-crop';
import { codeWithName } from '@/lib/product-display';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Konva starts a drag on the middle button as well as the left one by default
// (`dragButtons` ships as `[0, 1]`), so holding the wheel down over a layer
// dragged it while the workspace drew a band. The middle button pans (D44).
KonvaGlobal.dragButtons = [0];

let idCounter = 0;
function newLayerId(): string {
  idCounter += 1;
  return `layer-${Date.now()}-${idCounter}`;
}

/**
 * Whether Shift is held, tracked live rather than read once at keydown (S9).
 *
 * A rotate or resize drag runs its own mousemove loop inside Konva, and the
 * user can press or release Shift mid-drag ("pressing it mid-drag snaps
 * again"), so the Transformer's `rotationSnaps`/`keepRatio` props need a
 * value that keeps updating for the length of the drag, not a snapshot taken
 * when the drag started. Blur resets it so a Shift held when the window
 * loses focus (e.g. an OS shortcut) does not stay stuck on.
 */
function useShiftKey(): boolean {
  const [shift, setShift] = useState(false);
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'Shift') setShift(true);
    };
    const up = (e: KeyboardEvent) => {
      if (e.key === 'Shift') setShift(false);
    };
    const blur = () => setShift(false);
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', blur);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', blur);
    };
  }, []);
  return shift;
}

/**
 * Whatever is bound, named the way a person recognises it. Never a UUID.
 *
 * One function for the Inspector's "Bound to" line and for the preview chip,
 * because a product that reads one way in one of them and another way in the
 * other is two names for one thing.
 */
function describeBindingData(data: TagBindingData | null | undefined): string | null {
  if (!data) return null;
  if (data.kind === 'product') return codeWithName(data.product.code, data.product.name);
  if (data.kind === 'set') return codeWithName(data.set.set_code, data.set.name);
  return codeWithName(data.line.code, data.line.name);
}

/**
 * The eye chip that opens a block's (or the whole tag's) preview picker
 * (D10, S6). A plain DOM overlay, not a Konva node: it needs to sit ON TOP of
 * the canvas bitmap and stay a normal, focusable, hoverable button, which is
 * cheaper as HTML positioned over the Stage than as a Konva shape faking one.
 *
 * `onMouseEnter`/`onMouseLeave` (B1, AC-S6-4) exist because the chip sits
 * INSIDE the block's own corner: the pointer crossing from the Konva shape
 * onto the chip makes the Stage fire the shape's own `mouseleave` first,
 * which would otherwise unmount this button (nothing else was keeping it
 * shown) before the user's later, separate mousedown for the click ever
 * lands. Reasserting hover on the chip itself keeps it mounted for exactly as
 * long as the pointer is actually over it.
 */
function PreviewEyeButton({
  label,
  active,
  style,
  onClick,
  onMouseEnter,
  onMouseLeave,
}: {
  label: string;
  active: boolean;
  style: React.CSSProperties;
  onClick: () => void;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}) {
  return (
    <button
      type="button"
      className={cn(
        'absolute z-10 flex h-6 w-6 items-center justify-center rounded-full border bg-background shadow-sm hover:bg-accent',
        active && 'border-primary text-primary',
      )}
      style={style}
      title={label}
      aria-label={label}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <Eye className="size-3.5" />
    </button>
  );
}

/** One press of the toolbar's zoom buttons. The wheel uses its own factor. */
const ZOOM_BUTTON_FACTOR = 1.25;
/** Pixels the pointer may wander before a click counts as a marquee drag. */
const MARQUEE_SLOP_PX = 3;
/** How far a duplicate or a paste lands from its original, in mm. */
const CLONE_OFFSET_MM = 5;
/** Arrow-key nudge distances in mm: plain, Shift, Alt/Option. */
const NUDGE_MM = { base: 0.25, shift: 1, alt: 0.1 };
/** Rotation snap steps, degrees, ONLY while Shift is held (S9). */
const ROTATION_SNAPS = Array.from({ length: 24 }, (_, i) => i * 15);
/** How close to a snap step (degrees) counts as snapped, Shift held. */
const ROTATION_SNAP_TOLERANCE_DEG = 7;
/** How far above the rotate handle the live angle pill sits, screen px. */
const ROTATION_LABEL_OFFSET_PX = 24;

/**
 * Polygon corner-editing handles, in SCREEN pixels (S4).
 *
 * A constant, not a zoom-derived size: the Konva Layer here is never scaled
 * (every coordinate is multiplied by `scale` by hand), so a fixed radius
 * already IS a fixed size on screen at any zoom.
 */
const POLYGON_VERTEX_HANDLE_RADIUS_PX = 5;
const POLYGON_EDGE_HANDLE_SIZE_PX = 7;

/** Crop window handles (S8), same square-handle size as a polygon edge's. */
const CROP_HANDLE_SIZE_PX = 8;

const ZOOM_LIMITS = { min: CANVAS_MIN_ZOOM, max: CANVAS_MAX_ZOOM };

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface TagCanvasEditorProps {
  doc: TagTemplateDoc;
  onChange: (doc: TagTemplateDoc) => void;
  /**
   * Which promotion the preview prices resolve against. A template is not tied
   * to one, so this is normally absent and the badge previews list prices.
   */
  promotionId?: string | null;
  /**
   * What EVERY layer draws against, when the caller already knows (D51).
   *
   * The request designer edits one line's tag, so the thing on the canvas is
   * that line - resolved once by the pricing engine, with its marketing
   * override applied - rather than whatever each group happens to be bound to.
   * Absent in the template editor, where the document's own bindings answer.
   */
  boundData?: TagBindingData | null;
  /**
   * Anything the host wants above the Layers panel (D51: the request's lines).
   */
  leftRail?: ReactNode;
  /**
   * Every change to the layers, so a host that owns the document can keep up.
   *
   * The template editor does not use it: there, Save is the only moment the
   * document changes. The request designer does, because switching lines
   * remounts the canvas and the edits have to survive that.
   */
  onLayersChange?: (layers: TagLayer[]) => void;
  /** Offered in the Inspector on a selected group: swap the whole tag's template. */
  onUseTemplate?: () => void;
  /** The host owns saving, so the built-in Save bar would be a second Save. */
  hideSaveBar?: boolean;
  /**
   * The template id (template editor) or the placed tag's id (request
   * designer), so the clipboard (S3) can tell a paste back onto the SAME
   * doc (offset by `CLONE_OFFSET_MM`, as before) from a paste onto a
   * DIFFERENT one (land at the original x/y). Absent in a test render; every
   * copy then reads as the same doc, matching the old single-editor
   * behaviour.
   */
  docId?: string;
  /**
   * The host's own right-end toolbar actions (S7): Full screen / the
   * Template dropdown / Save for the request designer, Versions / Save /
   * Full screen for the template page - passed straight through to
   * `CanvasToolbar`'s own `trailing` slot. Absent renders no trailing group.
   */
  toolbarTrailing?: ToolbarTrailingAction[];
  /**
   * The salesperson's pinned change requests on THIS tag (r9 S2/D6), already
   * numbered. Drawn over the artboard at their own fractions; clicking one
   * opens its comment and selects no layer, which is why they are a DOM
   * overlay in the same coordinate space as the inline text editor rather
   * than Konva nodes inside the stage's own hit testing.
   *
   * The host owns the `Comments` toggle: hidden means an empty list.
   */
  reviewPins?: CanvasReviewPin[];
  /**
   * D13: how to resolve/reopen a pin from ITS OWN popover, same copy and
   * icons as `RequestDesignSection`'s footer (Check "Done", Undo2 "Reopen").
   * Rendered only when this is given - the template page and any other
   * read-only surface pass no `reviewPins` at all, and even a surface that
   * does but has nowhere to persist the decision simply omits this, and the
   * popover renders with no Done button.
   */
  onReviewPinResolve?: (pinId: string, resolved: boolean) => void | Promise<void>;
}

/** What the canvas is currently asking the user to pick. */
type PickerState =
  | { kind: 'none' }
  | { kind: 'add-product' }
  | { kind: 'add-set' }
  | { kind: 'rebind'; groupId: string; mode: PickMode }
  | { kind: 'alternatives' }
  | { kind: 'accessories' }
  | { kind: 'preview'; groupId: string; mode: PickMode };

/**
 * A layer that can carry its own corners: a polygon shape, or a price badge
 * drawing the flyer's white callout (S4, r4b AC-S6-2).
 */
type CornerHandleLayer = TagLayer & {
  props: Extract<TagLayerProps, { kind: 'shape' } | { kind: 'price_badge' }>;
};

/** What a drag is carrying, captured once when it starts. */
interface DragSession {
  anchorId: string;
  /** The layers the user grabbed. Descendants come along but are not roots. */
  roots: string[];
  /** Roots plus descendants: everything whose node moves. */
  moving: string[];
  start: Map<string, { x: number; y: number }>;
  dx: number;
  dy: number;
}

export function TagCanvasEditor({
  doc,
  onChange,
  promotionId,
  boundData,
  leftRail,
  onLayersChange,
  onUseTemplate,
  hideSaveBar,
  docId,
  toolbarTrailing,
  reviewPins,
  onReviewPinResolve,
}: TagCanvasEditorProps) {
  const [layers, setLayers] = useState<TagLayer[]>(doc.layers);
  /** Which change-request marker has its comment open (r9 S2/D6). */
  const [openPinId, setOpenPinId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  /** The text layer the inline editor (S2, D5) is currently open on, if any. */
  const [editingLayerId, setEditingLayerId] = useState<string | null>(null);
  /**
   * The corners a handle drag is CURRENTLY at, so the shape follows the
   * cursor. Never committed here: `handlePolygonDragEnd` writes once, so one
   * drag is one undo.
   */
  const [polygonPreview, setPolygonPreview] = useState<PolygonPoint[] | null>(null);
  const [view, setView] = useState<CanvasView>({ zoom: 1, panX: 0, panY: 0 });
  const [tool, setTool] = useState<CanvasTool>('select');
  const [spaceHeld, setSpaceHeld] = useState(false);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
  /**
   * Side-panel widths + collapsed state (S1, D7). Starts at the default so
   * server and first-paint markup match, then hydrates from localStorage -
   * the same trick `useDriveViewMode` uses to avoid a hydration mismatch.
   */
  const [panelLayout, setPanelLayoutState] = useState<PanelLayout>(DEFAULT_PANEL_LAYOUT);
  const [marquee, setMarquee] = useState<{
    x_mm: number;
    y_mm: number;
    width_mm: number;
    height_mm: number;
  } | null>(null);
  // Module-level, not `useState` (S3): the request designer remounts this
  // whole component with a new `key` on every line switch, and a local
  // clipboard would empty on that remount. `getTagClipboard` is stable across
  // renders, so it doubles as the required "getServerSnapshot"-free selector.
  const clipboard = useSyncExternalStore(subscribeTagClipboard, getTagClipboard);
  const [menuOnEmpty, setMenuOnEmpty] = useState(true);
  const [picker, setPicker] = useState<PickerState>({ kind: 'none' });
  const [pickerBusy, setPickerBusy] = useState(false);
  const [imagePicker, setImagePicker] = useState<{ layerId: string; badge: boolean } | null>(
    null,
  );
  const [fontUploadOpen, setFontUploadOpen] = useState(false);
  const [insertFieldOpen, setInsertFieldOpen] = useState(false);
  /** True while the middle button is held: the hand tool, borrowed (D44). */
  const [wheelPanning, setWheelPanning] = useState(false);
  /**
   * What each product BLOCK is DRAWN against while previewing (D53).
   *
   * Keyed by group id, because a tag carries several blocks and they are
   * several different products: the sink combo shows one sink and three
   * alternative taps. A block that is not in the map keeps its placeholders.
   *
   * Editor state only. It is never written into `layers` and Save never sees
   * it, which is the whole point: a template ships unbound so it can be reused
   * for any product in its family, and looking at it with real data must not
   * quietly bind it.
   */
  const [previews, setPreviews] = useState<PreviewMap>({});
  /**
   * The block the on-canvas eye is showing for (S6, D10). Hover only - the
   * SELECTED block reads straight off `selectedBlock` below, no state needed
   * for that half.
   */
  const [hoveredLayerId, setHoveredLayerId] = useState<string | null>(null);
  /**
   * A block's eye chip hovers itself (B1, AC-S6-4): the chip sits inside the
   * block's own corner, so the pointer crossing onto it makes the Stage fire
   * the block's `mouseleave` first. Without this, `hoveredLayerId` clearing
   * would unmount the chip before the click that followed the hover ever
   * landed - the whole point of `activeCanvasEyeBlockId` falling back to this
   * below.
   */
  const [chipHoveredBlockId, setChipHoveredBlockId] = useState<string | null>(null);
  /** Session-only ruler guides (D9/D17). Never touch the document. */
  const [rulerGuides, setRulerGuides] = useState<RulerGuide[]>([]);
  /**
   * The one guide, if any, currently selected for Delete/Backspace (D21,
   * AC-S8-2) - a plain click on it (not a drag) is what sets this. Kept
   * separate from `selectedIds` (layer selection): the two are mutually
   * exclusive, and mixing a guide id into that Set would make the Transformer
   * try to attach to it.
   */
  const [selectedGuideId, setSelectedGuideId] = useState<string | null>(null);

  const bindings = useTagBindings(promotionId);
  const library = useKitLibrary();

  const containerRef = useRef<HTMLDivElement>(null);
  /** The whole [left][canvas][right] row, measured to convert px <-> % (S1). */
  const panelGroupRef = useRef<HTMLDivElement>(null);
  const [panelGroupSize, setPanelGroupSize] = useState({ width: 0, height: 0 });
  const leftPanelRef = useRef<ImperativePanelHandle>(null);
  const rightPanelRef = useRef<ImperativePanelHandle>(null);
  /** The TAG SIZE / LAYERS divider inside the left rail (S7, GitHub #676). */
  const railPanelRef = useRef<ImperativePanelHandle>(null);
  const stageRef = useRef<Konva.Stage | null>(null);
  const transformerRef = useRef<Konva.Transformer>(null);
  const shiftHeld = useShiftKey();
  /**
   * The live angle pill shown while dragging the rotate handle (S9).
   *
   * `setState` per transform tick (review nit): unlike the text-reflow loop
   * right below it in `handleTransform`, which is already fully imperative
   * (`node.width()`/`textNode.width()`, one `batchDraw()`), this genuinely
   * triggers a React re-render every tick while rotating. Left as `setState`
   * rather than converted to a ref-updated `Label` here: that needs the
   * pill's `Label`/`Tag`/`Text` mounted (and its visibility toggled)
   * imperatively too, which is a JSX/ref restructuring of its own, not a
   * same-shape swap - a rotate drag is also a short, bounded gesture, not a
   * sustained one, so the cost is a real one but not a hot path worth that
   * risk without its own test coverage in this pass.
   */
  const [rotationLabel, setRotationLabel] = useState<{
    x: number;
    y: number;
    text: string;
  } | null>(null);
  const dragRef = useRef<DragSession | null>(null);
  /**
   * What a polygon handle drag started from (S4), so it can move by a delta.
   *
   * `cancelled` is how Escape abandons one (r4d). Konva delivers a `dragend`
   * even for a node destroyed mid-drag, and Escape destroys the handles by
   * deselecting, so the release still reaches `handlePolygonDragEnd` and used
   * to commit the half-drag the user had just walked away from.
   */
  const polygonDragRef = useRef<{
    kind: 'vertex' | 'edge';
    index: number;
    base: PolygonPoint[];
    origin: { x: number; y: number };
    cancelled: boolean;
  } | null>(null);
  const panRef = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const marqueeRef = useRef<{
    start: { x_mm: number; y_mm: number };
    additive: boolean;
  } | null>(null);
  const fittedRef = useRef(false);
  /**
   * Which guide a pointer drag is currently moving, if any (S6, B2).
   *
   * `moved` distinguishes a genuine drag from a plain click, the same way
   * `MARQUEE_SLOP_PX` does for the marquee below: it only flips once the
   * pointer has wandered past the slop from `downClient`, not on the very
   * first `mousemove` tick. A freshly-dropped guide sits INSIDE ruler
   * territory by construction (that is where the ruler's own mousedown put
   * it) - without the slop, the sub-pixel jitter every real click carries set
   * `moved` on tick one, and the guide deleted itself on mouseup for having
   * never actually left the ruler.
   *
   * `leftRuler` is the second half: only a drag that has genuinely LEFT ruler
   * territory at some point counts as "dragged back onto the ruler" on
   * release. A newly-spawned guide starts inside the ruler and so starts
   * `false`; a guide picked up off the canvas starts outside it and so starts
   * `true` - it can be sent home on the very first re-entry, exactly as
   * before.
   */
  const guideDragRef = useRef<{
    id: string;
    orientation: RulerGuide['orientation'];
    moved: boolean;
    leftRuler: boolean;
    downClient: { x: number; y: number };
  } | null>(null);

  const history = useCanvasHistory(doc.layers);
  const { computeSnap, guides, clearGuides } = useSnapGuides();

  const scale = CANVAS_PX_PER_MM * view.zoom;
  const canvasWidthPx = doc.width_mm * scale;
  const canvasHeightPx = doc.height_mm * scale;
  const stageWidth = Math.max(0, containerSize.width - RULER_THICKNESS);
  const stageHeight = Math.max(0, containerSize.height - RULER_THICKNESS);
  /** The hand tool, whether chosen or borrowed by holding Space (D35). */
  const handMode = tool === 'hand' || spaceHeld;

  // -- Commit ----------------------------------------------------------------

  /** One state write and one history entry, so one undo reverts one action. */
  const commit = useCallback(
    (next: TagLayer[], nextSelection?: Set<string>) => {
      setLayers(next);
      history.pushState(next);
      if (nextSelection) setSelectedIds(nextSelection);
    },
    [history],
  );

  /**
   * A brand font was renamed: rewrite every layer in the OPEN document that
   * named the old family, so unsaved edits do not save the dead name back
   * (AC-5, PLAN-brand-font-manage.md). The pure rewrite lives in
   * `canvas-geometry.ts` next to the other layer-array functions, testable
   * without mounting this component.
   */
  const renameFontFamilyInOpenDoc = useCallback(
    (oldName: string, newName: string) => {
      const next = renameFontFamilyInLayers(layers, oldName, newName);
      if (next !== layers) commit(next);
    },
    [layers, commit],
  );

  // -- Layer mutations -------------------------------------------------------

  const updateLayer = useCallback(
    (id: string, changes: Partial<TagLayer>) => {
      setLayers((prev) => {
        const layer = prev.find((l) => l.id === id);
        const geometric = (
          ['x_mm', 'y_mm', 'width_mm', 'height_mm', 'rotation_deg'] as const
        ).some((key) => key in changes);

        let next: TagLayer[];
        if (layer && layer.props.kind === 'group' && geometric) {
          // The inspector's X/Y/W/H on a group means the whole block (D38).
          next = transformGroup(prev, id, {
            x_mm: changes.x_mm ?? layer.x_mm,
            y_mm: changes.y_mm ?? layer.y_mm,
            width_mm: changes.width_mm ?? layer.width_mm,
            height_mm: changes.height_mm ?? layer.height_mm,
            rotation_deg: changes.rotation_deg ?? layer.rotation_deg,
          }).map((l) => (l.id === id ? { ...l, ...changes } : l));
        } else {
          next = prev.map((l) => (l.id === id ? { ...l, ...changes } : l));
          if (geometric) next = refitAncestors(next, id);
        }
        history.pushState(next);
        return next;
      });
    },
    [history],
  );

  const updateLayerProps = useCallback(
    (id: string, propsChanges: Partial<TagLayerProps>) => {
      setLayers((prev) => {
        const next = prev.map((l) =>
          l.id === id ? { ...l, props: { ...l.props, ...propsChanges } as TagLayerProps } : l,
        );
        history.pushState(next);
        return next;
      });
    },
    [history],
  );

  /**
   * B/I/U/Shift+X (S2, D4): applied to every selected TEXT layer at once, one
   * history entry, all landing on the same target state (AC-S2-4) - a mixed
   * selection turns the flag ON, an all-set selection turns it OFF. Bold is
   * `fontWeight`, not a boolean, so it gets its own branch using the same
   * "already bold" reading (>= 600) `toggleBold` itself uses per layer.
   */
  const applyTextFormat = useCallback(
    (flag: 'bold' | TextFormatFlag) => {
      const targetIds = Array.from(selectedIds).filter(
        (id) => layers.find((l) => l.id === id)?.props.kind === 'text',
      );
      if (targetIds.length === 0) return;

      if (flag === 'bold') {
        const targeted = layers.filter((l) => targetIds.includes(l.id));
        const allBold = targeted.every(
          (l) => (l.props as Extract<TagLayerProps, { kind: 'text' }>).fontWeight >= 600,
        );
        const nextWeight = toggleBold(allBold ? 600 : 400);
        commit(
          layers.map((l) =>
            targetIds.includes(l.id) && l.props.kind === 'text'
              ? { ...l, props: { ...l.props, fontWeight: nextWeight } }
              : l,
          ),
        );
        return;
      }

      commit(toggleTextFlag(layers, targetIds, flag));
    },
    [layers, selectedIds, commit],
  );

  const addLayer = useCallback(
    (layer: TagLayer) => {
      setLayers((prev) => {
        const next = [...prev, layer];
        history.pushState(next);
        return next;
      });
      setSelectedIds(new Set([layer.id]));
    },
    [history],
  );

  const addLayers = useCallback(
    (incoming: TagLayer[]) => {
      if (incoming.length === 0) return;
      setLayers((prev) => {
        const next = [...prev, ...incoming];
        history.pushState(next);
        return next;
      });
      // The GROUP is what gets selected when a block is dropped, so the
      // inspector opens on the binding rather than on whichever child happened
      // to be last.
      const group = incoming.find((layer) => layer.props.kind === 'group');
      setSelectedIds(new Set([group ? group.id : incoming[incoming.length - 1].id]));
    },
    [history],
  );

  /** The selected ids with no selected ancestor: one entry per grabbed block. */
  const selectionRoots = useMemo(() => {
    const ids = Array.from(selectedIds);
    return ids.filter((id) => !ancestorsOf(layers, id).some((a) => selectedIds.has(a)));
  }, [selectedIds, layers]);

  const deleteSelectedLayers = useCallback(() => {
    if (selectionRoots.length === 0) return;
    commit(removeLayers(layers, selectionRoots), new Set());
  }, [selectionRoots, layers, commit]);

  const duplicateSelectedLayers = useCallback(() => {
    if (selectionRoots.length === 0) return;
    const cloned = cloneLayers(layers, selectionRoots, newLayerId, CLONE_OFFSET_MM);
    if (cloned.layers.length === 0) return;
    commit([...layers, ...cloned.layers], new Set(cloned.ids));
  }, [selectionRoots, layers, commit]);

  const groupSelectedLayers = useCallback(() => {
    if (selectionRoots.length < 2) return;
    const children = layers.filter((l) => selectionRoots.includes(l.id));
    if (children.length < 2) return;

    const minX = Math.min(...children.map((l) => l.x_mm));
    const minY = Math.min(...children.map((l) => l.y_mm));
    const maxX = Math.max(...children.map((l) => l.x_mm + l.width_mm));
    const maxY = Math.max(...children.map((l) => l.y_mm + l.height_mm));
    const maxZ = Math.max(...layers.map((l) => l.z_index)) + 1;

    const groupId = newLayerId();
    const groupLayer: TagLayer = {
      id: groupId,
      type: 'group',
      x_mm: minX,
      y_mm: minY,
      width_mm: maxX - minX,
      height_mm: maxY - minY,
      rotation_deg: 0,
      z_index: maxZ,
      locked: false,
      visible: true,
      slot_binding: null,
      text_override: null,
      props: { kind: 'group', children: children.map((l) => l.id) },
    };

    commit([...layers, groupLayer], new Set([groupId]));
  }, [selectionRoots, layers, commit]);

  const ungroupSelectedLayers = useCallback(() => {
    const groupIds = layers
      .filter((l) => selectedIds.has(l.id) && l.props.kind === 'group')
      .map((l) => l.id);
    if (groupIds.length === 0) return;
    const result = ungroupLayers(layers, groupIds);
    commit(result.layers, new Set(result.ids));
  }, [selectedIds, layers, commit]);

  const reorderSelection = useCallback(
    (direction: ReorderDirection) => {
      if (selectionRoots.length === 0) return;
      commit(reorderZ(layers, selectionRoots, direction));
    },
    [selectionRoots, layers, commit],
  );

  /** Nudge the whole selection at once, so a group and a child move once each. */
  const nudgeSelection = useCallback(
    (dx_mm: number, dy_mm: number) => {
      const movable = selectionRoots.filter(
        (id) => !layers.find((l) => l.id === id)?.locked,
      );
      if (movable.length === 0) return;
      let next = moveLayers(layers, movable, dx_mm, dy_mm);
      for (const id of movable) next = refitAncestors(next, id);
      commit(next);
    },
    [selectionRoots, layers, commit],
  );

  // -- Add layer factories ---------------------------------------------------

  const centerX = doc.width_mm / 2;
  const centerY = doc.height_mm / 2;
  const maxZ = useMemo(
    () => (layers.length > 0 ? Math.max(...layers.map((l) => l.z_index)) + 1 : 1),
    [layers],
  );

  // -- Bound data ------------------------------------------------------------

  /** childId -> the group that owns it, so a layer can find its binding. */
  const groupOfChild = useMemo(() => {
    const map = new Map<string, TagLayer>();
    for (const layer of layers) {
      if (layer.props.kind !== 'group') continue;
      for (const childId of layer.props.children) map.set(childId, layer);
    }
    return map;
  }, [layers]);

  const bindingOf = useCallback(
    (layer: TagLayer): GroupBinding | undefined => {
      // The preview of the layer's own BLOCK wins (D53), then whatever the
      // document binds. Asking the document first would leave every slot empty
      // on a template, which ships unbound on purpose.
      const previewed = previewBindingFor(layer, previews, groupOfChild);
      if (previewed) return previewed;
      if (layer.props.kind === 'group') return layer.props.binding ?? undefined;
      const group = groupOfChild.get(layer.id);
      return group && group.props.kind === 'group'
        ? group.props.binding ?? undefined
        : undefined;
    },
    [groupOfChild, previews],
  );

  const dataOf = useCallback(
    (layer: TagLayer) => {
      // A previewed block is the deliberate "show me this other thing", so it
      // outranks even the data the host handed in (D51). Per layer rather than
      // per canvas: previewing ONE block of a request's tag must not blank the
      // rest of it.
      const previewed = previewBindingFor(layer, previews, groupOfChild);
      if (previewed) return bindings.get(previewed);
      if (boundData) return boundData;
      return bindings.get(bindingOf(layer));
    },
    [bindings, bindingOf, boundData, groupOfChild, previews],
  );

  // Resolve whatever the document already carries, once, on open. A template
  // stores bindings and no values (ADR 0008), so without this every bound layer
  // would open showing the text it was created with.
  useEffect(() => {
    // Nothing to resolve when the host hands the data in: the bindings would
    // be fetched only to be ignored by `dataOf`.
    if (boundData) return;
    const carried = doc.layers
      .map((layer) => (layer.props.kind === 'group' ? layer.props.binding : undefined))
      .filter((binding): binding is GroupBinding => Boolean(binding));
    if (carried.length > 0) void bindings.loadAll(carried);
    // Deliberately once per document: re-running on every layer change would
    // re-fetch the whole catalogue on each drag.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc]);

  const makeBaseLayer = useCallback(
    (type: TagLayer['type'], width: number, height: number): Omit<TagLayer, 'props'> => ({
      id: newLayerId(),
      type,
      x_mm: centerX - width / 2,
      y_mm: centerY - height / 2,
      width_mm: width,
      height_mm: height,
      rotation_deg: 0,
      z_index: maxZ,
      locked: false,
      visible: true,
      slot_binding: null,
      text_override: null,
    }),
    [centerX, centerY, maxZ],
  );

  const handleAddText = useCallback(() => {
    addLayer({ ...makeBaseLayer('text', 40, 10), props: defaultTextProps() });
  }, [addLayer, makeBaseLayer]);

  const handleAddShape = useCallback(() => {
    addLayer({ ...makeBaseLayer('shape', 30, 20), props: defaultShapeProps() });
  }, [addLayer, makeBaseLayer]);

  const handleAddImage = useCallback(() => {
    addLayer({ ...makeBaseLayer('image', 40, 30), props: defaultImageProps() });
  }, [addLayer, makeBaseLayer]);

  const handleAddProductSlot = useCallback(() => {
    addLayer({
      ...makeBaseLayer('product_slot', 40, 30),
      props: defaultProductSlotProps(),
    });
  }, [addLayer, makeBaseLayer]);

  const handleAddPriceBadge = useCallback(() => {
    addLayer({
      ...makeBaseLayer('price_badge', 45, 17),
      props: defaultPriceBadgeProps(),
    });
  }, [addLayer, makeBaseLayer]);

  const handleAddBadge = useCallback(() => {
    const layer = { ...makeBaseLayer('badge', 15, 15), props: defaultBadgeProps() };
    addLayer(layer);
    // Straight into the picker: a badge with no artwork is an empty square, and
    // the reason somebody pressed the button was to place a specific badge.
    setImagePicker({ layerId: layer.id, badge: true });
  }, [addLayer, makeBaseLayer]);

  const handleAddBarcode = useCallback(() => {
    // Bound to slot 'barcode' on creation, not left null like the generic
    // layer types: there is only one thing a barcode layer could ever draw
    // (the tag's own product), so there is no picker step to go through (S7).
    addLayer({
      ...makeBaseLayer('barcode', 40, 22),
      slot_binding: 'barcode',
      props: defaultBarcodeProps(),
    });
  }, [addLayer, makeBaseLayer]);

  // -- Bound blocks (D27) and preview (D41) ----------------------------------

  const closePicker = useCallback(() => {
    setPicker({ kind: 'none' });
    setPickerBusy(false);
  }, []);

  /**
   * The blocks the user may preview, and what each of them wants (D53).
   *
   * Read off the layers rather than off a binding, because a template carries
   * no binding: a `set_members` slot is the document saying what it is for.
   */
  const previewBlocks = useMemo(() => previewableBlocks(layers), [layers]);

  /**
   * ONE implicit block over every loose (ungrouped) bound layer (D10, S6).
   * Its eye lives on the tag frame rather than on any single layer - there is
   * no group to put it on.
   */
  const wholeTagPreviewBlock = useMemo(() => wholeTagBlock(layers), [layers]);

  /** Every block with its own eye - the real ones plus the frame's, if any. */
  const allPreviewBlocks = useMemo(
    () => (wholeTagPreviewBlock ? [...previewBlocks, wholeTagPreviewBlock] : previewBlocks),
    [previewBlocks, wholeTagPreviewBlock],
  );

  /**
   * One block's eye, from anywhere it is shown - the canvas chip, the
   * Inspector, or the tag frame (D10, S6): all three open the same
   * single-question picker aimed at that block.
   */
  const openBlockPreview = useCallback(
    (groupId: string) => {
      const block = allPreviewBlocks.find((b) => b.groupId === groupId);
      if (!block) return;
      setPicker({ kind: 'preview', groupId: block.groupId, mode: block.mode });
    },
    [allPreviewBlocks],
  );

  const clearBlockPreview = useCallback((groupId: string) => {
    setPreviews((prev) => {
      const next = { ...prev };
      delete next[groupId];
      return next;
    });
  }, []);

  const handlePick = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0) return;
      setPickerBusy(true);
      try {
        if (picker.kind === 'add-product') {
          const product = await bindings.loadProduct(ids[0]);
          if (!product) return;
          addLayers(
            buildProductBlock(product, {
              newId: newLayerId,
              x_mm: Math.max(0, centerX - PRODUCT_BLOCK_SIZE.width_mm / 2),
              y_mm: Math.max(0, centerY - PRODUCT_BLOCK_SIZE.height_mm / 2),
              z_index: maxZ,
            }),
          );
        } else if (picker.kind === 'add-set') {
          const set = await bindings.loadSet(ids[0]);
          if (!set) return;
          addLayers(
            buildSetBlock(set, {
              newId: newLayerId,
              x_mm: Math.max(0, centerX - SET_BLOCK_SIZE.width_mm / 2),
              y_mm: Math.max(0, centerY - SET_BLOCK_SIZE.height_mm / 2),
              z_index: maxZ,
            }),
          );
        } else if (picker.kind === 'preview') {
          const isSet = picker.mode === 'set';
          const groupId = picker.groupId;
          const loaded = isSet
            ? await bindings.loadSet(ids[0])
            : await bindings.loadProduct(ids[0]);
          if (!loaded) return;
          setPreviews((prev) => ({
            ...prev,
            [groupId]: isSet ? { product_set_id: ids[0] } : { product_id: ids[0] },
          }));
        } else if (picker.kind === 'alternatives') {
          const products = [];
          for (const id of ids) {
            const product = await getProductTagData(id, promotionId ?? null);
            products.push(product);
          }
          addLayers(
            buildAlternativesRow(products, {
              newId: newLayerId,
              x_mm: 5,
              y_mm: Math.max(0, centerY),
              z_index: maxZ,
            }),
          );
        } else if (picker.kind === 'accessories') {
          const items = [];
          for (const id of ids) {
            const product = await getProductTagData(id, promotionId ?? null);
            const primary =
              product.images.find((image) => image.is_primary) ?? product.images[0];
            items.push({
              caption: product.code,
              source: primary
                ? ({
                    type: 'product_attachment' as const,
                    attachmentId: primary.attachment_id,
                  })
                : null,
            });
          }
          addLayers(
            buildAccessoriesStrip(items, {
              newId: newLayerId,
              x_mm: 5,
              y_mm: Math.max(0, centerY),
              z_index: maxZ,
            }),
          );
        } else if (picker.kind === 'rebind') {
          const groupId = picker.groupId;
          const isSet = picker.mode === 'set';

          // The block's binding moves; its layout and any typed-over text stay.
          const product = isSet ? null : await bindings.loadProduct(ids[0]);
          const set = isSet ? await bindings.loadSet(ids[0]) : null;
          if (!product && !set) return;

          const group = layers.find((layer) => layer.id === groupId);
          const childIds = new Set(
            group && group.props.kind === 'group' ? group.props.children : [],
          );
          const binding: GroupBinding = isSet
            ? { product_set_id: ids[0] }
            : { product_id: ids[0] };

          setLayers((prev) => {
            const rebound = prev.map((layer) =>
              layer.id === groupId && layer.props.kind === 'group'
                ? { ...layer, props: { ...layer.props, binding } }
                : layer,
            );
            // An image layer still holding the old product's attachment id
            // would print the wrong photo under the right name.
            const next = product
              ? rebindImageLayers(rebound, childIds, { kind: 'product', product })
              : rebound;
            history.pushState(next);
            return next;
          });
        }
        closePicker();
      } finally {
        setPickerBusy(false);
      }
    },
    [
      picker,
      bindings,
      addLayers,
      centerX,
      centerY,
      maxZ,
      layers,
      history,
      promotionId,
      closePicker,
    ],
  );

  const handleRebind = useCallback(
    (groupId: string) => {
      const group = layers.find((layer) => layer.id === groupId);
      const binding =
        group && group.props.kind === 'group' ? group.props.binding : undefined;

      // What the block is FOR, not only what it already holds. Asking the
      // binding alone works for a block the editor built - "Add set" leaves a
      // set id behind - but not for a TEMPLATE, which ships unbound on purpose:
      // the seeded bathroom-furniture tag is entirely about a set, and it could
      // only ever be offered a product picker, so the one thing it exists to be
      // bound to was unreachable. A `set_members` slot is the block saying so.
      const childIds =
        group && group.props.kind === 'group' ? new Set(group.props.children) : null;
      const aboutASet =
        Boolean(binding?.product_set_id) ||
        (childIds != null &&
          layers.some(
            (layer) => childIds.has(layer.id) && layer.slot_binding === 'set_members',
          ));

      setPicker({
        kind: 'rebind',
        groupId,
        mode: aboutASet ? 'set' : 'product',
      });
    },
    [layers],
  );

  /** Put every typed-over layer in this block back on the product's own words. */
  const handleRelinkGroup = useCallback(
    (groupId: string) => {
      const group = layers.find((layer) => layer.id === groupId);
      if (!group || group.props.kind !== 'group') return;
      const childIds = new Set(group.props.children);
      setLayers((prev) => {
        const next = prev.map((layer) =>
          // A dynamic layer is skipped (D57): its override holds merge fields,
          // so it already draws from the product, and clearing it would delete
          // the sentence somebody wrote rather than repair a broken link.
          childIds.has(layer.id) && layer.slot_binding && !isDynamic(layer)
            ? { ...layer, text_override: null }
            : layer,
        );
        history.pushState(next);
        return next;
      });
    },
    [layers, history],
  );

  const handleChooseImage = useCallback((layerId: string) => {
    setImagePicker({ layerId, badge: false });
  }, []);

  const handleChooseBadge = useCallback((layerId: string) => {
    setImagePicker({ layerId, badge: true });
  }, []);

  const handleImagePicked = useCallback(
    (source: ImageSource) => {
      const target = imagePicker;
      if (!target) return;
      setLayers((prev) => {
        const next = prev.map((layer) => {
          if (layer.id !== target.layerId) return layer;
          if (layer.props.kind === 'badge') {
            if (source.type !== 'asset') return layer;
            return { ...layer, props: { ...layer.props, assetId: source.assetId } };
          }
          if (layer.props.kind === 'image') {
            return { ...layer, props: { ...layer.props, source } };
          }
          return layer;
        });
        history.pushState(next);
        return next;
      });
      // A freshly uploaded asset is not in the library map yet, so refresh it or
      // the layer draws its no-image state until the next open.
      void library.reload();
      setImagePicker(null);
    },
    [imagePicker, history, library],
  );

  // -- Selection and group isolation (D37) -----------------------------------

  /**
   * The groups the user is inside, derived from the selection rather than
   * stored: every ancestor of every selected layer. Nothing to keep in step
   * with undo, redo or a Layers panel click, because there is no second copy.
   */
  const entered = useMemo(() => {
    const set = new Set<string>();
    for (const id of selectedIds) {
      for (const ancestorId of ancestorsOf(layers, id)) set.add(ancestorId);
    }
    return set;
  }, [selectedIds, layers]);

  /** The innermost group being edited, which scopes a marquee (D36). */
  const insideGroupId = useMemo(() => {
    const first = Array.from(selectedIds)[0];
    if (!first) return null;
    return ancestorsOf(layers, first)[0] ?? null;
  }, [selectedIds, layers]);

  /** A raw canvas hit, answered at the level the user is working at. */
  const resolveTarget = useCallback(
    (rawId: string) => {
      const chain = [...ancestorsOf(layers, rawId)].reverse();
      for (const ancestorId of chain) {
        if (!entered.has(ancestorId)) return ancestorId;
      }
      return rawId;
    },
    [layers, entered],
  );

  /** Layers panel and inspector: pick exactly what was asked for. */
  const handleSelect = useCallback((id: string, additive: boolean) => {
    // Selecting a LAYER deselects the guide (B4). The two selections share one
    // Delete key, so leaving both live makes what that key removes depend on
    // an order the user cannot see.
    setSelectedGuideId(null);
    setSelectedIds((prev) => {
      if (additive) {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }
      return new Set([id]);
    });
  }, []);

  const handleCanvasSelect = useCallback(
    (rawId: string, additive: boolean) => {
      const id = resolveTarget(rawId);
      setSelectedGuideId(null);
      setSelectedIds((prev) => {
        if (additive) {
          const next = new Set(prev);
          if (next.has(id)) next.delete(id);
          else next.add(id);
          return next;
        }
        // Grabbing something already selected keeps the multi-selection, so it
        // can be dragged as one.
        if (prev.has(id)) return prev;
        return new Set([id]);
      });
    },
    [resolveTarget],
  );

  const pointerMm = useCallback(() => {
    const point = stageRef.current?.getPointerPosition();
    return point ? stageToMm(view, point.x, point.y) : null;
  }, [view]);

  /**
   * A layer's own hover state (S6, D10) - not yet resolved to a BLOCK, which
   * `hoveredBlockId` below does: hovering any child of a block should reveal
   * its eye, and that resolution belongs with the other block bookkeeping,
   * not here.
   */
  const handleLayerHoverChange = useCallback((id: string, hovering: boolean) => {
    setHoveredLayerId((prev) => {
      if (hovering) return id;
      return prev === id ? null : prev;
    });
  }, []);

  /** A block's own eye chip re-asserting its hover (B1, AC-S6-4). */
  const handleChipHoverChange = useCallback((groupId: string, hovering: boolean) => {
    setChipHoveredBlockId((prev) => {
      if (hovering) return groupId;
      return prev === groupId ? null : prev;
    });
  }, []);

  const handleLayerDoubleClick = useCallback(
    (rawId: string) => {
      const targetId = resolveTarget(rawId);
      const target = layers.find((layer) => layer.id === targetId);
      if (!target) return;
      // A text layer opens the inline editor in place (S2, D5); a group still
      // steps a level in, exactly as before.
      if (target.props.kind === 'text') {
        setSelectedIds(new Set([target.id]));
        setEditingLayerId(target.id);
        return;
      }
      // A polygon enters edit-points mode (S5): select mode (a single click)
      // already shows the Transformer's resize anchors, so the second click
      // is what asks for the vertex/edge handles instead. Any other shape
      // has nothing a second click could mean, so it is just the click that
      // selected it, twice.
      if (target.props.kind === 'shape') {
        setSelectedIds(new Set([target.id]));
        if (target.props.shape === 'polygon') setEditingPointsId(target.id);
        return;
      }
      // A boxed list-only price badge is the same shape by another name
      // (AC-S6-2, AC-S5-4) - the same two modes apply.
      if (
        target.props.kind === 'price_badge' &&
        target.props.variant === 'list_only' &&
        target.props.showBox === true
      ) {
        setSelectedIds(new Set([target.id]));
        setEditingPointsId(target.id);
        return;
      }
      if (target.props.kind !== 'group') return;
      const point = pointerMm();
      const childId = point
        ? topmostChildAt(layers, targetId, point.x_mm, point.y_mm)
        : null;
      const fallback = target.props.children.find((id) =>
        layers.some((layer) => layer.id === id),
      );
      const pick = childId ?? fallback;
      if (pick) setSelectedIds(new Set([pick]));
    },
    [layers, resolveTarget, pointerMm],
  );

  // -- Polygon corner handles (S4, r4b; select vs edit-points modes, S5) -----

  /**
   * Which layer is in edit-points mode (S5), if any - raw state, not
   * derived, because ENTERING the mode is a real user action (double-click,
   * Enter, the Inspector button) with no geometry to derive it from. Reading
   * it is always through `editingPoints` below, which folds in eligibility.
   */
  const [editingPointsId, setEditingPointsId] = useState<string | null>(null);

  /**
   * The layer ELIGIBLE for corner handles: the SOLE selection, whenever it
   * is an unlocked, visible polygon - or a price badge drawing the flyer's
   * white callout, which is the same shape by another name (AC-S6-2).
   *
   * Derived rather than held in state, so there is one source of truth:
   * clicking empty canvas, selecting something else, locking it, switching
   * the shape back to a rectangle or unticking Box all take the eligibility
   * away without a single setter. `editingPointsId` below is what actually
   * decides whether the handles show (S5) - this only says whether they
   * COULD.
   */
  const cornerHandleLayer = useMemo((): CornerHandleLayer | null => {
    if (selectedIds.size !== 1) return null;
    const layer = layers.find((l) => selectedIds.has(l.id));
    if (!layer || layer.locked || !layer.visible) return null;
    if (layer.props.kind === 'shape') {
      return layer.props.shape === 'polygon' ? (layer as CornerHandleLayer) : null;
    }
    if (layer.props.kind === 'price_badge') {
      // A promotional badge's box is only the part of the layer under the
      // struck price, and it keeps its rounded rectangle (AC-S6-3): there are
      // no corners of its own to drag.
      return layer.props.variant === 'list_only' && layer.props.showBox === true
        ? (layer as CornerHandleLayer)
        : null;
    }
    return null;
  }, [selectedIds, layers]);

  /**
   * Whether the eligible layer is actually in EDIT-POINTS mode right now
   * (S5): select mode (single click) shows the Transformer's full anchor
   * set instead; double-click, Enter, or the Inspector's "Edit points"
   * button flip `editingPointsId` to the layer's id. A derived guard, not an
   * effect - selecting anything else, or the layer losing eligibility
   * (locked, hidden, deselected, shape switched away from polygon), takes
   * this back to false the instant `cornerHandleLayer` itself goes null or
   * points elsewhere, with nothing having to clear the raw id.
   */
  const editingPoints = Boolean(cornerHandleLayer && cornerHandleLayer.id === editingPointsId);

  /**
   * Clears the raw id once it stops pointing at the eligible layer (B2):
   * `editingPoints` above already derives false the instant that happens, so
   * the handles disappear correctly - but the id itself stayed put, so
   * re-selecting the SAME polygon (click empty, then click it again) matched
   * again and landed straight back in edit-points mode instead of showing
   * the Transformer's own anchors like any other first click does.
   */
  useEffect(() => {
    if (editingPointsId && cornerHandleLayer?.id !== editingPointsId) {
      setEditingPointsId(null);
    }
  }, [cornerHandleLayer, editingPointsId]);

  /** Where every handle sits, in the layer's own pixel space. Edit-points
   * mode only (S5) - select mode shows the Transformer's own anchors instead. */
  const polygonHandles = useMemo(() => {
    if (!cornerHandleLayer || !editingPoints) return null;
    const width = cornerHandleLayer.width_mm * scale;
    const height = cornerHandleLayer.height_mm * scale;
    const vertices = scalePolygonPoints(
      polygonPreview ?? polygonPoints(cornerHandleLayer.props),
      width,
      height,
    );
    return {
      layer: cornerHandleLayer,
      width,
      height,
      vertices,
      edges: vertices.map((point, index) => {
        const next = vertices[(index + 1) % vertices.length];
        return { x: (point.x + next.x) / 2, y: (point.y + next.y) / 2 };
      }),
    };
  }, [cornerHandleLayer, editingPoints, polygonPreview, scale]);

  const startPolygonDrag = useCallback(
    (kind: 'vertex' | 'edge', index: number) => {
      const layer = cornerHandleLayer;
      if (!layer) return;
      // The BASE is read once, at the start: every tick of the drag is a
      // delta from where the handle began, so a clamped tick cannot ratchet
      // the corner along by re-basing on its own clamped result.
      const base = polygonPoints(layer.props);
      const points = scalePolygonPoints(base, layer.width_mm * scale, layer.height_mm * scale);
      const from = points[index];
      const to = points[(index + 1) % points.length];
      if (!from || !to) return;
      polygonDragRef.current = {
        kind,
        index,
        base,
        origin:
          kind === 'vertex' ? from : { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 },
        cancelled: false,
      };
    },
    [cornerHandleLayer, scale],
  );

  /**
   * Abandon the drag in flight, leaving the shape exactly as it was (r4d).
   *
   * Only the flag and the preview: the drag ref itself has to survive, because
   * the `dragend` this is cancelling has not arrived yet - it comes when the
   * handles unmount, or later when the button finally comes up, and it is that
   * handler which reads the flag and declines to commit.
   */
  const cancelPolygonDrag = useCallback(() => {
    const drag = polygonDragRef.current;
    if (!drag) return;
    drag.cancelled = true;
    setPolygonPreview(null);
  }, []);

  /**
   * The delta from the drag's own start, in the layer's local pixel space -
   * the Group carrying these handles already has the layer's rotation, so a
   * plain `node.x()/y()` minus `origin` is unrotated (r4b's own comment on
   * `refitPolygon` says the same thing for mm space). Shift (S1, AC-S1-1/3)
   * snaps that delta BEFORE it is normalized by width/height, because the
   * snap is about the shape drawn in pixels, not the box's aspect ratio - a
   * 45-degree pixel move on a non-square box is not a 45-degree normalized
   * one. The node is repositioned to the snapped point so the handle stops
   * exactly where the shape does, rather than trailing the raw cursor.
   */
  const polygonPointsFromDrag = useCallback(
    (e: Konva.KonvaEventObject<DragEvent>) => {
      const drag = polygonDragRef.current;
      const layer = cornerHandleLayer;
      if (!drag || !layer) return null;
      const width = layer.width_mm * scale;
      const height = layer.height_mm * scale;
      if (width <= 0 || height <= 0) return null;
      const node = e.target;
      let dx = node.x() - drag.origin.x;
      let dy = node.y() - drag.origin.y;
      if (e.evt?.shiftKey) {
        const snapped = snapDelta(dx, dy);
        dx = snapped.dx;
        dy = snapped.dy;
        node.position({ x: drag.origin.x + dx, y: drag.origin.y + dy });
      }
      const normDx = dx / width;
      const normDy = dy / height;
      return drag.kind === 'vertex'
        ? movePoint(drag.base, drag.index, normDx, normDy)
        : moveEdge(drag.base, drag.index, normDx, normDy);
    },
    [cornerHandleLayer, scale],
  );

  const handlePolygonDragMove = useCallback(
    (e: Konva.KonvaEventObject<DragEvent>) => {
      const next = polygonPointsFromDrag(e);
      if (next) setPolygonPreview(next);
    },
    [polygonPointsFromDrag],
  );

  /**
   * Release: re-fit the BOX around wherever the corners ended up (r4b).
   *
   * A corner is normalized against the layer box, so a drag past the wall
   * leaves a coordinate outside [0, 1]. `refitPolygon` turns that back into a
   * box that actually contains the shape, which is what keeps the
   * Transformer, the snap guides and the Inspector's W/H describing the thing
   * on screen (AC-S4-11).
   *
   * ONE `updateLayer` call, carrying the geometry AND the props: `updateLayer`
   * pushes one history entry, so one drag stays one undo (AC-S4-7) even
   * though it changed four numbers and every corner.
   */
  const handlePolygonDragEnd = useCallback(
    (e: Konva.KonvaEventObject<DragEvent>) => {
      const drag = polygonDragRef.current;
      const next = polygonPointsFromDrag(e);
      const layer = cornerHandleLayer;
      polygonDragRef.current = null;
      setPolygonPreview(null);
      // Escape already abandoned this one (r4d). Checked FIRST: everything
      // below writes the layer, and a cancelled drag must leave no trace -
      // no geometry, no history entry, nothing for the autosave to send.
      if (drag?.cancelled) return;
      if (!next || !layer) return;

      const refit = refitPolygon({
        x: layer.x_mm,
        y: layer.y_mm,
        width: layer.width_mm,
        height: layer.height_mm,
        rotation: layer.rotation_deg,
        points: next,
      });
      updateLayer(layer.id, {
        x_mm: refit.x,
        y_mm: refit.y,
        width_mm: refit.width,
        height_mm: refit.height,
        props: { ...layer.props, points: refit.points },
      });

      // The handles re-derive from the REFITTED props, but Konva left this
      // node wherever the pointer dropped it - in the old box's coordinates,
      // inside a group that has just moved. Put it back by hand or it strands
      // a handle's width away from the corner it stands for.
      if (drag) {
        const vertices = scalePolygonPoints(
          refit.points,
          refit.width * scale,
          refit.height * scale,
        );
        const from = vertices[drag.index];
        const to = vertices[(drag.index + 1) % vertices.length];
        if (from && to) {
          e.target.position(
            drag.kind === 'vertex'
              ? { x: from.x, y: from.y }
              : { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 },
          );
        }
      }
    },
    [polygonPointsFromDrag, cornerHandleLayer, updateLayer, scale],
  );

  /**
   * The layer as the canvas should DRAW it mid-drag: the committed corners
   * everywhere else, the dragged ones on the polygon being edited.
   */
  const withPolygonPreview = useCallback(
    (layer: TagLayer): TagLayer => {
      if (!polygonPreview || layer.id !== cornerHandleLayer?.id) return layer;
      const props = layer.props;
      if (props.kind !== 'shape' && props.kind !== 'price_badge') return layer;
      return { ...layer, props: { ...props, points: polygonPreview } };
    },
    [polygonPreview, cornerHandleLayer],
  );

  /**
   * A corner drag that is interrupted by the handles disappearing (Escape
   * deselects, the layer is deleted or locked mid-drag) never reaches
   * `handlePolygonDragEnd` - Konva's dragend fires on a node that has
   * already unmounted, so React never hears it. Once `cornerHandleLayer`
   * goes null there is nothing left to preview against, so drop the stale
   * preview and the drag ref here instead (AC-S4-7).
   */
  useEffect(() => {
    if (!cornerHandleLayer) {
      setPolygonPreview(null);
      polygonDragRef.current = null;
    }
  }, [cornerHandleLayer]);

  // -- Image crop (S8) --------------------------------------------------------

  /** The image layer currently in crop mode, or null. Entered ONLY from the
   * context menu's "Crop image" (double-click on an image stays a no-op). */
  const [cropEditingLayerId, setCropEditingLayerId] = useState<string | null>(null);
  /** The crop window mid-edit, normalised 0-1 against the source. Seeded from
   * the layer's own `cropRect` on entry; nothing is written to the layer
   * until Enter/click-outside commits it. */
  const [cropDraft, setCropDraft] = useState<CropRect | null>(null);
  const cropDragRef = useRef<{
    anchor: { fx: number; fy: number } | null;
    base: CropRect;
    origin: { x: number; y: number };
    cancelled: boolean;
    /** The last rect a MOVE tick actually computed (r6 S8 review, #723) -
     *  `handleCropDragEnd` reads this instead of re-deriving from the drag
     *  event's own node, which by then no longer carries the raw pointer
     *  position. See the comment on `handleCropDragEnd` below for why. */
    lastRect: CropRect;
  } | null>(null);

  /** Eligibility mirrors `cornerHandleLayer`'s own guard (S5): locked,
   * hidden, deleted or no-longer-an-image all take the mode away for free
   * the next time this recomputes, the same "derived guard" pattern. */
  const cropEditingLayer = useMemo(() => {
    if (!cropEditingLayerId) return null;
    const layer = layers.find((l) => l.id === cropEditingLayerId);
    if (!layer || layer.locked || !layer.visible || layer.props.kind !== 'image') return null;
    return layer as TagLayer & { props: Extract<TagLayerProps, { kind: 'image' }> };
  }, [cropEditingLayerId, layers]);

  const cropImageUrl = cropEditingLayer
    ? layerDisplay(cropEditingLayer, dataOf(cropEditingLayer), library.assetUrls)?.imageUrl
    : null;
  const cropImage = useHtmlImage(cropImageUrl ?? null);

  /**
   * The overlay's own layout for an ARBITRARY crop rect, not just the
   * current `cropDraft` (r6 S8 review, #723): `cropRectFromEvent` and
   * `positionCropHandle` below need it for `drag.base` (the STABLE rect a
   * drag started from) and for the in-flight `next` rect a tick just
   * computed, not only for what has already committed to state.
   *
   * `cropOverlayLayout` (`lib/dealer-kit/image-crop.ts`) fits the CROPPED
   * region per the layer's own `fit` - the SAME maths `KonvaTagLayer`'s
   * `ImageContent` draws the committed layer with - rather than the old
   * always-CONTAIN whole-image frame this replaced: that frame agreed with
   * itself (the dimmed pass and the bright window both derived from it) but
   * never with what the layer actually draws, so the two disagreed on scale
   * and centring and the picture looked doubled.
   */
  const layoutForCropRect = useCallback(
    (rect: CropRect) => {
      if (!cropEditingLayer || !cropImage) return null;
      const w = cropEditingLayer.width_mm * scale;
      const h = cropEditingLayer.height_mm * scale;
      if (w <= 0 || h <= 0) return null;
      return cropOverlayLayout(rect, cropImage, cropEditingLayer.props.fit, w, h);
    },
    [cropEditingLayer, cropImage, scale],
  );

  const cropLayout = cropDraft ? layoutForCropRect(cropDraft) : null;

  const enterCropMode = useCallback(() => {
    if (selectedIds.size !== 1) return;
    const layer = layers.find((l) => selectedIds.has(l.id));
    if (!layer || layer.props.kind !== 'image') return;
    setCropEditingLayerId(layer.id);
    setCropDraft(resolvedCropRect(layer.props.cropRect));
  }, [selectedIds, layers]);

  /** Writes the draft, one history entry, then leaves the mode. */
  const commitCrop = useCallback(() => {
    const layer = cropEditingLayer;
    const draft = cropDraft;
    setCropEditingLayerId(null);
    setCropDraft(null);
    cropDragRef.current = null;
    if (!layer || !draft) return;
    updateLayerProps(layer.id, { cropRect: draft });
  }, [cropEditingLayer, cropDraft, updateLayerProps]);

  /** Esc (AC-S8-3): the layer's own `cropRect` was never touched, so there
   * is nothing to undo - just stop editing. */
  const cancelCropMode = useCallback(() => {
    setCropEditingLayerId(null);
    setCropDraft(null);
    cropDragRef.current = null;
  }, []);

  const startCropDrag = useCallback(
    (anchor: { fx: number; fy: number } | null, origin: { x: number; y: number }) => {
      if (!cropDraft) return;
      cropDragRef.current = { anchor, base: cropDraft, origin, cancelled: false, lastRect: cropDraft };
    },
    [cropDraft],
  );

  const cropRectFromEvent = useCallback(
    (e: Konva.KonvaEventObject<DragEvent>): CropRect | null => {
      const drag = cropDragRef.current;
      if (!drag) return null;
      // Normalised against the BASE rect's own layout, read ONCE per drag
      // rather than the in-flight `next` rect's (r6 S8 review, #723): the
      // source's own on-screen footprint changes AS the crop rect resizes
      // (shrinking the selection zooms in), so re-deriving the scale every
      // tick would make the same pointer distance mean a different amount
      // of crop mid-drag - the same "read the base once" reasoning
      // `startPolygonDrag` already uses above.
      const baseLayout = layoutForCropRect(drag.base);
      if (!baseLayout || baseLayout.source.width <= 0 || baseLayout.source.height <= 0) {
        return null;
      }
      const node = e.target;
      const normDx = (node.x() - drag.origin.x) / baseLayout.source.width;
      const normDy = (node.y() - drag.origin.y) / baseLayout.source.height;
      return drag.anchor
        ? cropRectFromDrag(drag.base, drag.anchor, normDx, normDy)
        : panCropRect(drag.base, normDx, normDy);
    },
    [layoutForCropRect],
  );

  /**
   * Snaps the dragged handle's own Konva node back onto the recomputed
   * point (S3 review), the same reason the polygon handles' own drag does
   * it (`polygonPointsFromDrag` above): react-konva only writes a prop back
   * to the node when its VALUE changed from the last render, so an
   * edge/middle handle constrained to one axis (top-center moves only y,
   * middle-left only x) never gets its OTHER axis corrected once Konva's
   * own free drag has already moved it there on a diagonal pointer move -
   * it strands off to the side instead of tracking the window.
   *
   * Takes `anchor` as an explicit argument rather than reading
   * `cropDragRef.current.anchor` itself: `handleCropDragEnd` below clears
   * that ref before this runs (its own `cancelled` guard needs the drag
   * gone from the ref first), so reading it in here landed on the fallback
   * `{ fx: 0, fy: 0 }` for every drag END and repositioned every handle
   * onto the window's top-left corner instead of its own anchor. Positions
   * against `rect`'s OWN layout (the in-flight/just-committed crop), unlike
   * `cropRectFromEvent`'s base-pinned normalisation above - the handle has
   * to land where THIS tick's window actually is, not where the drag
   * started (r6 S8 review, #723).
   */
  const positionCropHandle = useCallback(
    (e: Konva.KonvaEventObject<DragEvent>, rect: CropRect, anchor: { fx: number; fy: number }) => {
      const layout = layoutForCropRect(rect);
      if (!layout) return;
      e.target.position({
        x: layout.window.x + anchor.fx * layout.window.width,
        y: layout.window.y + anchor.fy * layout.window.height,
      });
    },
    [layoutForCropRect],
  );

  const handleCropDragMove = useCallback(
    (e: Konva.KonvaEventObject<DragEvent>) => {
      const drag = cropDragRef.current;
      const anchor = drag?.anchor ?? { fx: 0, fy: 0 };
      const next = cropRectFromEvent(e);
      if (next) {
        if (drag) drag.lastRect = next;
        setCropDraft(next);
        positionCropHandle(e, next, anchor);
      }
    },
    [cropRectFromEvent, positionCropHandle],
  );

  /**
   * Reads `drag.lastRect`, not a fresh `cropRectFromEvent(e)` off the
   * dragend event's own node (r6 S8 review, #723 - AC-S8-3 never
   * committing). Konva's own `mouseup`/`dragend` handling
   * (`DragAndDrop.js`) never recomputes a dragged node's position - it
   * fires with whatever the node's position CURRENTLY is, which by the
   * time dragend runs is whatever `positionCropHandle` last SET it to (the
   * corrected window-anchored point, in the SAME coordinate space as
   * `drag.origin` but a different UNIT - "how far the window moved"
   * rather than "how far the pointer moved"). Re-deriving `normDx`/`normDy`
   * from that self-written position feeds the previous tick's OUTPUT back
   * in as this tick's INPUT, which very nearly always computes back to the
   * starting rect - the crop silently reverted on every commit. `lastRect`
   * is the actual last correctly-computed rect (from a MOVE tick, whose
   * own node position Konva itself had JUST written from the real pointer,
   * before we overwrote it) - a drag that never moved (a plain click) still
   * lands on `base`, which `startCropDrag` seeds `lastRect` with.
   */
  const handleCropDragEnd = useCallback(() => {
    const drag = cropDragRef.current;
    cropDragRef.current = null;
    if (!drag || drag.cancelled) return;
    setCropDraft(drag.lastRect);
  }, []);

  // The mode itself is what disappears if the layer stops being eligible
  // mid-edit (deleted, locked, hidden) - the same reasoning as the polygon
  // handles' own cleanup effect just above.
  useEffect(() => {
    if (cropEditingLayerId && !cropEditingLayer) {
      setCropEditingLayerId(null);
      setCropDraft(null);
      cropDragRef.current = null;
    }
  }, [cropEditingLayerId, cropEditingLayer]);

  /** Escape climbs one level, and deselects at the top. */
  const selectParentGroup = useCallback(() => {
    const first = Array.from(selectedIds)[0];
    const parent = first ? ancestorsOf(layers, first)[0] : null;
    setSelectedIds(parent ? new Set([parent]) : new Set());
  }, [selectedIds, layers]);

  const enterSelectedGroup = useCallback(() => {
    const first = Array.from(selectedIds)[0];
    const group = first ? layers.find((layer) => layer.id === first) : null;
    if (!group || group.props.kind !== 'group') return;
    const child = group.props.children.find((id) => layers.some((l) => l.id === id));
    if (child) setSelectedIds(new Set([child]));
  }, [selectedIds, layers]);

  const selectAll = useCallback(() => {
    const parented = new Set<string>();
    for (const layer of layers) {
      if (layer.props.kind !== 'group') continue;
      for (const childId of layer.props.children) parented.add(childId);
    }
    setSelectedIds(
      new Set(
        layers
          .filter((layer) => !parented.has(layer.id) && layer.visible && !layer.locked)
          .map((layer) => layer.id),
      ),
    );
  }, [layers]);

  // -- Drag and snap (D38) ---------------------------------------------------

  const handleDragStart = useCallback(
    (id: string) => {
      const roots = selectedIds.has(id) ? selectionRoots : [id];
      const moving = new Set<string>();
      for (const rootId of roots) {
        moving.add(rootId);
        for (const childId of descendantsOf(layers, rootId)) moving.add(childId);
      }
      const start = new Map<string, { x: number; y: number }>();
      for (const layer of layers) {
        if (moving.has(layer.id)) start.set(layer.id, { x: layer.x_mm, y: layer.y_mm });
      }
      dragRef.current = {
        anchorId: id,
        roots: [...roots],
        moving: Array.from(moving),
        start,
        dx: 0,
        dy: 0,
      };
    },
    [selectedIds, selectionRoots, layers],
  );

  /** Ruler guide positions, split by axis, so a drag can also snap to them (D9). */
  const guideSnapTargets = useMemo(
    () => ({
      vertical: rulerGuides
        .filter((g) => g.orientation === 'vertical')
        .map((g) => g.position_mm),
      horizontal: rulerGuides
        .filter((g) => g.orientation === 'horizontal')
        .map((g) => g.position_mm),
    }),
    [rulerGuides],
  );

  /** The axis's own one guide (D21) - what the ruler's x chip draws at. */
  const verticalGuide = useMemo(() => guideForAxis(rulerGuides, 'vertical'), [rulerGuides]);
  const horizontalGuide = useMemo(() => guideForAxis(rulerGuides, 'horizontal'), [rulerGuides]);

  const handleDragMove = useCallback(
    (id: string, rawX: number, rawY: number) => {
      const drag = dragRef.current;
      if (!drag || drag.anchorId !== id) return;
      const anchorStart = drag.start.get(id);
      const layer = layers.find((l) => l.id === id);
      if (!anchorStart || !layer) return;

      // Everything travelling with the pointer is excluded from the snap
      // targets, or a group would latch onto its own children on every pixel.
      const movingSet = new Set(drag.moving);
      const result = computeSnap(
        id,
        rawX,
        rawY,
        layer.width_mm,
        layer.height_mm,
        layers.filter((l) => !movingSet.has(l.id)),
        doc.width_mm,
        doc.height_mm,
        guideSnapTargets,
      );
      drag.dx = result.x_mm - anchorStart.x;
      drag.dy = result.y_mm - anchorStart.y;

      const stage = stageRef.current;
      if (!stage) return;
      for (const memberId of drag.moving) {
        const from = drag.start.get(memberId);
        if (!from) continue;
        const node = stage.findOne(`#${memberId}`);
        if (!node) continue;
        node.x((from.x + drag.dx) * scale);
        node.y((from.y + drag.dy) * scale);
      }
    },
    [layers, computeSnap, doc.width_mm, doc.height_mm, scale, guideSnapTargets],
  );

  const handleDragEnd = useCallback(
    (id: string) => {
      clearGuides();
      const drag = dragRef.current;
      dragRef.current = null;
      if (!drag || drag.anchorId !== id) return;
      if (drag.dx === 0 && drag.dy === 0) return;

      let next = moveLayers(layers, drag.roots, drag.dx, drag.dy);
      for (const rootId of drag.roots) next = refitAncestors(next, rootId);
      commit(next);
    },
    [clearGuides, layers, commit],
  );

  // -- Transform (D38) -------------------------------------------------------

  // ONE Transformer for the whole selection. Attaching happens after render, so
  // the nodes it is given exist and carry the ids `KonvaTagLayer` now sets.
  useEffect(() => {
    const transformer = transformerRef.current;
    const stage = stageRef.current;
    if (!transformer || !stage) return;
    const nodes = Array.from(selectedIds)
      .map((id) => layers.find((layer) => layer.id === id))
      .filter((layer): layer is TagLayer => Boolean(layer) && !layer!.locked && layer!.visible)
      .map((layer) => stage.findOne(`#${layer.id}`))
      .filter((node): node is Konva.Node => Boolean(node));
    transformer.nodes(nodes);
    transformer.getLayer()?.batchDraw();
  }, [selectedIds, layers, scale, view]);

  /**
   * Live reflow while a TEXT layer is being resized (D8, S6).
   *
   * Fires on every `onTransform` tick, not just at the end. A text node's
   * Group sits at a fixed `width`/`height` (the layer's own mm size, in px)
   * until `handleTransformEnd` commits a new one; in between, Konva expresses
   * the drag as a `scale` on that Group, which stretches the fixed-size Text
   * child inside it along with everything else - the font visibly balloons or
   * shrinks for the whole drag and only snaps back to its real size on
   * release. Reading that scale here, folding it into the Group's AND the
   * Text child's own `width`/`height`, and resetting the scale to 1 makes
   * Konva re-wrap the text at its real, UNCHANGED `fontSize` on every frame
   * instead: the box reflows live, the font never moves.
   *
   * Only text nodes get this. A GROUP's own Konva node is just its dashed
   * outline (`KonvaTagLayer`'s children render as separate, flat siblings,
   * not nested inside it) - resizing it live here would touch nothing a user
   * can see, and `transformGroup`'s redistribution to every descendant still
   * has to wait for `handleTransformEnd`, exactly as before.
   *
   * Konva's Transformer fires its own `transform` event once per ATTACHED
   * NODE on every tick, not once (`_fitNodesInto` loops `this._nodes` and
   * fires on each) - so with N selected layers this callback itself runs N
   * times per tick, and each run already loops every selected node again
   * (N4). The guard below collapses that back to one pass: the first call in
   * a tick does the work and schedules a microtask to clear itself, and every
   * other call in the SAME synchronous batch of `_fire`s - which is all of
   * them, since `_fitNodesInto` never yields between them - sees the guard
   * still set and returns immediately.
   */
  const transformTickRef = useRef(false);

  const handleTransform = useCallback(() => {
    if (transformTickRef.current) return;
    transformTickRef.current = true;
    queueMicrotask(() => {
      transformTickRef.current = false;
    });

    const transformer = transformerRef.current;
    if (!transformer) return;

    // Live angle pill while dragging the ROTATE handle (S9): one decimal
    // free, whole degrees once Shift is snapping. Cleared on every other
    // anchor's tick and on `handleTransformEnd` below.
    if (transformer.getActiveAnchor?.() === 'rotater') {
      const rotaterNode = transformer.findOne('.rotater');
      if (rotaterNode) {
        const abs = rotaterNode.getAbsolutePosition();
        const rotationDeg = transformer.rotation();
        const text = `${shiftHeld ? Math.round(rotationDeg) : Math.round(rotationDeg * 10) / 10}°`;
        setRotationLabel({
          x: abs.x - view.panX,
          y: abs.y - view.panY - ROTATION_LABEL_OFFSET_PX,
          text,
        });
      }
    } else {
      setRotationLabel(null);
    }

    for (const node of transformer.nodes()) {
      const layer = layers.find((l) => l.id === node.id());
      if (!layer || layer.props.kind !== 'text') continue;
      const { width, height } = reflowedTextSize(
        node.width(),
        node.height(),
        node.scaleX(),
        node.scaleY(),
      );
      node.scaleX(1);
      node.scaleY(1);
      node.width(width);
      node.height(height);
      // `KonvaTagLayer` always renders a text layer as `<Group><Text/></Group>`,
      // so this cast is safe for exactly the nodes reaching this branch -
      // `findOne` only exists on a Container, and `Konva.Node` (what
      // `transformer.nodes()` is typed as) is not one.
      const textNode = (node as unknown as Konva.Group).findOne('Text');
      if (textNode) {
        // The Text CHILD wraps inside the PADDED box (S3), not the Group's
        // raw one - `KonvaTagLayer`'s own render does the same fold on every
        // commit, so a live drag has to fold it too or the wrap visibly
        // widens for the length of the drag and then snaps back on release.
        const padded = paddedBox(width, height, layer.props.padding, scale);
        textNode.width(padded.width);
        textNode.height(padded.height);
      }
    }
    transformer.getLayer()?.batchDraw();
  }, [layers, scale, shiftHeld, view]);

  const handleTransformEnd = useCallback(() => {
    setRotationLabel(null);
    const transformer = transformerRef.current;
    if (!transformer) return;

    // Read and reset the Konva scale BEFORE touching state: a React updater can
    // run twice, and a second read would see the already-reset scale and undo
    // the resize. `handleTransform` above has already zeroed a text node's
    // scale on the last live tick, so this reads 1 for one - and runs the
    // SAME `reflowedTextSize` fold either way (N3), so text and non-text
    // really do share one commit path rather than a comment merely claiming
    // they do.
    const changes = transformer.nodes().map((node) => {
      const scaleX = node.scaleX();
      const scaleY = node.scaleY();
      node.scaleX(1);
      node.scaleY(1);
      const { width, height } = reflowedTextSize(node.width(), node.height(), scaleX, scaleY);
      return {
        id: node.id(),
        attrs: {
          x_mm: node.x() / scale,
          y_mm: node.y() / scale,
          width_mm: width / scale,
          height_mm: height / scale,
          rotation_deg: node.rotation(),
        },
      };
    });

    let next = layers;
    for (const { id, attrs } of changes) {
      const layer = next.find((l) => l.id === id);
      if (!layer) continue;
      if (layer.props.kind === 'group') {
        next = transformGroup(next, id, attrs);
      } else {
        next = refitAncestors(
          next.map((l) => (l.id === id ? { ...l, ...attrs } : l)),
          id,
        );
      }
    }
    commit(next);
  }, [layers, scale, commit]);

  // -- Ruler guides (D9/D17, S6) ----------------------------------------------

  /** The stage-relative pixel a client (viewport) point falls on. */
  const stagePointFromClient = useCallback((clientX: number, clientY: number) => {
    const element = containerRef.current;
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return {
      x: clientX - rect.left - RULER_THICKNESS,
      y: clientY - rect.top - RULER_THICKNESS,
    };
  }, []);

  /**
   * A guide-drop gesture started on a ruler (D9/D17). The guide is placed (or,
   * if that axis already has one, MOVED - D21, AC-S8-1) IMMEDIATELY, right
   * where the pointer went down - a plain click needs no separate "drop"
   * step - and `guideDragRef` hands the rest of the gesture to the same
   * window-level listener an existing guide's own drag reuses below, so a
   * click that is followed by movement smoothly turns into a pull-out
   * without two code paths to keep in sync.
   */
  const handleGuideStart = useCallback(
    (orientation: RulerGuide['orientation'], event: React.MouseEvent) => {
      // Left button only (D44's own rule, applied here too, S4): the middle
      // button pans and the right button opens no context menu on a ruler,
      // so neither should spawn a guide. `preventDefault` keeps a middle-
      // button press from also triggering the browser's autoscroll cursor.
      if (event.button !== 0) return;
      event.preventDefault();
      const point = stagePointFromClient(event.clientX, event.clientY);
      if (!point) return;
      const { x_mm, y_mm } = stageToMm(view, point.x, point.y);
      const position_mm = orientation === 'vertical' ? x_mm : y_mm;
      // A fresh id in case this turns out to be a placement; a move reuses
      // the axis's existing guide's own id instead (placeOrMoveGuide decides
      // which), and the drag ref below has to track WHICHEVER one it was.
      const freshId = newGuideId();
      const drag = {
        id: freshId,
        orientation,
        moved: false,
        // Spawned FROM the ruler, so the drag starts inside it (B2).
        leftRuler: false,
        downClient: { x: event.clientX, y: event.clientY },
      };
      guideDragRef.current = drag;
      setSelectedGuideId(null);
      setRulerGuides((prev) => {
        // Which guide this gesture is now dragging is decided by the state the
        // update actually runs against, not by a `rulerGuides` snapshot read
        // during render - two ruler clicks in the same tick would otherwise
        // both see "no guide yet" and the second would track an id that never
        // made it into the array. Idempotent, so StrictMode's double-invoked
        // updater is harmless.
        const existing = guideForAxis(prev, orientation);
        if (existing) drag.id = existing.id;
        return placeOrMoveGuide(prev, orientation, freshId, position_mm);
      });
    },
    [stagePointFromClient, view],
  );

  /** An EXISTING guide picked up off the canvas: the same drag, a later start. */
  const handleGuidePointerDown = useCallback(
    (guide: RulerGuide, event: { clientX: number; clientY: number }) => {
      guideDragRef.current = {
        id: guide.id,
        orientation: guide.orientation,
        moved: false,
        // Picked up from somewhere on the canvas, never from inside the
        // ruler strip itself, so this drag has already left ruler territory
        // the moment it starts (B2).
        leftRuler: true,
        downClient: { x: event.clientX, y: event.clientY },
      };
    },
    [],
  );

  // ONE pair of window listeners, always attached, no-op unless a guide is
  // being dragged (`guideDragRef`) - the drag can wander outside the ruler
  // or the Stage's own DOM bounds, which a React-level handler on either
  // would silently drop.
  useEffect(() => {
    const onMove = (event: MouseEvent) => {
      const drag = guideDragRef.current;
      if (!drag) return;
      // The button came up somewhere outside the window - no `mouseup` here
      // to catch it (S3) - so there is no drag left to continue.
      if (event.buttons === 0) {
        guideDragRef.current = null;
        return;
      }
      if (!drag.moved) {
        // The file's own marquee-slop pattern (B2): a real click always
        // wanders a pixel or two between mousedown and mouseup, and without
        // this threshold that jitter alone flipped `moved` on tick one.
        const dx = event.clientX - drag.downClient.x;
        const dy = event.clientY - drag.downClient.y;
        if (Math.hypot(dx, dy) >= MARQUEE_SLOP_PX) drag.moved = true;
      }
      const point = stagePointFromClient(event.clientX, event.clientY);
      if (!point) return;
      // Only once the pointer has genuinely left ruler territory does a later
      // re-entry count as "dragged back onto the ruler" (B2) - see the ref's
      // own doc comment above.
      if (!guideCrossedIntoRuler(drag.orientation, point)) drag.leftRuler = true;
      const { x_mm, y_mm } = stageToMm(view, point.x, point.y);
      setRulerGuides((prev) =>
        moveGuide(prev, drag.id, drag.orientation === 'vertical' ? x_mm : y_mm),
      );
    };

    const onUp = (event: MouseEvent) => {
      const drag = guideDragRef.current;
      if (!drag) return;
      // A plain click - never past the slop, or never having left ruler
      // territory - never counts as "dragged back onto the ruler", or the
      // guide this same gesture just dropped would delete itself (B2).
      const point = stagePointFromClient(event.clientX, event.clientY);
      if (
        drag.moved &&
        drag.leftRuler &&
        point &&
        guideCrossedIntoRuler(drag.orientation, point)
      ) {
        setRulerGuides((prev) => removeGuide(prev, drag.id));
        setSelectedGuideId((id) => (id === drag.id ? null : id));
      } else if (!drag.moved) {
        // A plain click - placing/moving the axis's guide from the ruler, or
        // picking an existing one back up off the canvas without dragging it
        // anywhere - selects it (D21, AC-S8-2), the same way clicking a layer
        // selects the layer. And, the same way, it deselects whatever was
        // selected before (B4): one Delete key, one thing it can remove.
        setSelectedGuideId(drag.id);
        setSelectedIds(new Set());
      }
      guideDragRef.current = null;
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [stagePointFromClient, view]);

  /** The x chip at a guide's own ruler position was clicked (D21, AC-S8-2). */
  const handleGuideRemove = useCallback(
    (orientation: RulerGuide['orientation']) => {
      const target = guideForAxis(rulerGuides, orientation);
      if (!target) return;
      setRulerGuides((prev) => removeGuide(prev, target.id));
      setSelectedGuideId((id) => (id === target.id ? null : id));
    },
    [rulerGuides],
  );

  // Delete/Backspace removes the SELECTED guide (D21, AC-S8-2) - a second
  // removal path alongside the x chip and the drag-back gesture above. Lives
  // beside the layer keyboard shortcuts below rather than its own effect, so
  // there is one keydown listener for the whole canvas, not two racing ones.

  // -- Viewport (D33, D34) ---------------------------------------------------

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      setContainerSize({ width: rect.width, height: rect.height });
    });
    observer.observe(element);
    setContainerSize({ width: element.clientWidth, height: element.clientHeight });
    return () => observer.disconnect();
  }, []);

  const handleFit = useCallback(() => {
    if (stageWidth <= 0 || stageHeight <= 0) return;
    setView(
      fitView(
        { width: stageWidth, height: stageHeight },
        { width_mm: doc.width_mm, height_mm: doc.height_mm },
      ),
    );
  }, [stageWidth, stageHeight, doc.width_mm, doc.height_mm]);

  const handleZoomReset = useCallback(() => {
    setView(
      actualSizeView(
        { width: stageWidth, height: stageHeight },
        { width_mm: doc.width_mm, height_mm: doc.height_mm },
      ),
    );
  }, [stageWidth, stageHeight, doc.width_mm, doc.height_mm]);

  // Fit once, as soon as the container has a size to fit into.
  useEffect(() => {
    if (fittedRef.current || stageWidth <= 0 || stageHeight <= 0) return;
    fittedRef.current = true;
    handleFit();
  }, [stageWidth, stageHeight, handleFit]);

  // -- Side panels (S1, D7) ---------------------------------------------------

  /**
   * `onCollapse`/`onExpand` (unlike `onResize`) fire once on MOUNT too, to
   * announce a collapsible panel's initial state - which happens before the
   * hydration effect below has replaced `panelLayout` with what is actually
   * stored, so persisting unconditionally clobbered the stored left/right/
   * railSplit with plain defaults on every load. Guarded the same way as the
   * resize handlers below, just against "has hydration run" rather than "is a
   * handle being dragged", since a collapse/expand can be a button click.
   */
  const hasHydratedRef = useRef(false);

  /**
   * True only between a handle's own `onDragging(true)` and `onDragging(false)`
   * (react-resizable-panels calls it once per pointer drag). It gates which
   * `onResize` calls are a genuine user action worth persisting.
   *
   * The library ALSO fires `onResize` on its own, outside any drag, whenever
   * the group's real pixel width first becomes known: `defaultSize` is only
   * honoured on a Panel's very first mount, and that first mount happens
   * before the group has been measured (percentages have to come from
   * SOMETHING, so a generic fallback width stands in) - once the real width
   * arrives, the panel's minSize/maxSize percentages are recomputed against
   * it and, if the mount-time percentage now reads as below the real minimum,
   * the library corrects the panel up to it and reports that as a resize.
   * Persisting that correction would floor the STORED width to the minimum
   * on every load, discarding whatever the user actually had it at - which is
   * exactly the bug this ref exists to avoid.
   */
  const draggingHandleRef = useRef(false);

  // Hydrate from localStorage after mount (avoids a hydration mismatch - see
  // the state's own comment). A stored COLLAPSED flag needs an imperative
  // `.collapse()` call here, not just the state update: the panel already
  // mounted expanded (hydration runs after mount, and `defaultSize` is only
  // ever read at mount, before this state existed), so without this the
  // collapsed half of AC-S1-5 would silently not apply on reload even though
  // the value round-trips through storage correctly.
  useEffect(() => {
    const stored = readPanelLayout();
    setPanelLayoutState(stored);
    hasHydratedRef.current = true;
    if (stored.leftCollapsed) leftPanelRef.current?.collapse();
    if (stored.rightCollapsed) rightPanelRef.current?.collapse();
  }, []);

  useEffect(() => {
    const element = panelGroupRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      setPanelGroupSize({ width: rect.width, height: rect.height });
    });
    observer.observe(element);
    setPanelGroupSize({ width: element.clientWidth, height: element.clientHeight });
    return () => observer.disconnect();
  }, []);

  const persistPanelLayout = useCallback((updater: (prev: PanelLayout) => PanelLayout) => {
    setPanelLayoutState((prev) => {
      const next = updater(prev);
      writePanelLayout(next);
      return next;
    });
  }, []);

  // Percentages `react-resizable-panels` wants, converted from the persisted
  // pixel widths against the group's own measured size. Before that size is
  // known, a generic fallback keeps the first paint sane; the ResizeObserver
  // above corrects it a moment later, the same one-frame settle every other
  // measured layout in this file already accepts (`containerSize` does the
  // same at 0x0 until its own observer fires). jsdom never fires it at all
  // (no real layout engine), so this fallback is also what every panel test
  // renders against - a fixed, environment-independent number rather than 0.
  const groupWidth = panelGroupSize.width || 1200;
  const groupHeight = panelGroupSize.height || 600;
  /**
   * Below this, the side columns are `hidden` (AC-S1-7) but still mounted -
   * squeezing the group this narrow can force the library to auto-collapse a
   * panel just to satisfy its own `minSize`, which is a viewport-width
   * artefact, not a choice. `onCollapse`/`onExpand` below skip persisting
   * while the group is this narrow so a phone-width visit does not leave the
   * desktop layout collapsed the next time it is opened wide.
   */
  const isGroupInteractive = groupWidth >= 640;
  const leftPercent = (panelLayout.left / groupWidth) * 100;
  const rightPercent = (panelLayout.right / groupWidth) * 100;
  const leftMinPercent = (LEFT_MIN_PX / groupWidth) * 100;
  const leftMaxPercent = (LEFT_MAX_PX / groupWidth) * 100;
  const rightMinPercent = (RIGHT_MIN_PX / groupWidth) * 100;
  const rightMaxPercent = (RIGHT_MAX_PX / groupWidth) * 100;
  const railPercent = (panelLayout.railSplit / groupHeight) * 100;
  const railMinPercent = (RAIL_MIN_PX / groupHeight) * 100;
  const railMaxPercent = 100 - railMinPercent;

  /**
   * `react-resizable-panels` reads a Panel's `defaultSize` ONCE, at mount -
   * before hydration has replaced `panelLayout.railSplit` with the stored
   * value and before `panelGroupSize.height` is known, so the FIRST
   * `railPercent` is computed against the 600px fallback with the default
   * split. Nothing tells the panel to move once the real values arrive a
   * render later, so the stored split round-trips through localStorage and
   * is never applied (S7, GitHub #676). Fix: once hydration has run AND the
   * group has a REAL measured height, push the panel there imperatively,
   * exactly once - `hasAppliedRailRef` is what keeps a later ResizeObserver
   * tick from fighting a drag the user is mid-way through.
   */
  const hasAppliedRailRef = useRef(false);
  useEffect(() => {
    if (hasAppliedRailRef.current) return;
    if (!hasHydratedRef.current || panelGroupSize.height <= 0) return;
    hasAppliedRailRef.current = true;
    railPanelRef.current?.resize(railPercent);
  }, [panelGroupSize.height, railPercent]);

  const handleLeftResize = useCallback(
    (size: number) => {
      if (!draggingHandleRef.current) return;
      // Dragging PAST the minimum is the library's own collapse gesture, which
      // reports a run of intermediate sizes below `leftMinPercent` on its way
      // to 0 - `onCollapse` below owns that transition, so anything under the
      // real minimum is ignored here rather than clamped and persisted (that
      // would floor the stored width to LEFT_MIN_PX and lose whatever the
      // panel was actually at before the user dragged it shut).
      if (size < leftMinPercent - 0.1) return;
      persistPanelLayout((prev) => ({ ...prev, left: clampLeft((size / 100) * groupWidth) }));
    },
    [groupWidth, leftMinPercent, persistPanelLayout],
  );

  const handleRightResize = useCallback(
    (size: number) => {
      if (!draggingHandleRef.current) return;
      if (size < rightMinPercent - 0.1) return;
      persistPanelLayout((prev) => ({ ...prev, right: clampRight((size / 100) * groupWidth) }));
    },
    [groupWidth, rightMinPercent, persistPanelLayout],
  );

  const handleRailResize = useCallback(
    (size: number) => {
      if (!draggingHandleRef.current) return;
      if (size < railMinPercent - 0.1 || size > railMaxPercent + 0.1) return;
      persistPanelLayout((prev) => ({
        ...prev,
        railSplit: clampRailSplit((size / 100) * groupHeight),
      }));
    },
    [groupHeight, railMinPercent, railMaxPercent, persistPanelLayout],
  );

  // A layout change that came from actually dragging a handle is worth
  // re-centring the artboard for (unlike the collapse toggle, which the
  // chevron buttons drive directly) - the same "Fit to View" the toolbar and
  // Ctrl+0 already trigger.
  const handlePanelDragEnd = useCallback(
    (isDragging: boolean) => {
      draggingHandleRef.current = isDragging;
      if (!isDragging) handleFit();
    },
    [handleFit],
  );

  const handleZoomIn = useCallback(() => {
    setView((v) =>
      zoomAt(v, { x: stageWidth / 2, y: stageHeight / 2 }, ZOOM_BUTTON_FACTOR, ZOOM_LIMITS),
    );
  }, [stageWidth, stageHeight]);

  const handleZoomOut = useCallback(() => {
    setView((v) =>
      zoomAt(v, { x: stageWidth / 2, y: stageHeight / 2 }, 1 / ZOOM_BUTTON_FACTOR, ZOOM_LIMITS),
    );
  }, [stageWidth, stageHeight]);

  // A NATIVE wheel listener with { passive: false }: React's onWheel is passive,
  // so preventDefault there is ignored and the page scrolls instead of zooming.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      const pointer = {
        x: event.clientX - rect.left - RULER_THICKNESS,
        y: event.clientY - rect.top - RULER_THICKNESS,
      };
      setView((v) => zoomAt(v, pointer, Math.pow(1.1, -event.deltaY / 100), ZOOM_LIMITS));
    };
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => element.removeEventListener('wheel', onWheel);
  }, []);

  // -- Stage pointer handling (D35, D36) -------------------------------------

  const isBackground = useCallback((e: Konva.KonvaEventObject<MouseEvent>) => {
    const target = e.target;
    return target === target.getStage() || target.name() === 'artboard-bg';
  }, []);

  const handleStageMouseDown = useCallback(
    (e: Konva.KonvaEventObject<MouseEvent>) => {
      const point = stageRef.current?.getPointerPosition();
      if (!point) return;
      // The middle button pans wherever it is pressed and whatever the tool is,
      // and `preventDefault` keeps the browser's autoscroll cursor away. The
      // right button starts nothing: the context menu resolves its own target.
      if (e.evt.button === 1) {
        e.evt.preventDefault();
        panRef.current = { x: point.x, y: point.y, panX: view.panX, panY: view.panY };
        setWheelPanning(true);
        return;
      }
      if (e.evt.button !== 0) return;
      // Crop mode (S8, AC-S8-3): `e.target` is the Konva node actually hit,
      // same as `isBackground` below reads it. The window and its 8 handles
      // start their OWN Konva drag on this same mousedown, so this only
      // treats it as "outside" when it hit neither of those - a hit on them
      // falls through to Konva's own drag machinery untouched.
      if (cropEditingLayerId) {
        const name = e.target.name();
        if (name === 'crop-window' || name.startsWith('crop-handle-')) return;
        commitCrop();
        return;
      }
      if (handMode) {
        panRef.current = { x: point.x, y: point.y, panX: view.panX, panY: view.panY };
        return;
      }
      if (!isBackground(e)) return;
      const start = stageToMm(view, point.x, point.y);
      marqueeRef.current = { start, additive: e.evt.shiftKey };
      setMarquee(bandBetween(start, start));
    },
    [handMode, view, isBackground, cropEditingLayerId, commitCrop],
  );

  const handleStageMouseMove = useCallback(() => {
    const point = stageRef.current?.getPointerPosition();
    if (!point) return;
    const pan = panRef.current;
    if (pan) {
      setView((v) => ({
        ...v,
        panX: pan.panX + (point.x - pan.x),
        panY: pan.panY + (point.y - pan.y),
      }));
      return;
    }
    const band = marqueeRef.current;
    if (band) setMarquee(bandBetween(band.start, stageToMm(view, point.x, point.y)));
  }, [view]);

  const handleStageMouseUp = useCallback(() => {
    panRef.current = null;
    setWheelPanning(false);
    const band = marqueeRef.current;
    marqueeRef.current = null;
    setMarquee(null);
    if (!band) return;

    const point = stageRef.current?.getPointerPosition();
    const end = point ? stageToMm(view, point.x, point.y) : band.start;
    const rect = bandBetween(band.start, end);
    const dragged =
      rect.width_mm * scale > MARQUEE_SLOP_PX || rect.height_mm * scale > MARQUEE_SLOP_PX;

    if (!dragged) {
      // A click on empty space deselects and leaves the group (D36).
      if (!band.additive) setSelectedIds(new Set());
      setSelectedGuideId(null);
      return;
    }
    const hits = marqueeHits(layers, rect, { insideGroupId });
    setSelectedIds((prev) => (band.additive ? new Set([...prev, ...hits]) : new Set(hits)));
    setSelectedGuideId(null);
  }, [view, scale, layers, insideGroupId]);

  // A mouseup outside the canvas would otherwise leave a band or a pan running.
  useEffect(() => {
    const cancel = () => {
      panRef.current = null;
      setWheelPanning(false);
      if (marqueeRef.current) {
        marqueeRef.current = null;
        setMarquee(null);
      }
    };
    window.addEventListener('mouseup', cancel);
    return () => window.removeEventListener('mouseup', cancel);
  }, []);

  const handleStageDoubleClick = useCallback(
    (e: Konva.KonvaEventObject<MouseEvent>) => {
      if (isBackground(e)) {
        setSelectedIds(new Set());
        setSelectedGuideId(null);
      }
    },
    [isBackground],
  );

  /**
   * Bound on the OUTER container div Radix's trigger wraps (S10, observed
   * bug), not on the Konva `<Stage>` itself.
   *
   * `<Stage onContextMenu>` only fires once Konva's OWN internal listener -
   * bound to `stage.content` specifically - sees the native event; a DOM
   * overlay that sits ALONGSIDE the Stage as this same container's sibling
   * (the preview-block eye chips a few hundred lines down, absolutely
   * positioned right over a layer's own corner) can be the native event's
   * real target instead, in which case Konva's listener never runs at all:
   * `menuOnEmpty` is left at whatever it last was - `true` on the very
   * first right-click of a session - while Radix's OWN trigger (bound to
   * THIS div either way) still opens the menu, showing the empty one. The
   * fix reads the point off THIS event's own `clientX`/`clientY` against
   * the Stage's content div, rather than `stage.getPointerPosition()` -
   * which only reflects whatever Konva's own listener last recorded - so
   * the hit test no longer depends on that listener having run at all.
   *
   * Runs before the Radix trigger despite sharing its element: Radix's own
   * "contextmenu" handling is composed onto this SAME div via `asChild`
   * (React's handler-composition order, not DOM bubbling). It must NOT call
   * preventDefault - the trigger needs this event, and the trigger is what
   * stops the browser's own menu.
   */
  const handleStageContextMenu = useCallback(
    (e: React.MouseEvent) => {
      const content = stageRef.current?.getContent();
      const point = content
        ? (() => {
            const rect = content.getBoundingClientRect();
            return stageToMm(view, e.clientX - rect.left, e.clientY - rect.top);
          })()
        : null;
      const hitId = point ? hitLayerAt(layers, point.x_mm, point.y_mm, entered) : null;
      setMenuOnEmpty(!hitId);
      if (hitId && !selectedIds.has(hitId)) setSelectedIds(new Set([hitId]));
    },
    [view, layers, entered, selectedIds],
  );

  // -- Undo / Redo -----------------------------------------------------------

  const handleUndo = useCallback(() => {
    const restored = history.undo();
    if (restored) {
      setLayers(restored);
      setSelectedIds(new Set());
    }
  }, [history]);

  const handleRedo = useCallback(() => {
    const restored = history.redo();
    if (restored) {
      setLayers(restored);
      setSelectedIds(new Set());
    }
  }, [history]);

  // -- Clipboard (D39) --------------------------------------------------------

  const handleCopy = useCallback(() => {
    if (selectionRoots.length === 0) return;
    const ids = new Set(selectionRoots);
    for (const rootId of selectionRoots) {
      for (const childId of descendantsOf(layers, rootId)) ids.add(childId);
    }
    setTagClipboard({
      layers: structuredClone(layers.filter((layer) => ids.has(layer.id))),
      roots: [...selectionRoots],
      sourceDocId: docId ?? null,
    });
  }, [layers, selectionRoots, docId]);

  const handlePaste = useCallback(() => {
    if (!clipboard || clipboard.layers.length === 0) return;
    // Same doc (or neither side names one, as in a bare test render): offset
    // like a duplicate, as before. A DIFFERENT doc - a copy carried across
    // lines or from the template editor - lands at the original x/y instead,
    // so a layout copied elsewhere reappears in the same place (S3).
    const sameDoc = clipboard.sourceDocId === (docId ?? null);
    const cloned = cloneLayers(
      clipboard.layers,
      clipboard.roots,
      newLayerId,
      sameDoc ? CLONE_OFFSET_MM : 0,
      maxZ,
    );
    if (cloned.layers.length === 0) return;
    commit([...layers, ...cloned.layers], new Set(cloned.ids));
  }, [clipboard, layers, maxZ, commit, docId]);

  const handleCut = useCallback(() => {
    handleCopy();
    deleteSelectedLayers();
  }, [handleCopy, deleteSelectedLayers]);

  // -- Visibility and lock toggles -------------------------------------------

  const handleToggleVisibility = useCallback(
    (id: string) => {
      const layer = layers.find((l) => l.id === id);
      if (layer) updateLayer(id, { visible: !layer.visible });
    },
    [layers, updateLayer],
  );

  const handleToggleLock = useCallback(
    (id: string) => {
      const layer = layers.find((l) => l.id === id);
      if (layer) updateLayer(id, { locked: !layer.locked });
    },
    [layers, updateLayer],
  );

  /**
   * A drag in the Layers panel (D43): one reorder, one history entry.
   *
   * A drop that changes nothing (into its own subtree, or onto a target that is
   * no longer there) answers the same array, and then there is nothing to undo.
   */
  const handleMoveLayer = useCallback(
    (id: string, target: ReparentTarget) => {
      const next = reparentLayer(layers, id, target);
      if (next === layers) return;
      commit(next);
    },
    [layers, commit],
  );

  const selectionLocked = useMemo(
    () =>
      selectionRoots.length > 0 &&
      selectionRoots.every((id) => layers.find((l) => l.id === id)?.locked),
    [selectionRoots, layers],
  );

  const toggleSelectionLock = useCallback(() => {
    if (selectionRoots.length === 0) return;
    const locked = !selectionLocked;
    const ids = new Set(selectionRoots);
    commit(layers.map((layer) => (ids.has(layer.id) ? { ...layer, locked } : layer)));
  }, [selectionRoots, selectionLocked, layers, commit]);

  const hideSelection = useCallback(() => {
    if (selectionRoots.length === 0) return;
    const ids = new Set(selectionRoots);
    commit(layers.map((layer) => (ids.has(layer.id) ? { ...layer, visible: false } : layer)));
  }, [selectionRoots, layers, commit]);

  // -- Keyboard shortcuts ----------------------------------------------------

  useEffect(() => {
    const isFormControl = (node: EventTarget | null): boolean =>
      node instanceof HTMLInputElement ||
      node instanceof HTMLTextAreaElement ||
      node instanceof HTMLSelectElement ||
      (node instanceof HTMLElement && node.isContentEditable);

    const handler = (e: KeyboardEvent) => {
      // The listener is on `window`, so `e.target` is only ever right when
      // the control that has focus is also what dispatched the event. A
      // synthetic or re-targeted keydown (Radix, a portal, a test harness)
      // can hand this handler an `e.target` that is the canvas while the
      // REAL focus - and where Ctrl+A/Delete would actually act - sits on a
      // form control elsewhere in the DOM (an Inspector field, a dialog
      // input). Checking `document.activeElement` too is what keeps that
      // case from having Ctrl+A select every layer, or Delete delete the
      // selection, out from under whatever the user is actually typing in.
      const isInput = isFormControl(e.target) || isFormControl(document.activeElement);
      const modifier = e.ctrlKey || e.metaKey;

      // B/I/U/Shift+X format the selected text layer(s) whether the inline
      // editor has focus or not (AC-S2-4, D4) - checked ahead of the
      // `isInput` guard below, because the inline editor IS a textarea and
      // would otherwise never let these through. That exception is scoped to
      // the inline editor specifically (N2): any OTHER input keeps the normal
      // `isInput` guard, so Cmd+B while typing a name in "Save as template"
      // types a B there instead of bolding the canvas underneath it.
      const formatShortcutsAllowed = !isInput || Boolean(editingLayerId);
      if (formatShortcutsAllowed && modifier && !e.shiftKey && (e.key === 'b' || e.key === 'B')) {
        e.preventDefault();
        applyTextFormat('bold');
        return;
      }
      if (formatShortcutsAllowed && modifier && !e.shiftKey && (e.key === 'i' || e.key === 'I')) {
        e.preventDefault();
        applyTextFormat('italic');
        return;
      }
      if (formatShortcutsAllowed && modifier && !e.shiftKey && (e.key === 'u' || e.key === 'U')) {
        e.preventDefault();
        applyTextFormat('underline');
        return;
      }
      if (formatShortcutsAllowed && modifier && e.shiftKey && (e.key === 'x' || e.key === 'X')) {
        e.preventDefault();
        applyTextFormat('strikethrough');
        return;
      }

      // The inline editor is its own textarea layered over the canvas; while
      // it is open nothing else here may fire (AC-S2-8). Redundant with the
      // `isInput` check right below - which already covers it, since the
      // editor IS a textarea - kept explicit for safety per the plan.
      if (editingLayerId) return;

      if (isInput) return;

      if (e.key === ' ' && !modifier) {
        e.preventDefault();
        setSpaceHeld(true);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        // Crop mode (S8) outranks everything else Escape does: it is its
        // own modal edit, and cancelling it leaves the layer's cropRect
        // exactly as it was, never touched until a commit.
        if (cropEditingLayerId) {
          cancelCropMode();
          return;
        }
        // Before the deselect, which is what unmounts the handles and lets
        // Konva's last `dragend` through (r4d).
        cancelPolygonDrag();
        // Edit-points mode (S5) drops back to select mode first, same as
        // Illustrator/Figma - the shape stays selected, only the vertex/edge
        // handles go away. Escape's older job (step out of a group, or
        // deselect) only runs once there is no mode left to leave.
        if (editingPoints) {
          setEditingPointsId(null);
          return;
        }
        selectParentGroup();
        return;
      }
      // Scopes the two Enter handlers below off a focused BUTTON (review
      // nit) - same `document.activeElement` reasoning as `isInput` above:
      // a Tab-focused button (Save, Publish) firing its OWN native
      // Enter-click would otherwise ALSO hit these and preventDefault it
      // away, just because a crop or an eligible polygon happened to still
      // be selected underneath.
      const enterOnButton = document.activeElement instanceof HTMLButtonElement;
      // Enter commits crop mode (S8, AC-S8-3) before anything else it might
      // otherwise mean.
      if (e.key === 'Enter' && cropEditingLayerId && !enterOnButton) {
        e.preventDefault();
        commitCrop();
        return;
      }
      // Enter toggles edit-points mode (S5) on the eligible selection -
      // in, if select mode is showing the Transformer's anchors; back out,
      // if the vertex/edge handles are already up.
      if (e.key === 'Enter' && cornerHandleLayer && !enterOnButton) {
        e.preventDefault();
        setEditingPointsId(editingPoints ? null : cornerHandleLayer.id);
        return;
      }
      if (!modifier && (e.key === 'v' || e.key === 'V')) {
        setTool('select');
        return;
      }
      if (!modifier && (e.key === 'h' || e.key === 'H')) {
        setTool('hand');
        return;
      }
      if (modifier && e.key === '0') {
        e.preventDefault();
        handleFit();
        return;
      }
      if (modifier && e.key === '1') {
        e.preventDefault();
        handleZoomReset();
        return;
      }

      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedGuideId) {
        // The GUIDE first (B4). Selecting one already clears the layer
        // selection, so this is only ever reached with a guide selected - but
        // asking about the layers first meant that if the two ever DID overlap
        // (as they did before this), Delete silently removed the layers and
        // left the thing the user had just clicked on untouched. Cheap to make
        // the order say what is meant.
        e.preventDefault();
        setRulerGuides((prev) => removeGuide(prev, selectedGuideId));
        setSelectedGuideId(null);
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && selectedIds.size > 0) {
        e.preventDefault();
        deleteSelectedLayers();
      }
      if (e.key === 'z' && modifier && !e.shiftKey) {
        e.preventDefault();
        handleUndo();
      }
      if (e.key === 'z' && modifier && e.shiftKey) {
        e.preventDefault();
        handleRedo();
      }
      if (e.key === 'y' && modifier) {
        e.preventDefault();
        handleRedo();
      }
      if (e.key === 'd' && modifier) {
        e.preventDefault();
        duplicateSelectedLayers();
      }
      if (e.key === 'g' && modifier) {
        e.preventDefault();
        if (e.shiftKey) ungroupSelectedLayers();
        else groupSelectedLayers();
      }
      if (e.key === 'a' && modifier) {
        e.preventDefault();
        selectAll();
      }
      if (e.key === 'c' && modifier) {
        e.preventDefault();
        handleCopy();
      }
      if (e.key === 'x' && modifier) {
        e.preventDefault();
        handleCut();
      }
      if (e.key === 'v' && modifier) {
        e.preventDefault();
        handlePaste();
      }

      // Nudge with arrow keys (0.25mm, 1mm with shift, 0.1mm with alt/option).
      const nudge = e.shiftKey ? NUDGE_MM.shift : e.altKey ? NUDGE_MM.alt : NUDGE_MM.base;
      if (e.key === 'ArrowLeft' && selectedIds.size > 0) {
        e.preventDefault();
        nudgeSelection(-nudge, 0);
      }
      if (e.key === 'ArrowRight' && selectedIds.size > 0) {
        e.preventDefault();
        nudgeSelection(nudge, 0);
      }
      if (e.key === 'ArrowUp' && selectedIds.size > 0) {
        e.preventDefault();
        nudgeSelection(0, -nudge);
      }
      if (e.key === 'ArrowDown' && selectedIds.size > 0) {
        e.preventDefault();
        nudgeSelection(0, nudge);
      }
    };

    const release = (e: KeyboardEvent) => {
      if (e.key === ' ') setSpaceHeld(false);
    };

    window.addEventListener('keydown', handler);
    window.addEventListener('keyup', release);
    return () => {
      window.removeEventListener('keydown', handler);
      window.removeEventListener('keyup', release);
    };
  }, [
    selectedIds,
    selectedGuideId,
    editingLayerId,
    applyTextFormat,
    deleteSelectedLayers,
    duplicateSelectedLayers,
    groupSelectedLayers,
    ungroupSelectedLayers,
    cancelPolygonDrag,
    selectParentGroup,
    selectAll,
    handleUndo,
    handleRedo,
    handleCopy,
    handleCut,
    handlePaste,
    handleFit,
    handleZoomReset,
    nudgeSelection,
    cornerHandleLayer,
    editingPoints,
    cropEditingLayerId,
    cancelCropMode,
    commitCrop,
  ]);

  // A window that loses focus while Space is down would otherwise stay in hand.
  useEffect(() => {
    const clear = () => setSpaceHeld(false);
    window.addEventListener('blur', clear);
    return () => window.removeEventListener('blur', clear);
  }, []);

  // -- Expose current doc for save ------------------------------------------

  /** Call onChange with the current layers state. */
  const save = useCallback(() => {
    onChange({ ...doc, layers });
  }, [doc, layers, onChange]);

  // A host that owns the document (the request designer) hears every change, so
  // switching to another line does not throw the edits away. The template
  // editor passes nothing and keeps its Save-only behaviour.
  useEffect(() => {
    onLayersChange?.(layers);
  }, [layers, onLayersChange]);

  // -- Derived state for toolbar ---------------------------------------------

  const selectedLayer = useMemo(() => {
    if (selectedIds.size === 1) {
      const id = Array.from(selectedIds)[0];
      return layers.find((l) => l.id === id) ?? null;
    }
    return null;
  }, [selectedIds, layers]);

  const selectionIsGroup = selectedLayer?.props.kind === 'group';
  /** The context menu's "Crop image" (S8): an image layer only, entered ONLY
   * from this menu (double-click on an image stays a no-op, captain call 4). */
  const canCropSelectedImage =
    selectedLayer?.props.kind === 'image' && !selectedLayer.locked && selectedLayer.visible;

  const selectedData = selectedLayer ? dataOf(selectedLayer) : null;

  /**
   * D7/AC-S4-1/S4-7: the CANVAS's own tag parts and parent code - `boundData`
   * rather than `selectedData`, so the Layers panel's suffix still shows with
   * nothing selected. Every layer on this canvas is on the same tag, so one
   * binding serves the Inspector's picker AND every row's suffix. Empty/null
   * on a bare product or set block, or in the template editor (no
   * `boundData` at all) - neither carries parts, so the picker never shows
   * (AC-S4-2).
   */
  const subjectParts = boundData?.kind === 'line' ? boundData.line.parts : undefined;
  const subjectParentCode =
    boundData?.kind === 'line'
      ? boundData.line.code
      : boundData?.kind === 'product'
        ? boundData.product.code
        : null;

  /**
   * What the inspector's Content box falls back to when nothing was typed,
   * AND what its "Copy rendered text" preview shows (D21/S16 code review).
   *
   * `layerText` is the same function the print renderer resolves a layer's
   * final text through (`TagSheetRenderer.tsx`) - it is not `resolveSlotText`
   * alone, which answers null for a layer with NO `slot_binding` even when
   * its own `props.text` carries a `{{token}}` (`Made of {{spec.material}}`,
   * D57). `isDynamic` already gates the box to layers that carry a token
   * either way, so the InspectorPanel decides whether to show this; this only
   * has to compute what it would show.
   */
  const selectedResolvedText = selectedLayer
    ? layerText(selectedLayer, selectedData, 'print')
    : null;

  /**
   * The content a given layer's Content box opens on, and where its commit
   * writes to. A slot-bound layer is edited through `text_override` so the
   * binding survives, an unbound one through its own text.
   */
  const contentFor = useCallback(
    (layer: TagLayer) => {
      if (layer.slot_binding) {
        return (
          layer.text_override ??
          resolveSlotText(layer, dataOf(layer)) ??
          (layer.props.kind === 'text' ? layer.props.text : '')
        );
      }
      return layer.props.kind === 'text' ? layer.props.text : '';
    },
    [dataOf],
  );

  /**
   * The content the Insert field dialog opens on, and where Done writes it.
   * Each path is one `setLayers` and one history entry, so a whole dialog's
   * worth of edits undoes in one step.
   */
  const selectedContent = selectedLayer ? contentFor(selectedLayer) : '';

  const writeContentToLayer = useCallback(
    (layerId: string, content: string) => {
      const layer = layers.find((l) => l.id === layerId);
      if (!layer) return;
      if (layer.slot_binding) {
        updateLayer(layer.id, { text_override: content });
      } else if (layer.props.kind === 'text') {
        updateLayerProps(layer.id, { ...layer.props, text: content });
      }
    },
    [layers, updateLayer, updateLayerProps],
  );

  const writeSelectedContent = useCallback(
    (content: string) => {
      if (!selectedLayer) return;
      writeContentToLayer(selectedLayer.id, content);
    },
    [selectedLayer, writeContentToLayer],
  );

  /**
   * The text layer the inline editor is open on - looked up by the id it was
   * opened with, NOT the current selection (S1). Clicking a different text
   * layer while editing moves `selectedLayer`, but the editor stays open on
   * whatever it was double-clicked into until it commits, so it must keep
   * targeting that same layer regardless of where the selection has moved.
   */
  const editingLayer = useMemo(
    () => (editingLayerId ? (layers.find((l) => l.id === editingLayerId) ?? null) : null),
    [editingLayerId, layers],
  );

  /** The seed value the inline editor opened with - stable for the whole edit. */
  const editingContent = editingLayer ? contentFor(editingLayer) : '';

  /**
   * A sole-token layer opens the inline editor on its RESOLVED value instead
   * of the raw token (S3, AC-S3-1): `{{product.code}}` shows
   * `SRTWT8267-GM`, selected, so Cmd/Ctrl+C copies it - reading `{{product.
   * code}}` off the canvas and typing braces into a search box is the bug
   * this closes.
   *
   * Two things have to hold, or the raw content opens exactly as before:
   *  - the whole (trimmed) CONTENT is exactly one token, never mixed text
   *    ("Code {{product.code}}") - there is no single value to substitute.
   *    Content, not `props.text`: a bound layer's content is its
   *    `text_override`, which is exactly where the seeded product block keeps
   *    its `{{product.code}}` (D57), and the first cut of this excluded every
   *    bound layer and so missed the one layer a user actually
   *    double-clicks to copy a code (AC-S3-5);
   *  - something actually resolves it. With no bound data (the template
   *    editor's own preview-less state, AC-S3-4) there is nothing to
   *    substitute and the raw token opens.
   *
   * Decided ONCE per edit session, keyed on the layer the editor opened on:
   * the request designer re-resolves its line data on window focus, and an
   * editor that was read-only when it opened must not turn editable (or
   * change its value) underneath a user who is mid-copy.
   */
  const editingResolvedValue = useMemo(() => {
    if (!editingLayer) return null;
    const content = contentFor(editingLayer);
    if (!soleMergeField(content)) return null;
    const resolved = renderMergeFields(content, dataOf(editingLayer), 'print');
    return resolved && resolved !== content ? resolved.trim() : null;
    // Deliberately keyed on the OPEN alone - see the freeze note above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editingLayerId]);

  /** Mirrors the inline editor's live (uncommitted) text, for the effect below. */
  const editingTextRef = useRef('');

  // Re-seed the live-text mirror on every open, to `editingContent` AS OF
  // this open - otherwise a selection change with nothing typed would flush
  // whatever was left over from a PREVIOUS edit session against the newly
  // opened layer.
  useEffect(() => {
    if (editingLayerId) editingTextRef.current = editingContent;
    // Only re-seed when a NEW edit session opens, not on every keystroke -
    // `editingTextRef` itself is the up-to-date value once one is in progress.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editingLayerId]);

  /**
   * The inline editor's own commit (S2, D5): Esc, Cmd/Ctrl+Enter or blur
   * already decided the text changed (InlineTextEditor's own no-op guard,
   * B2) - write it to the layer that was opened, then close.
   */
  const commitInlineEdit = useCallback(
    (content: string) => {
      if (editingLayerId) writeContentToLayer(editingLayerId, content);
      setEditingLayerId(null);
    },
    [editingLayerId, writeContentToLayer],
  );

  /** The inline editor's own no-op close (B2): nothing was typed, nothing to write. */
  const cancelInlineEdit = useCallback(() => {
    setEditingLayerId(null);
  }, []);

  /**
   * Clicking a DIFFERENT layer while the inline editor is open changes
   * `selectedLayer` - that used to just close the editor without saving
   * (S1), discarding whatever was typed, or landing on whatever layer
   * happened to be selected by the time the write ran. This flushes the
   * text the editor is CURRENTLY showing (`editingTextRef`, kept live by
   * `InlineTextEditor`) against the layer it was opened on
   * (`editingLayerId`), then closes - a genuine commit-then-close, not a
   * silent discard, and it never depends on native blur firing in time.
   */
  useEffect(() => {
    if (!editingLayerId) return;
    if (selectedLayer && selectedLayer.id === editingLayerId) return;
    if (editingTextRef.current !== editingContent) {
      writeContentToLayer(editingLayerId, editingTextRef.current);
    }
    setEditingLayerId(null);
  }, [editingLayerId, selectedLayer, editingContent, writeContentToLayer]);

  /** The bound thing, named the way a person recognises it. Never a UUID. */
  const selectedBindingLabel = describeBindingData(selectedData);

  /** What each previewed block is showing, named for a person (D53). */
  const previewChoices = useMemo(() => {
    const out: Record<string, PreviewChoice> = {};
    for (const [groupId, binding] of Object.entries(previews)) {
      const id = binding.product_id ?? binding.product_set_id;
      const label = describeBindingData(bindings.get(binding));
      if (id && label) out[groupId] = { id, label };
    }
    return out;
  }, [previews, bindings]);

  /**
   * The block the selection sits in, so the Inspector - and the on-canvas eye
   * chip below - can preview THAT block. A child answers for its block:
   * somebody who clicked the code text wants the whole block's product, not
   * a binding on one text layer.
   */
  const selectedBlock = selectedLayer
    ? previewBlockOf(selectedLayer, previewBlocks, groupOfChild)
    : null;

  /** The block the pointer is hovering, if any (D10, S6). */
  const hoveredBlock = useMemo(() => {
    if (!hoveredLayerId) return null;
    const layer = layers.find((l) => l.id === hoveredLayerId);
    return layer ? previewBlockOf(layer, previewBlocks, groupOfChild) : null;
  }, [hoveredLayerId, layers, previewBlocks, groupOfChild]);

  /**
   * Which block's on-canvas eye is showing right now: hovered, then the
   * chip's OWN hover (B1 - keeps it mounted once the pointer has crossed
   * onto it, even after the block's own hover cleared), then selected.
   */
  const activeCanvasEyeBlockId =
    hoveredBlock?.groupId ?? chipHoveredBlockId ?? selectedBlock?.groupId ?? null;

  /** The bound product's photos, for the image picker's first tab. */
  const pickerProductImages = useMemo(() => {
    if (!imagePicker) return [];
    const layer = layers.find((l) => l.id === imagePicker.layerId);
    const data = layer ? dataOf(layer) : null;
    return data?.kind === 'product' ? data.product.images : [];
  }, [imagePicker, layers, dataOf]);

  // -- Render ----------------------------------------------------------------

  /**
   * The whole-tag eye's on-canvas position (S5). Anchored to the frame's own
   * top-right corner, but CLAMPED into the viewport: a pan or zoom that
   * carries that corner above the ruler or off either side must not bury the
   * one control that reaches the whole-tag preview.
   */
  const wholeTagEyePosition = useMemo(() => {
    const rawLeft = RULER_THICKNESS + view.panX + canvasWidthPx - 28;
    const rawTop = RULER_THICKNESS + view.panY - 28;
    const maxLeft = Math.max(RULER_THICKNESS + 2, containerSize.width - 26);
    return {
      left: Math.min(Math.max(rawLeft, RULER_THICKNESS + 2), maxLeft),
      top: Math.max(rawTop, RULER_THICKNESS + 2),
    };
  }, [view.panX, view.panY, canvasWidthPx, containerSize.width]);

  const sortedLayers = useMemo(
    () => [...layers].sort((a, b) => a.z_index - b.z_index),
    [layers],
  );

  /** Layers a resize left partly or wholly past the artboard edge (S4). */
  const overflowingIds = useMemo(
    () =>
      new Set(
        layers
          .filter((layer) => layerOverflowsArtboard(layer, doc))
          .map((layer) => layer.id),
      ),
    [layers, doc],
  );

  const hasSelection = selectedIds.size > 0;
  const canEnterGroup = selectionIsGroup;
  const canSelectParent = insideGroupId !== null;

  return (
    <div className="flex h-full flex-col overflow-x-hidden">
      {/* Toolbar */}
      <CanvasToolbar
        tool={tool}
        onToolChange={setTool}
        onAddText={handleAddText}
        onAddShape={handleAddShape}
        onAddImage={handleAddImage}
        onAddProductSlot={handleAddProductSlot}
        onAddPriceBadge={handleAddPriceBadge}
        onAddBadge={handleAddBadge}
        onAddBarcode={handleAddBarcode}
        onAddProduct={() => setPicker({ kind: 'add-product' })}
        onAddSet={() => setPicker({ kind: 'add-set' })}
        onAddAlternativesRow={() => setPicker({ kind: 'alternatives' })}
        onAddAccessoriesStrip={() => setPicker({ kind: 'accessories' })}
        onUndo={handleUndo}
        onRedo={handleRedo}
        canUndo={history.canUndo}
        canRedo={history.canRedo}
        zoom={view.zoom}
        onZoomIn={handleZoomIn}
        onZoomOut={handleZoomOut}
        onZoomReset={handleZoomReset}
        onFit={handleFit}
        onDeleteSelected={deleteSelectedLayers}
        onDuplicateSelected={duplicateSelectedLayers}
        onGroupSelected={groupSelectedLayers}
        onUngroupSelected={ungroupSelectedLayers}
        hasSelection={hasSelection}
        hasMultiSelection={selectedIds.size >= 2}
        selectionIsGroup={selectionIsGroup}
        trailing={toolbarTrailing}
      />

      <div ref={panelGroupRef} className="flex flex-1 overflow-hidden">
        {/* Left column collapsed strip: rendered OUTSIDE the panel so it stays
            clickable while the panel itself is at collapsedSize={0}. */}
        {panelLayout.leftCollapsed && (
          <button
            type="button"
            className="hidden w-6 shrink-0 items-start justify-center border-r bg-muted/30 pt-2 hover:bg-muted md:flex"
            title="Expand Lines + Layers"
            aria-label="Expand Lines + Layers"
            onClick={() => {
              leftPanelRef.current?.expand(leftPercent);
              handleFit();
            }}
          >
            <ChevronRight className="size-3.5" />
          </button>
        )}

        <ResizablePanelGroup direction="horizontal" className="flex-1">
          {/* Left sidebar: the host's rail, then the Layers panel (AC-S1-1, AC-S1-3). */}
          <ResizablePanel
            ref={leftPanelRef}
            id="canvas-left"
            order={1}
            collapsible
            collapsedSize={0}
            minSize={leftMinPercent}
            maxSize={leftMaxPercent}
            defaultSize={panelLayout.leftCollapsed ? 0 : leftPercent}
            onResize={handleLeftResize}
            onCollapse={() => {
              if (!hasHydratedRef.current || !isGroupInteractive) return;
              persistPanelLayout((prev) => ({ ...prev, leftCollapsed: true }));
            }}
            onExpand={() => {
              if (!hasHydratedRef.current || !isGroupInteractive) return;
              persistPanelLayout((prev) => ({ ...prev, leftCollapsed: false }));
            }}
            className="relative hidden md:flex md:flex-col md:overflow-hidden"
          >
            <button
              type="button"
              className="absolute right-1 top-1 z-10 flex size-5 items-center justify-center rounded bg-background/80 text-muted-foreground hover:bg-accent hover:text-foreground"
              title="Collapse Lines + Layers"
              aria-label="Collapse Lines + Layers"
              onClick={() => {
                leftPanelRef.current?.collapse();
                handleFit();
              }}
            >
              <ChevronLeft className="size-3.5" />
            </button>
            {leftRail ? (
              <ResizablePanelGroup direction="vertical" className="h-full">
                <ResizablePanel
                  ref={railPanelRef}
                  id="canvas-left-rail"
                  order={1}
                  minSize={railMinPercent}
                  maxSize={railMaxPercent}
                  defaultSize={railPercent}
                  onResize={handleRailResize}
                  className="flex flex-col"
                >
                  {/* D9: no scroll on this wrapper any more - LINES (the
                      rail's own `flex-1` child) scrolls itself, and TAG SIZE
                      sits below it, both always in view. */}
                  <div className="flex h-full flex-col">{leftRail}</div>
                </ResizablePanel>
                <ResizableHandle
                  withHandle
                  onDragging={handlePanelDragEnd}
                  aria-label="Resize Lines and Layers"
                />
                <ResizablePanel id="canvas-left-layers" order={2} minSize={railMinPercent} className="min-h-0">
                  <LayersPanel
                    layers={layers}
                    selectedIds={selectedIds}
                    onSelect={handleSelect}
                    onToggleVisibility={handleToggleVisibility}
                    onToggleLock={handleToggleLock}
                    onMoveLayer={handleMoveLayer}
                    overflowingIds={overflowingIds}
                    subjectParts={subjectParts}
                  />
                </ResizablePanel>
              </ResizablePanelGroup>
            ) : (
              <div className="min-h-0 flex-1">
                <LayersPanel
                  layers={layers}
                  selectedIds={selectedIds}
                  onSelect={handleSelect}
                  onToggleVisibility={handleToggleVisibility}
                  onToggleLock={handleToggleLock}
                  onMoveLayer={handleMoveLayer}
                  overflowingIds={overflowingIds}
                  subjectParts={subjectParts}
                />
              </div>
            )}
          </ResizablePanel>
          <ResizableHandle
            withHandle
            className="hidden md:flex"
            onDragging={handlePanelDragEnd}
            aria-label="Resize Lines and Layers panel"
          />

          {/* Centre: canvas workspace. The Stage fills it and the artboard sits at
              a pan offset inside (D33), so there is nothing to scroll. */}
          <ResizablePanel id="canvas-centre" order={2} className="flex">
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div
              ref={containerRef}
              className={cn(
                'relative flex-1 overflow-hidden bg-muted/50',
                handMode && 'cursor-grab active:cursor-grabbing',
                wheelPanning && 'cursor-grabbing',
              )}
              onContextMenu={handleStageContextMenu}
            >
              <CanvasRulers
                widthMm={doc.width_mm}
                heightMm={doc.height_mm}
                scale={scale}
                originX={view.panX}
                originY={view.panY}
                viewportWidth={stageWidth}
                viewportHeight={stageHeight}
                onGuideStart={handleGuideStart}
                verticalGuideMm={verticalGuide?.position_mm ?? null}
                horizontalGuideMm={horizontalGuide?.position_mm ?? null}
                onGuideRemove={handleGuideRemove}
              />

              <div
                className="absolute"
                style={{ left: RULER_THICKNESS, top: RULER_THICKNESS }}
              >
                <Stage
                  ref={stageRef as React.RefObject<Konva.Stage>}
                  // Never 0 (#726). A Konva stage sizes its BUFFER canvas from
                  // its own width/height, and any shape drawn with a fill, a
                  // stroke and an absolute opacity below 1 composites through
                  // that buffer (`Shape._useBufferCanvas`): Konva then calls
                  // `drawImage(bufferCanvas)`, which throws `InvalidStateError:
                  // the image argument is a canvas element with a width or
                  // height of 0` and takes the whole route down through the
                  // error boundary. The ghost pass (S4) draws exactly such a
                  // shape - a translucent copy at 30% opacity - so any template
                  // with a layer past the artboard edge crashed the moment the
                  // stage measured 0. Which is not hypothetical: the template
                  // page keeps this editor MOUNTED and merely `hidden` while
                  // the version viewer is up (its own comment explains why),
                  // and `display:none` makes the ResizeObserver report 0x0. So
                  // Versions > View crashed every time on such a template.
                  // `stageWidth`/`stageHeight` themselves stay honest - the
                  // fit-to-view effects use `<= 0` to mean "not measured yet".
                  width={Math.max(1, stageWidth)}
                  height={Math.max(1, stageHeight)}
                  onMouseDown={handleStageMouseDown}
                  onMouseMove={handleStageMouseMove}
                  onMouseUp={handleStageMouseUp}
                  onDblClick={handleStageDoubleClick}
                >
                  <KonvaLayer x={view.panX} y={view.panY}>
                    {/* White tag background */}
                    <Rect
                      name="artboard-bg"
                      x={0}
                      y={0}
                      width={canvasWidthPx}
                      height={canvasHeightPx}
                      fill="#ffffff"
                      stroke="#d4d4d8"
                      strokeWidth={1}
                    />

                    {/* Ghost pass (S4, fixed #720): a layer a resize left
                        partly or wholly past the artboard edge, drawn again
                        here UNCLIPPED at 30% opacity so its outside part
                        stays visible instead of vanishing behind the clip
                        below - fully interactive (AC-S4-1: clicked, dragged
                        back inside, transformed), not decorative. Its Konva
                        id stays distinct from the real layer's (`${id}-
                        ghost`, so `stage.findOne`/a test's `getByTestId`
                        never collide with the clipped copy below), but
                        `interactionId` routes every callback to the REAL
                        layer, so selecting or dragging the ghost acts on it.
                        The Transformer still attaches to the clipped copy's
                        own node either way (real id, unaffected by any of
                        this) - it renders outside any clip in the JSX tree
                        regardless of which node it is attached to, so its
                        handles draw correctly past the artboard edge. */}
                    {sortedLayers
                      .filter((layer) => overflowingIds.has(layer.id))
                      .map((layer) => (
                        <KonvaTagLayer
                          key={`${layer.id}-ghost`}
                          layer={{ ...withPolygonPreview(layer), id: `${layer.id}-ghost` }}
                          scale={scale}
                          display={layerDisplay(layer, dataOf(layer), library.assetUrls)}
                          draggable={!handMode}
                          listening={
                            !handMode &&
                            !(layer.props.kind === 'group' && entered.has(layer.id))
                          }
                          // Crop mode (S8, r6 S8 review, #723): same reason
                          // as the clipped copy below - its own overlay
                          // already draws this layer, correctly.
                          opacity={layer.id === cropEditingLayerId ? 0 : 0.3}
                          interactionId={layer.id}
                          onSelect={handleCanvasSelect}
                          onDoubleClick={handleLayerDoubleClick}
                          onDragStart={handleDragStart}
                          onDragMove={handleDragMove}
                          onDragEnd={handleDragEnd}
                          onHoverChange={handleLayerHoverChange}
                        />
                      ))}

                    {/* Layers, clipped to the artboard (S9 review S4): a
                        layer dragged or resized past the tag's own edge is
                        hidden on screen exactly the way TagSheetRenderer's
                        `overflow: hidden` clips it on the printed sheet -
                        WYSIWYG after a shrink, not a canvas that still shows
                        what the PDF will not. The ghost pass above adds back
                        the outside part at reduced opacity for an
                        overflowing layer, ALSO interactive (#720); this copy
                        is unchanged, keeps the real Konva id, and is what
                        the Transformer attaches to either way. */}
                    <Group
                      clipFunc={(ctx) => {
                        ctx.rect(0, 0, canvasWidthPx, canvasHeightPx);
                      }}
                    >
                      {sortedLayers.map((layer) => (
                        <KonvaTagLayer
                          key={layer.id}
                          layer={withPolygonPreview(layer)}
                          scale={scale}
                          display={layerDisplay(layer, dataOf(layer), library.assetUrls)}
                          draggable={!handMode}
                          listening={
                            !handMode &&
                            !(layer.props.kind === 'group' && entered.has(layer.id))
                          }
                          // Crop mode (S8, r6 S8 review, #723) draws its own
                          // overlay for this SAME layer, at the correct
                          // transform - the layer's own render has to hide
                          // for as long as that is up, or the two show at
                          // once and the picture looks doubled.
                          opacity={layer.id === cropEditingLayerId ? 0 : undefined}
                          onSelect={handleCanvasSelect}
                          onDoubleClick={handleLayerDoubleClick}
                          onDragStart={handleDragStart}
                          onDragMove={handleDragMove}
                          onDragEnd={handleDragEnd}
                          onHoverChange={handleLayerHoverChange}
                        />
                      ))}
                    </Group>

                    {/* Snap guides */}
                    {guides.map((g, i) =>
                      g.orientation === 'vertical' ? (
                        <Line
                          key={`guide-v-${i}`}
                          points={[
                            g.position_mm * scale,
                            0,
                            g.position_mm * scale,
                            canvasHeightPx,
                          ]}
                          stroke="#f43f5e"
                          strokeWidth={0.5}
                          dash={[4, 4]}
                          listening={false}
                        />
                      ) : (
                        <Line
                          key={`guide-h-${i}`}
                          points={[
                            0,
                            g.position_mm * scale,
                            canvasWidthPx,
                            g.position_mm * scale,
                          ]}
                          stroke="#f43f5e"
                          strokeWidth={0.5}
                          dash={[4, 4]}
                          listening={false}
                        />
                      ),
                    )}

                    {/* Ruler guides (D9/D17, S6; D21 - one per axis, S8).
                        Dotted, and a different colour from the transient
                        snap guides above so the two are never confused -
                        these are placed on purpose and stay until removed.
                        `listening={!handMode}` (S2) so the hand tool can pan
                        through them like everything else, and a narrower
                        `hitStrokeWidth` so a guide crossing a layer does not
                        turn a whole strip of that layer unselectable. A
                        SELECTED guide (D21, AC-S8-2) draws solid and thicker
                        instead of dashed, so Delete/Backspace has a visible
                        target to confirm against. */}
                    {rulerGuides.map((g) =>
                      g.orientation === 'vertical' ? (
                        <Line
                          key={g.id}
                          points={[g.position_mm * scale, 0, g.position_mm * scale, canvasHeightPx]}
                          stroke="#0ea5e9"
                          strokeWidth={g.id === selectedGuideId ? 2 : 1}
                          dash={g.id === selectedGuideId ? undefined : [2, 3]}
                          listening={!handMode}
                          hitStrokeWidth={4}
                          onMouseDown={(e) => {
                            e.cancelBubble = true;
                            handleGuidePointerDown(g, e.evt);
                          }}
                        />
                      ) : (
                        <Line
                          key={g.id}
                          points={[0, g.position_mm * scale, canvasWidthPx, g.position_mm * scale]}
                          stroke="#0ea5e9"
                          strokeWidth={g.id === selectedGuideId ? 2 : 1}
                          dash={g.id === selectedGuideId ? undefined : [2, 3]}
                          listening={!handMode}
                          hitStrokeWidth={4}
                          onMouseDown={(e) => {
                            e.cancelBubble = true;
                            handleGuidePointerDown(g, e.evt);
                          }}
                        />
                      ),
                    )}

                    {/* ONE Transformer, after every layer, for the selection. */}
                    <Transformer
                      ref={transformerRef}
                      // EDIT-POINTS mode (S5 review) hides the WHOLE
                      // Transformer, rotate handle included - the plan calls
                      // for the anchors gone while the vertex/edge handles
                      // are up, and a rotate stalk sitting on top of them
                      // reads as the Transformer still being "on" rather
                      // than having handed off to the shape's own points.
                      rotateEnabled={!editingPoints}
                      // Shift keeps a corner drag's aspect ratio (side
                      // anchors are unaffected by `keepRatio` regardless);
                      // Shift also arms the rotation snap list below (S9).
                      keepRatio={shiftHeld}
                      rotationSnaps={shiftHeld ? ROTATION_SNAPS : []}
                      rotationSnapTolerance={ROTATION_SNAP_TOLERANCE_DEG}
                      listening={!handMode}
                      onTransform={handleTransform}
                      onTransformEnd={handleTransformEnd}
                      // A polygon/boxed badge in EDIT-POINTS mode (S5) shows
                      // no resize anchors at all: a resize anchor sits
                      // exactly where a corner handle sits, and the anchor
                      // would win every click meant for the corner. In
                      // SELECT mode `polygonHandles` is null (S5 gates it on
                      // `editingPoints` too), so the full anchor set below
                      // shows instead - points stay normalised 0-1, so a box
                      // resize scales the shape with no maths change. Crop
                      // mode (S8) hides every resize anchor the same way -
                      // the crop window's own handles own the drag while it
                      // is open.
                      enabledAnchors={
                        polygonHandles || cropEditingLayerId
                          ? []
                          : [
                              'top-left',
                              'top-right',
                              'bottom-left',
                              'bottom-right',
                              'middle-left',
                              'middle-right',
                              'top-center',
                              'bottom-center',
                            ]
                      }
                      boundBoxFunc={(_oldBox, newBox) => {
                        // Minimum size = 2mm in pixels.
                        const minSize = 2 * scale;
                        if (newBox.width < minSize || newBox.height < minSize) {
                          return {
                            ...newBox,
                            width: Math.max(newBox.width, minSize),
                            height: Math.max(newBox.height, minSize),
                          };
                        }
                        return newBox;
                      }}
                    />

                    {/* Live angle pill while dragging the rotate handle
                        (S9); gone as soon as the drag ends or moves to a
                        different anchor. */}
                    {rotationLabel && (
                      <KonvaLabel
                        x={rotationLabel.x}
                        y={rotationLabel.y}
                        listening={false}
                      >
                        <KonvaLabelTag fill="#3b82f6" cornerRadius={4} />
                        <KonvaText
                          text={rotationLabel.text}
                          fontSize={11}
                          padding={4}
                          fill="#ffffff"
                        />
                      </KonvaLabel>
                    )}

                    {/* Polygon corner handles (S4): a circle on every
                        corner and a smaller square on every edge midpoint,
                        inside a Group carrying the layer's own transform so
                        they stay on the shape when it is rotated. Edges are
                        drawn first so a corner handle wins an overlapping
                        click. */}
                    {polygonHandles && (
                      <Group
                        x={polygonHandles.layer.x_mm * scale}
                        y={polygonHandles.layer.y_mm * scale}
                        rotation={polygonHandles.layer.rotation_deg}
                      >
                        {polygonHandles.edges.map((point, index) => (
                          <Rect
                            key={`polygon-edge-${index}`}
                            name={`polygon-edge-${index}`}
                            x={point.x}
                            y={point.y}
                            width={POLYGON_EDGE_HANDLE_SIZE_PX}
                            height={POLYGON_EDGE_HANDLE_SIZE_PX}
                            offsetX={POLYGON_EDGE_HANDLE_SIZE_PX / 2}
                            offsetY={POLYGON_EDGE_HANDLE_SIZE_PX / 2}
                            fill="#ffffff"
                            stroke="#3b82f6"
                            strokeWidth={1}
                            draggable={!handMode}
                            onDragStart={() => startPolygonDrag('edge', index)}
                            onDragMove={handlePolygonDragMove}
                            onDragEnd={handlePolygonDragEnd}
                          />
                        ))}
                        {polygonHandles.vertices.map((point, index) => (
                          <Circle
                            key={`polygon-vertex-${index}`}
                            name={`polygon-vertex-${index}`}
                            x={point.x}
                            y={point.y}
                            radius={POLYGON_VERTEX_HANDLE_RADIUS_PX}
                            fill="#ffffff"
                            stroke="#3b82f6"
                            strokeWidth={1.5}
                            draggable={!handMode}
                            onDragStart={() => startPolygonDrag('vertex', index)}
                            onDragMove={handlePolygonDragMove}
                            onDragEnd={handlePolygonDragEnd}
                            onMouseEnter={(e) => {
                              const stage = e.target.getStage();
                              if (stage) stage.container().style.cursor = 'crosshair';
                            }}
                            onMouseLeave={(e) => {
                              const stage = e.target.getStage();
                              if (stage) stage.container().style.cursor = 'default';
                            }}
                          />
                        ))}
                      </Group>
                    )}

                    {/* Crop mode (S8, r6 S8 review, #723): the whole source
                        at 40% opacity, the crop window bright on top of it -
                        the SAME image drawn a second time at the SAME
                        transform (`cropLayout.source`), opaque, clipped to
                        the window (`cropLayout.window`); the opaque draw
                        always wins there regardless of paint order (S4
                        review used the same reasoning for the ghost pass),
                        so the two never fight over how that part looks. One
                        shared layout for both passes is the whole fix here -
                        two independently-derived ones (the old always-
                        CONTAIN whole-image frame the bright window carved a
                        fraction out of) agreed with each other but not with
                        what the layer actually draws, so the picture looked
                        doubled. The layer's own render is hidden below
                        (`opacity={0}` on its `KonvaTagLayer`) for as long as
                        this is up, so the two never both show at once. */}
                    {cropEditingLayer && cropImage && cropLayout && cropDraft && (
                      <Group
                        x={cropEditingLayer.x_mm * scale}
                        y={cropEditingLayer.y_mm * scale}
                        rotation={cropEditingLayer.rotation_deg}
                      >
                        <KonvaImage
                          image={cropImage}
                          x={cropLayout.source.x}
                          y={cropLayout.source.y}
                          width={cropLayout.source.width}
                          height={cropLayout.source.height}
                          opacity={0.4}
                          listening={false}
                        />
                        <Group
                          clipFunc={(ctx) => {
                            ctx.rect(
                              cropLayout.window.x,
                              cropLayout.window.y,
                              cropLayout.window.width,
                              cropLayout.window.height,
                            );
                          }}
                        >
                          <KonvaImage
                            image={cropImage}
                            x={cropLayout.source.x}
                            y={cropLayout.source.y}
                            width={cropLayout.source.width}
                            height={cropLayout.source.height}
                            listening={false}
                          />
                        </Group>
                        <Rect
                          x={cropLayout.window.x}
                          y={cropLayout.window.y}
                          width={cropLayout.window.width}
                          height={cropLayout.window.height}
                          stroke="#3b82f6"
                          strokeWidth={1.5}
                          listening={false}
                        />
                        {/* Dragging INSIDE the window pans it (AC-S8-2).
                            Transparent so the dimmed/bright split above
                            still shows through; drawn before the handles so
                            a handle wins an overlapping click. */}
                        <Rect
                          name="crop-window"
                          x={cropLayout.window.x}
                          y={cropLayout.window.y}
                          width={cropLayout.window.width}
                          height={cropLayout.window.height}
                          fill="transparent"
                          draggable={!handMode}
                          onDragStart={(e) => startCropDrag(null, { x: e.target.x(), y: e.target.y() })}
                          onDragMove={handleCropDragMove}
                          onDragEnd={handleCropDragEnd}
                        />
                        {CROP_HANDLE_ANCHORS.map((anchor) => {
                          const hx = cropLayout.window.x + anchor.fx * cropLayout.window.width;
                          const hy = cropLayout.window.y + anchor.fy * cropLayout.window.height;
                          return (
                            <Rect
                              key={`crop-handle-${anchor.name}`}
                              name={`crop-handle-${anchor.name}`}
                              x={hx}
                              y={hy}
                              width={CROP_HANDLE_SIZE_PX}
                              height={CROP_HANDLE_SIZE_PX}
                              offsetX={CROP_HANDLE_SIZE_PX / 2}
                              offsetY={CROP_HANDLE_SIZE_PX / 2}
                              fill="#ffffff"
                              stroke="#3b82f6"
                              strokeWidth={1.5}
                              draggable={!handMode}
                              onDragStart={() => startCropDrag(anchor, { x: hx, y: hy })}
                              onDragMove={handleCropDragMove}
                              onDragEnd={handleCropDragEnd}
                            />
                          );
                        })}
                      </Group>
                    )}

                    {/* Marquee band */}
                    {marquee && (
                      <Rect
                        x={marquee.x_mm * scale}
                        y={marquee.y_mm * scale}
                        width={marquee.width_mm * scale}
                        height={marquee.height_mm * scale}
                        fill="#3b82f6"
                        opacity={0.12}
                        stroke="#3b82f6"
                        strokeWidth={1}
                        listening={false}
                      />
                    )}
                  </KonvaLayer>
                </Stage>
              </div>

              {/* Per-block preview eyes (D10, S6): hover or select a
                  previewable block to reveal its eye, right on the block. */}
              {previewBlocks.map((block) => {
                if (activeCanvasEyeBlockId !== block.groupId) return null;
                const layer = layers.find((l) => l.id === block.groupId);
                if (!layer) return null;
                return (
                  <PreviewEyeButton
                    key={block.groupId}
                    label={
                      previewChoices[block.groupId]
                        ? `Previewing ${previewChoices[block.groupId].label}`
                        : `Preview ${block.label}`
                    }
                    active={Boolean(previewChoices[block.groupId])}
                    style={{
                      left:
                        RULER_THICKNESS +
                        view.panX +
                        (layer.x_mm + layer.width_mm) * scale -
                        24,
                      top: RULER_THICKNESS + view.panY + layer.y_mm * scale + 4,
                    }}
                    onClick={() => openBlockPreview(block.groupId)}
                    onMouseEnter={() => handleChipHoverChange(block.groupId, true)}
                    onMouseLeave={() => handleChipHoverChange(block.groupId, false)}
                  />
                );
              })}

              {/* The whole-tag eye, on the frame itself (D10, AC-S6-5): one
                  product choice resolves every loose bound layer at once.
                  Always visible when eligible, unlike a block's own eye -
                  there is no single layer on the tag to hover for it.
                  Clamped into the viewport (S5) so a pan or zoom that carries
                  the frame's own corner off-screen cannot bury it. */}
              {wholeTagPreviewBlock && (
                <>
                  <PreviewEyeButton
                    label={
                      previewChoices[WHOLE_TAG_BLOCK_ID]
                        ? `Previewing ${previewChoices[WHOLE_TAG_BLOCK_ID].label}`
                        : 'Preview the whole tag'
                    }
                    active={Boolean(previewChoices[WHOLE_TAG_BLOCK_ID])}
                    style={wholeTagEyePosition}
                    onClick={() => openBlockPreview(WHOLE_TAG_BLOCK_ID)}
                  />
                  {/* Clear affordance (S1), mirroring PreviewBlockInspector's
                      own X: a whole-tag preview is otherwise only clearable
                      from the Inspector, which is not on screen for whatever
                      layer is currently selected. */}
                  {previewChoices[WHOLE_TAG_BLOCK_ID] && (
                    <button
                      type="button"
                      className="absolute z-20 flex h-3.5 w-3.5 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-sm hover:bg-accent hover:text-foreground"
                      style={{
                        left: wholeTagEyePosition.left - 6,
                        top: wholeTagEyePosition.top - 6,
                      }}
                      title="Stop previewing the whole tag"
                      aria-label="Stop previewing the whole tag"
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => {
                        e.stopPropagation();
                        clearBlockPreview(WHOLE_TAG_BLOCK_ID);
                      }}
                    >
                      <X className="size-2" />
                    </button>
                  )}
                </>
              )}

              {/* The salesperson's pinned change requests (r9 S2/D6), in the
                  SAME coordinate space as the inline editor below: the
                  artboard IS this line's tag, so a comment's fractions land
                  on the part of the tag it was pinned to at any zoom or pan.
                  A DOM overlay rather than Konva nodes, so a click can open
                  the comment without ever reaching the stage's selection. */}
              {(reviewPins ?? []).map((pin) => {
                const left = RULER_THICKNESS + view.panX + pin.x * canvasWidthPx;
                const top = RULER_THICKNESS + view.panY + pin.y * canvasHeightPx;
                return (
                  <div key={pin.id}>
                    {pin.w > 0 && pin.h > 0 && (
                      <div
                        className={cn(
                          'pointer-events-none absolute z-20 rounded-sm border-2',
                          pin.resolved
                            ? 'border-muted-foreground/40 bg-muted-foreground/10'
                            : 'border-primary bg-primary/10',
                        )}
                        style={{
                          left,
                          top,
                          width: pin.w * canvasWidthPx,
                          height: pin.h * canvasHeightPx,
                        }}
                      />
                    )}
                    <button
                      type="button"
                      data-testid={`canvas-pin-${pin.id}`}
                      aria-label={`Change request ${pin.number}`}
                      className={cn(
                        'absolute z-20 flex size-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-2 border-background text-2xs font-semibold text-white shadow',
                        pin.resolved ? 'bg-muted-foreground/60' : 'bg-primary',
                      )}
                      style={{ left, top }}
                      onMouseDown={(event) => event.stopPropagation()}
                      onClick={(event) => {
                        event.stopPropagation();
                        setOpenPinId((current) =>
                          current === pin.id ? null : pin.id,
                        );
                      }}
                    >
                      {pin.number}
                    </button>
                    {openPinId === pin.id && (
                      <div
                        // A comment bubble, not a static box: the role is what
                        // lets it carry the mousedown guard that keeps a click
                        // inside it away from the canvas's own selection.
                        role="note"
                        className="absolute z-30 w-56 rounded-lg border bg-popover p-2 text-xs shadow-md"
                        style={{ left: Math.max(RULER_THICKNESS, left - 112), top: top + 14 }}
                        onMouseDown={(event) => event.stopPropagation()}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <span className="text-2xs uppercase tracking-wide text-muted-foreground">
                            {pin.caption}
                          </span>
                          <button
                            type="button"
                            aria-label="Close comment"
                            className="text-muted-foreground hover:text-foreground"
                            onClick={(event) => {
                              event.stopPropagation();
                              setOpenPinId(null);
                            }}
                          >
                            <X className="size-3.5" />
                          </button>
                        </div>
                        <p className="mt-1 whitespace-pre-wrap">{pin.body}</p>
                        {/* D13 (AC-S9-1/S9-2): dismiss the comment from the
                            pin that opened it, same copy/icons as
                            `RequestDesignSection`'s footer - only when the
                            host wired somewhere for the decision to go. */}
                        {onReviewPinResolve && (
                          <Button
                            type="button"
                            variant={pin.resolved ? 'ghost' : 'outline'}
                            size="sm"
                            className="mt-2 h-6 px-2 text-2xs"
                            onMouseDown={(event) => event.stopPropagation()}
                            onClick={(event) => {
                              event.stopPropagation();
                              void onReviewPinResolve(pin.id, !pin.resolved);
                            }}
                          >
                            {pin.resolved ? (
                              <>
                                <Undo2 className="mr-1 size-3" />
                                Reopen
                              </>
                            ) : (
                              <>
                                <Check className="mr-1 size-3" />
                                Done
                              </>
                            )}
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Inline text edit (S2, D5): a plain textarea laid over the
                  node, same maths `KonvaTagLayer` uses for the node itself.
                  Kept open on `editingLayer` regardless of where the
                  selection moves (S1) - see `commitInlineEdit` above. */}
              {editingLayerId && editingLayer && editingLayer.props.kind === 'text' && (
                <InlineTextEditor
                  key={editingLayer.id}
                  layer={editingLayer}
                  value={editingResolvedValue ?? editingContent}
                  readOnly={editingResolvedValue != null}
                  scale={scale}
                  originX={RULER_THICKNESS + view.panX}
                  originY={RULER_THICKNESS + view.panY}
                  onChangeText={(text) => {
                    editingTextRef.current = text;
                  }}
                  onCommit={commitInlineEdit}
                  onCancel={cancelInlineEdit}
                />
                )}
            </div>
          </ContextMenuTrigger>

          <ContextMenuContent className="w-56">
            {hasSelection && !menuOnEmpty ? (
              <>
                <ContextMenuItem onSelect={handleCut}>
                  <Scissors />
                  Cut
                  <ContextMenuShortcut>Ctrl+X</ContextMenuShortcut>
                </ContextMenuItem>
                <ContextMenuItem onSelect={handleCopy}>
                  <Copy />
                  Copy
                  <ContextMenuShortcut>Ctrl+C</ContextMenuShortcut>
                </ContextMenuItem>
                <ContextMenuItem onSelect={handlePaste} disabled={!clipboard}>
                  <ClipboardPaste />
                  Paste
                  <ContextMenuShortcut>Ctrl+V</ContextMenuShortcut>
                </ContextMenuItem>
                <ContextMenuItem onSelect={duplicateSelectedLayers}>
                  <Copy />
                  Duplicate
                  <ContextMenuShortcut>Ctrl+D</ContextMenuShortcut>
                </ContextMenuItem>
                {canCropSelectedImage && (
                  <ContextMenuItem onSelect={enterCropMode}>
                    <Crop />
                    Crop image
                  </ContextMenuItem>
                )}

                <ContextMenuSeparator />

                <ContextMenuItem onSelect={() => reorderSelection('front')}>
                  <ArrowUpToLine />
                  Bring to Front
                </ContextMenuItem>
                <ContextMenuItem onSelect={() => reorderSelection('forward')}>
                  <ArrowUp />
                  Bring Forward
                </ContextMenuItem>
                <ContextMenuItem onSelect={() => reorderSelection('backward')}>
                  <ArrowDown />
                  Send Backward
                </ContextMenuItem>
                <ContextMenuItem onSelect={() => reorderSelection('back')}>
                  <ArrowDownToLine />
                  Send to Back
                </ContextMenuItem>

                <ContextMenuSeparator />

                {selectionIsGroup ? (
                  <ContextMenuItem onSelect={ungroupSelectedLayers}>
                    <Ungroup />
                    Ungroup
                  </ContextMenuItem>
                ) : (
                  <ContextMenuItem
                    onSelect={groupSelectedLayers}
                    disabled={selectionRoots.length < 2}
                  >
                    <GroupIcon />
                    Group
                    <ContextMenuShortcut>Ctrl+G</ContextMenuShortcut>
                  </ContextMenuItem>
                )}
                {canEnterGroup && (
                  <ContextMenuItem onSelect={enterSelectedGroup}>
                    <CornerRightDown />
                    Enter Group
                  </ContextMenuItem>
                )}
                {canSelectParent && (
                  <ContextMenuItem onSelect={selectParentGroup}>
                    <CornerLeftUp />
                    Select Parent Group
                    <ContextMenuShortcut>Esc</ContextMenuShortcut>
                  </ContextMenuItem>
                )}

                <ContextMenuSeparator />

                <ContextMenuItem onSelect={toggleSelectionLock}>
                  {selectionLocked ? <Unlock /> : <Lock />}
                  {selectionLocked ? 'Unlock' : 'Lock'}
                </ContextMenuItem>
                <ContextMenuItem onSelect={hideSelection}>
                  <EyeOff />
                  Hide
                </ContextMenuItem>

                <ContextMenuSeparator />

                <ContextMenuItem variant="destructive" onSelect={deleteSelectedLayers}>
                  <Trash2 />
                  Delete
                  <ContextMenuShortcut>Del</ContextMenuShortcut>
                </ContextMenuItem>
              </>
            ) : (
              <>
                <ContextMenuItem onSelect={handlePaste} disabled={!clipboard}>
                  <ClipboardPaste />
                  Paste
                  <ContextMenuShortcut>Ctrl+V</ContextMenuShortcut>
                </ContextMenuItem>
                <ContextMenuItem onSelect={selectAll}>
                  <SquareDashed />
                  Select All
                  <ContextMenuShortcut>Ctrl+A</ContextMenuShortcut>
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem onSelect={handleFit}>
                  <Expand />
                  Fit to View
                  <ContextMenuShortcut>Ctrl+0</ContextMenuShortcut>
                </ContextMenuItem>
                <ContextMenuItem onSelect={handleZoomReset}>
                  <Percent />
                  Zoom 100%
                  <ContextMenuShortcut>Ctrl+1</ContextMenuShortcut>
                </ContextMenuItem>
              </>
            )}
          </ContextMenuContent>
        </ContextMenu>
          </ResizablePanel>

          {/* Right sidebar: Inspector panel (AC-S1-2, AC-S1-3). */}
          <ResizableHandle
            withHandle
            className="hidden lg:flex"
            onDragging={handlePanelDragEnd}
            aria-label="Resize Inspector panel"
          />
          <ResizablePanel
            ref={rightPanelRef}
            id="canvas-right"
            order={3}
            collapsible
            collapsedSize={0}
            minSize={rightMinPercent}
            maxSize={rightMaxPercent}
            defaultSize={panelLayout.rightCollapsed ? 0 : rightPercent}
            onResize={handleRightResize}
            onCollapse={() => {
              if (!hasHydratedRef.current || !isGroupInteractive) return;
              persistPanelLayout((prev) => ({ ...prev, rightCollapsed: true }));
            }}
            onExpand={() => {
              if (!hasHydratedRef.current || !isGroupInteractive) return;
              persistPanelLayout((prev) => ({ ...prev, rightCollapsed: false }));
            }}
            className="relative hidden lg:block"
          >
            <button
              type="button"
              className="absolute right-1 top-1 z-10 flex size-5 items-center justify-center rounded bg-background/80 text-muted-foreground hover:bg-accent hover:text-foreground"
              title="Collapse Inspector"
              aria-label="Collapse Inspector"
              onClick={() => {
                rightPanelRef.current?.collapse();
                handleFit();
              }}
            >
              <ChevronRight className="size-3.5" />
            </button>
            <InspectorPanel
              layer={selectedLayer}
              onUpdate={updateLayer}
              onUpdateProps={updateLayerProps}
              layers={layers}
              resolvedText={selectedResolvedText}
              bindingLabel={selectedBindingLabel}
              fontOptions={library.fontOptions}
              onUploadFont={() => setFontUploadOpen(true)}
              onInsertField={() => setInsertFieldOpen(true)}
              onChooseImage={handleChooseImage}
              onChooseBadge={handleChooseBadge}
              onRebind={handleRebind}
              onRelinkGroup={handleRelinkGroup}
              onUseTemplate={onUseTemplate}
              previewBlockId={selectedBlock?.groupId ?? null}
              previewBlockLabel={
                selectedBlock ? previewChoices[selectedBlock.groupId]?.label ?? null : null
              }
              onPreviewBlock={openBlockPreview}
              onClearBlockPreview={clearBlockPreview}
              editingPoints={editingPoints}
              onToggleEditPoints={(layerId) =>
                setEditingPointsId((prev) => (prev === layerId ? null : layerId))
              }
              subjectParts={subjectParts}
              subjectParentCode={subjectParentCode}
            />
          </ResizablePanel>
        </ResizablePanelGroup>

        {/* Right column collapsed strip - see the left column's own comment. */}
        {panelLayout.rightCollapsed && (
          <button
            type="button"
            className="hidden w-6 shrink-0 items-start justify-center border-l bg-muted/30 pt-2 hover:bg-muted lg:flex"
            title="Expand Inspector"
            aria-label="Expand Inspector"
            onClick={() => {
              rightPanelRef.current?.expand(rightPercent);
              handleFit();
            }}
          >
            <ChevronLeft className="size-3.5" />
          </button>
        )}
      </div>

      {/* Pickers. Rendered here rather than beside their buttons so the
          canvas keeps its selection while a dialog is open. */}
      <ProductPickDialog
        open={picker.kind !== 'none'}
        mode={
          picker.kind === 'add-set'
            ? 'set'
            : picker.kind === 'rebind'
              ? picker.mode
              : picker.kind === 'preview'
                ? picker.mode
                : 'product'
        }
        multiple={picker.kind === 'alternatives' || picker.kind === 'accessories'}
        title={
          picker.kind === 'add-set'
            ? 'Add a product set'
            : picker.kind === 'rebind'
              ? 'Change what this block is about'
              : picker.kind === 'preview'
                ? picker.groupId === WHOLE_TAG_BLOCK_ID
                  ? 'Preview the whole tag with'
                  : 'Preview this block with'
                : picker.kind === 'alternatives'
                  ? 'Add an alternatives row'
                  : picker.kind === 'accessories'
                    ? 'Add an accessories strip'
                    : 'Add a product'
        }
        confirmLabel={
          picker.kind === 'rebind'
            ? 'Rebind'
            : picker.kind === 'preview'
              ? 'Preview'
              : 'Add'
        }
        busy={pickerBusy}
        onCancel={closePicker}
        onConfirm={(ids) => {
          void handlePick(ids);
        }}
      />

      <AssetPickerDialog
        open={imagePicker !== null}
        productImages={pickerProductImages}
        allowProductPhotos={!imagePicker?.badge}
        uploadKind={imagePicker?.badge ? 'badge' : 'decorative'}
        title={imagePicker?.badge ? 'Choose a badge' : 'Choose an image'}
        onCancel={() => setImagePicker(null)}
        onPick={handleImagePicked}
      />

      <InsertFieldDialog
        open={insertFieldOpen}
        value={selectedContent}
        data={selectedData ?? null}
        specKeys={library.specKeys}
        onCancel={() => setInsertFieldOpen(false)}
        onDone={(content) => {
          setInsertFieldOpen(false);
          writeSelectedContent(content);
        }}
      />

      <FontUploadDialog
        open={fontUploadOpen}
        fonts={library.fonts}
        onCancel={() => {
          setFontUploadOpen(false);
          // A delete started inside the dialog keeps counting down after it
          // closes (D7 - the window is the server's, not the dialog's), so
          // whatever happened while it was open - a commit, a cancel, a
          // failure - has to be caught up on here, or the Font Family
          // dropdown can go on offering a family the server already deleted.
          void library.reload();
        }}
        onUploaded={(asset) => {
          library.remember(asset);
          setFontUploadOpen(false);
        }}
        onRenamed={(asset, oldName) => {
          library.rename(asset.id, asset.name);
          renameFontFamilyInOpenDoc(oldName, asset.name);
          void library.reload();
        }}
        onDeleted={(id) => {
          library.forget(id);
          void library.reload();
        }}
      />

      {/* Save bar (sticky bottom). Absent when the host owns saving. */}
      <div
        className={cn(
          'flex h-10 shrink-0 items-center justify-end gap-2 border-t bg-background px-4',
          hideSaveBar && 'hidden',
        )}
      >
        <span className="text-xs text-muted-foreground">
          {layers.length} layer{layers.length !== 1 ? 's' : ''}
          {' / '}
          {doc.width_mm} x {doc.height_mm} mm
        </span>
        <button
          type="button"
          className="rounded bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
          onClick={save}
        >
          Save
        </button>
      </div>
    </div>
  );
}

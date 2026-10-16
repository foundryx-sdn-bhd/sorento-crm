'use client';

import { useCallback, useMemo, useRef, useState } from 'react';
import { Copy, RotateCw, Trash2 } from 'lucide-react';

import { cn } from '@/lib/utils';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import {
  DEFAULT_GRID_MM,
  areaSquareMetres,
  boxCorners,
  moveWall,
  roomBounds,
  setWallLength,
  snapToGrid,
  type Box,
  type Point,
} from '@/lib/dealer-kit/roomGeometry';
import { clearances, snapToWall, wallUnder } from '@/lib/dealer-kit/roomSnap';
import { floorColor, wallColor, type Finishes } from '@/lib/dealer-kit/finishes';
import {
  openingEdgeGaps,
  placeOpeningOnNearestWall,
  type Opening,
} from '@/lib/dealer-kit/roomOpenings';

/**
 * The room, from above.
 *
 * Drawing is always available, never a fallback nobody can reach: an upload
 * that traces the walls badly must leave the user dragging corners, not stuck
 * (AC-R4). So this view is the primitive and detection merely seeds it.
 *
 * Everything is millimetres. The SVG viewBox does the scaling, which means no
 * pixel-per-mm constant is threaded through the drag maths - the one place
 * these things usually go wrong.
 */

/**
 * Breathing room around the outline, in millimetres.
 *
 * Wide enough for a wall label to sit OUTSIDE the room without being clipped by
 * the viewBox: the labels read "4000 mm" at 150 units, so 400 left half of one
 * hanging off the edge.
 */
const PADDING_MM = 1000;

/** The viewBox that shows the whole outline with breathing room, in millimetres. */
function viewBoxFor(outline: Point[]) {
  const raw = roomBounds(outline);
  return {
    minX: raw.minX - PADDING_MM,
    minY: raw.minY - PADDING_MM,
    width: Math.max(1000, raw.maxX - raw.minX + PADDING_MM * 2),
    height: Math.max(1000, raw.maxY - raw.minY + PADDING_MM * 2),
  };
}

export interface RoomPlanProps {
  outline: Point[];
  boxes: (Box & { id: string; label: string })[];
  selectedBoxId?: string | null;
  onOutlineChange: (outline: Point[]) => void;
  /** Rotation travels with the position: backing onto a wall turns the box. */
  onMoveBox?: (boxId: string, x: number, y: number, rotation: number) => void;
  onSelectBox?: (boxId: string) => void;
  onRotateBox?: (boxId: string) => void;
  onDuplicateBox?: (boxId: string) => void;
  onRemoveBox?: (boxId: string) => void;
  /** Called when a drag or a typed length finishes, so it can be undone as one step. */
  onCommit?: () => void;
  /** Doors and windows cut into the walls. */
  openings?: Opening[];
  selectedOpeningId?: string | null;
  onSelectOpening?: (openingId: string | null) => void;
  /** An opening can be dragged onto a different wall, so the wall travels too. */
  onMoveOpening?: (openingId: string, offsetMm: number, wallIndex: number) => void;
  /** Surface finishes, so the plan shows the scheme rather than a blank room. */
  finishes?: Finishes;
  /** Which wall is selected, so "add a door" knows where to put one. */
  selectedWallIndex?: number | null;
  onSelectWall?: (wallIndex: number | null) => void;
  /** A traced floor plan sits behind the grid as a guide (AC-R4). */
  backgroundUrl?: string | null;
}

export function RoomPlan({
  outline,
  boxes,
  selectedBoxId,
  onOutlineChange,
  onMoveBox,
  onSelectBox,
  onRotateBox,
  onDuplicateBox,
  onRemoveBox,
  onCommit,
  openings = [],
  selectedOpeningId,
  onSelectOpening,
  onMoveOpening,
  finishes,
  selectedWallIndex,
  onSelectWall,
  backgroundUrl,
}: RoomPlanProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [dragging, setDragging] = useState<
    | { kind: 'corner'; index: number }
    | { kind: 'box'; id: string }
    | { kind: 'wall'; index: number }
    | { kind: 'opening'; id: string }
    | null
  >(null);

  /**
   * Where a wall drag began, and the outline it began from.
   *
   * A ref, not state, and deliberately so. Moving a wall is a RELATIVE gesture,
   * so it needs a fixed origin; keeping that origin in state meant every
   * pointermove within one React batch still saw the previous origin and
   * re-applied the same delta. A 60px drag came out as 7 metres.
   *
   * Anchoring to the outline as it was at pointerdown also makes the drag
   * idempotent: the same cursor position always yields the same wall, however
   * many move events arrived on the way there.
   */
  const wallDragOrigin = useRef<{ x: number; y: number; outline: Point[] } | null>(null);

  /**
   * The wall whose length is being typed, and what has been typed so far.
   *
   * A dealer arrives with a tape measure, not a mouse. Dragging is for shaping;
   * typing is for transferring a measurement that already exists, and it has to
   * live ON the wall - a side panel makes you look away from the thing you are
   * changing.
   */
  const [editingWall, setEditingWall] = useState<{ index: number; value: string; error?: string } | null>(null);

  const liveBounds = useMemo(() => viewBoxFor(outline), [outline]);

  /**
   * The viewBox is FROZEN for the duration of a drag.
   *
   * Otherwise the drag chases itself: growing the room grows the bounds, the
   * bounds set the millimetres-per-pixel scale, and the same cursor position
   * then means MORE millimetres than it did a frame ago. Pulling a wall 60px
   * moved it 3.7 metres instead of 0.7, and a corner ran away from the pointer.
   *
   * The cost is that a room dragged past the frozen edge is briefly clipped;
   * the padding covers ordinary gestures, and the scale settles on drop.
   */
  const frozenBounds = useRef<ReturnType<typeof viewBoxFor> | null>(null);
  const fitted = dragging ? (frozenBounds.current ?? liveBounds) : liveBounds;

  /**
   * Where the user has moved and zoomed the drawing.
   *
   * Kept as an offset and a scale ON TOP of the fitted view rather than as an
   * absolute viewBox, so reshaping the room still re-fits sensibly and "reset"
   * is just clearing this back to zero and one.
   */
  const [view, setView] = useState({ panX: 0, panY: 0, zoom: 1 });
  const panOrigin = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  /** Read during a pan, where state would be a frame behind. */
  const viewRef = useRef({ panX: 0, panY: 0, zoom: 1 });

  /**
   * The gesture in progress, mirrored into a ref.
   *
   * State is a render behind: the first pointermove after a pointerdown can
   * still see the PREVIOUS gesture, and "previous gesture" here meant grabbing
   * a door and moving the wall it was cut into. The ref is written
   * synchronously in the same handler, so every move sees what was just
   * grabbed.
   */
  const dragRef = useRef<typeof dragging>(null);

  /**
   * Where a dragged product WOULD be if nothing stopped it.
   *
   * Illegal positions are refused by simply not following the cursor, which is
   * what the planner we studied does - nothing ever turns red. On its own that
   * reads as a stuck drag, so the attempted position is drawn as an outline:
   * the solid body says what you get, the ghost says what you asked for.
   */
  const [ghost, setGhost] = useState<Box | null>(null);

  const bounds = useMemo(() => {
    const width = fitted.width / view.zoom;
    const height = fitted.height / view.zoom;
    return {
      // Zoom about the middle of the current view, which is what a wheel over a
      // drawing is expected to do.
      minX: fitted.minX + (fitted.width - width) / 2 + view.panX,
      minY: fitted.minY + (fitted.height - height) / 2 + view.panY,
      width,
      height,
    };
  }, [fitted, view]);

  viewRef.current = view;

  const resetView = useCallback(() => setView({ panX: 0, panY: 0, zoom: 1 }), []);

  /** Screen point -> millimetres, via the SVG's own transform. */
  const toMillimetres = useCallback((event: React.PointerEvent): Point | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const local = point.matrixTransform(ctm.inverse());
    return { x: local.x, y: local.y };
  }, []);

  const handleMove = useCallback(
    (event: React.PointerEvent) => {
      // A gesture with no button held is a gesture whose pointerup was lost -
      // a pointer that left the window, or a synthetic event. Ending it here
      // stops a stale drag following the cursor around the plan.
      if (event.buttons === 0 && (dragRef.current || panOrigin.current)) {
        dragRef.current = null;
        panOrigin.current = null;
        setDragging(null);
        return;
      }

      const pan = panOrigin.current;
      if (pan) {
        // Millimetres per pixel, from the live viewBox: the drawing should move
        // exactly with the cursor at any zoom.
        const svg = svgRef.current;
        const scale = svg ? bounds.width / (svg.getBoundingClientRect().width || 1) : 1;
        setView((current) => ({
          ...current,
          panX: pan.panX - (event.clientX - pan.x) * scale,
          panY: pan.panY - (event.clientY - pan.y) * scale,
        }));
        return;
      }
      const dragging = dragRef.current;
      if (!dragging) return;
      const position = toMillimetres(event);
      if (!position) return;

      const x = snapToGrid(position.x, DEFAULT_GRID_MM);
      const y = snapToGrid(position.y, DEFAULT_GRID_MM);

      if (dragging.kind === 'corner') {
        const next = outline.map((point, index) =>
          index === dragging.index ? { x, y } : point,
        );
        onOutlineChange(next);
        return;
      }

      if (dragging.kind === 'wall') {
        // Always measured from the gesture's origin against the outline as it
        // was then. Measuring against the CURRENT outline would fold each move
        // into the next and run the wall away from the cursor.
        const origin = wallDragOrigin.current;
        if (!origin) return;
        onOutlineChange(
          moveWall(
            origin.outline,
            dragging.index,
            position.x - origin.x,
            position.y - origin.y,
          ),
        );
        return;
      }

      if (dragging.kind === 'opening') {
        const opening = openings.find((candidate) => candidate.id === dragging.id);
        if (!opening) return;

        // The door goes to whichever wall the pointer is nearest, not only the
        // one it started on. The rule is shared with the 3D view, which drags
        // openings too and has to land them in exactly the same place.
        const fitted = placeOpeningOnNearestWall(opening, outline, position);
        // A wall too short for this opening simply does not take it: the door
        // stays where it was rather than being narrowed to fit.
        if (fitted) onMoveOpening?.(opening.id, fitted.offsetMm, fitted.wallIndex);
        return;
      }

      const box = boxes.find((candidate) => candidate.id === dragging.id);
      if (box && onMoveBox) {
        // Drag by the centre: grabbing a corner makes a unit jump on pickup.
        const moved = { ...box, x: x - box.width / 2, y: y - box.depth / 2 };
        // Wall magnetism. Orientation is never the user's job: a vanity backs
        // onto a wall, and dragging it toward another wall hops it across and
        // turns it. There is no rotate handle to leave pointing at a wall.
        const snapped = snapToWall(moved, outline);
        const result = snapped?.box ?? moved;
        setGhost(moved);
        onMoveBox(dragging.id, result.x, result.y, result.rotation);
      }
    },
    [
      outline,
      boxes,
      openings,
      bounds.width,
      onOutlineChange,
      onMoveBox,
      onMoveOpening,
      toMillimetres,
    ],
  );

  /**
   * Pan with the middle button (or space-drag), zoom with the wheel.
   *
   * The middle button on purpose: left is already "grab the thing under the
   * cursor", and a planner where you cannot move the paper is a planner you
   * fight as soon as the room is bigger than the panel.
   */
  const startPan = useCallback((event: React.PointerEvent) => {
    // Middle, right, or shift-left. Left alone is "grab the thing under the
    // cursor" and always will be.
    if (event.button !== 1 && event.button !== 2 && !event.shiftKey) return false;
    event.preventDefault();
    panOrigin.current = {
      x: event.clientX,
      y: event.clientY,
      panX: viewRef.current.panX,
      panY: viewRef.current.panY,
    };
    return true;
  }, []);

  const endDrag = useCallback(() => {
    panOrigin.current = null;
    dragRef.current = null;
    setGhost(null);
    // One undo entry per gesture, not per frame: a drag emits a state every
    // pointermove, and recording each would make Ctrl-Z look broken.
    if (dragging) onCommit?.();
    setDragging(null);
    wallDragOrigin.current = null;
    frozenBounds.current = null;
  }, [dragging, onCommit]);

  /** Freeze the scale, then start the gesture. Order matters: the first
      pointermove already needs the frozen viewBox. */
  const beginDrag = useCallback(
    (next: NonNullable<typeof dragging>) => {
      frozenBounds.current = viewBoxFor(outline);
      dragRef.current = next;
      setDragging(next);
    },
    [outline],
  );

  /** Apply a typed wall length, or quietly drop a value that is not a room. */
  const commitWallLength = useCallback(() => {
    // Read the state, do not fold the edit into a setState updater: React may
    // run an updater twice, and applying a length twice moves the wall twice.
    if (!editingWall) return;
    // A blank or unusable length is not applied and not thrown away: the box stays open
    // with what was typed and says why.
    const error = numberFieldError(editingWall.value, { min: 1 });
    if (error) {
      setEditingWall({ ...editingWall, error });
      return;
    }
    const next = setWallLength(outline, editingWall.index, parseNumberDraft(editingWall.value) as number);
    setEditingWall(null);
    if (next !== outline) {
      onOutlineChange(next);
      onCommit?.();
    }
  }, [editingWall, outline, onOutlineChange, onCommit]);

  /**
   * How much wall is free either side of the selected product.
   *
   * This is the number a dealer actually wants: not "where is this thing", but
   * "will the next one fit beside it". Shown for the selection and live during
   * a drag, the way a tape measure would be.
   */
  const selected = boxes.find((box) => box.id === selectedBoxId) ?? null;
  const selectedWall = selected ? wallUnder(selected, outline) : null;
  const gaps = selected
    ? clearances(
        selected,
        boxes.filter((box) => box.id !== selected.id),
        outline,
        selectedWall,
      )
    : null;

  const area = areaSquareMetres(outline);
  /** Only used to decide which side of a wall its label sits on. */
  const centroid = useMemo(() => {
    if (outline.length === 0) return { x: 0, y: 0 };
    return {
      x: outline.reduce((total, point) => total + point.x, 0) / outline.length,
      y: outline.reduce((total, point) => total + point.y, 0) / outline.length,
    };
  }, [outline]);
  const path =
    outline.length >= 3
      ? `${outline.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ')} Z`
      : '';

  return (
    <div className="relative w-full overflow-hidden rounded-lg border border-border bg-muted/20">
      <svg
        ref={svgRef}
        viewBox={`${bounds.minX} ${bounds.minY} ${bounds.width} ${bounds.height}`}
        // select-none: dragging across the drawing was highlighting every
        // label it passed, which looks like a broken drag and leaves blue text
        // behind on drop.
        className="h-full w-full touch-none select-none"
        style={{ aspectRatio: `${bounds.width} / ${bounds.height}` }}
        onPointerDown={startPan}
        onPointerMove={handleMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
        onAuxClick={(event) => event.preventDefault()}
        onWheel={(event) => {
          event.preventDefault();
          // Zoom toward the CURSOR, not the middle of the panel: zooming to
          // centre means every zoom is followed by a pan to get back to the
          // corner you were looking at.
          const point = toMillimetres(event as unknown as React.PointerEvent);
          setView((current) => {
            // Clamped: past 8x a 50mm grid square fills the panel, and below
            // half the room is a dot nobody can grab.
            const zoom = Math.min(
              8,
              Math.max(0.5, current.zoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1)),
            );
            if (!point || zoom === current.zoom) return { ...current, zoom };

            // Keep the millimetre under the cursor under the cursor: the view
            // shrinks about the centre, so shift by the share of that shrink
            // that lies between the centre and the pointer.
            const factor = 1 / current.zoom - 1 / zoom;
            const centreX = bounds.minX + bounds.width / 2;
            const centreY = bounds.minY + bounds.height / 2;
            return {
              zoom,
              panX: current.panX + (point.x - centreX) * factor * current.zoom,
              panY: current.panY + (point.y - centreY) * factor * current.zoom,
            };
          });
        }}
        onContextMenu={(event) => {
          // Right-drag pans, as it does in the planner we studied, so the
          // browser menu must not open on top of it.
          event.preventDefault();
        }}
        data-dk-room-plan
      >
        <defs>
          <pattern
            id="dk-grid"
            width={DEFAULT_GRID_MM * 10}
            height={DEFAULT_GRID_MM * 10}
            patternUnits="userSpaceOnUse"
          >
            <path
              d={`M ${DEFAULT_GRID_MM * 10} 0 L 0 0 0 ${DEFAULT_GRID_MM * 10}`}
              fill="none"
              stroke="currentColor"
              strokeWidth={6}
              className="text-border"
            />
          </pattern>
        </defs>

        {backgroundUrl && (
          <image
            href={backgroundUrl}
            x={bounds.minX}
            y={bounds.minY}
            width={bounds.width}
            height={bounds.height}
            opacity={0.35}
            preserveAspectRatio="xMidYMid meet"
          />
        )}

        <rect
          x={bounds.minX}
          y={bounds.minY}
          width={bounds.width}
          height={bounds.height}
          fill="url(#dk-grid)"
        />

        {path && (
          // The floor carries its finish, and each wall is stroked in its own:
          // two schemes should be tellable apart at a glance without opening
          // anything.
          <path
            d={path}
            fill={floorColor(finishes)}
            className="stroke-foreground"
            strokeWidth={24}
            strokeLinejoin="round"
          />
        )}

        {outline.length >= 3 &&
          outline.map((point, index) => {
            const next = outline[(index + 1) % outline.length];
            return (
              <line
                key={`wall-finish-${index}`}
                x1={point.x}
                y1={point.y}
                x2={next.x}
                y2={next.y}
                stroke={wallColor(finishes, index)}
                strokeWidth={16}
                data-dk-wall-finish={index}
              />
            );
          })}

        {/* The chosen wall, drawn over the outline so it is obvious which one a
            new door would land in. */}
        {selectedWallIndex !== null &&
          selectedWallIndex !== undefined &&
          outline[selectedWallIndex] && (
            <line
              x1={outline[selectedWallIndex].x}
              y1={outline[selectedWallIndex].y}
              x2={outline[(selectedWallIndex + 1) % outline.length].x}
              y2={outline[(selectedWallIndex + 1) % outline.length].y}
              className="stroke-primary"
              strokeWidth={44}
              data-dk-wall-selected={selectedWallIndex}
            />
          )}

        {/*
          A wall length beside every wall (AC-R1). Without it a user drags a
          corner and has no idea whether they just made a 3.6m run or a 4.1m
          one - and "roughly right" is how a worktop gets ordered 200mm short.
          Derived from the outline on every render, so it is live during a drag
          rather than a value that catches up on drop.
        */}
        {outline.length >= 3 &&
          outline.map((point, index) => {
            const next = outline[(index + 1) % outline.length];
            const length = Math.round(Math.hypot(next.x - point.x, next.y - point.y));
            if (length === 0) return null;

            // Nudged off the wall, on the side away from the room's middle, so
            // the label never sits on top of the line it measures.
            const midX = (point.x + next.x) / 2;
            const midY = (point.y + next.y) / 2;
            const horizontal = Math.abs(next.x - point.x) >= Math.abs(next.y - point.y);
            const outward = horizontal
              ? (midY < centroid.y ? -1 : 1)
              : (midX < centroid.x ? -1 : 1);

            // A vertical wall's label is set BESIDE the wall and anchored at its
            // near edge. Centring it on a point 180mm away is not enough: the
            // text is over a metre wide at this scale, so half of it lands back
            // on the wall it measures.
            const gap = 140;

            const labelX = midX + (horizontal ? 0 : outward * gap);
            const labelY = midY + (horizontal ? outward * 260 : 0);

            if (editingWall?.index === index) {
              // An HTML input inside the SVG, so it sits exactly where the
              // label was. Everything here is in millimetres, same as the plan.
              const boxWidth = 1300;
              const boxHeight = 420;
              return (
                <foreignObject
                  key={`wall-${index}`}
                  x={labelX - (horizontal ? boxWidth / 2 : outward < 0 ? boxWidth : 0)}
                  y={labelY - boxHeight / 2}
                  width={boxWidth}
                  height={boxHeight + (editingWall.error ? 300 : 0)}
                >
                  <NumberInput
                    autoFocus
                    // Pre-selected: the number is being REPLACED, not edited,
                    // so typing should overwrite it without a select-all first.
                    onFocus={(event) => event.currentTarget.select()}
                    aria-label={`Length of wall ${index + 1} in millimetres`}
                    data-dk-wall-input={index}
                    value={editingWall.value}
                    aria-invalid={editingWall.error ? true : undefined}
                    onChange={(event) =>
                      setEditingWall({ index, value: event.target.value })
                    }
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') commitWallLength();
                      if (event.key === 'Escape') setEditingWall(null);
                    }}
                    // Committing on blur too: clicking away with a number typed
                    // and having it thrown out is the more annoying half of the
                    // two possible mistakes.
                    onBlur={commitWallLength}
                    style={{ fontSize: 170, height: boxHeight, width: boxWidth }}
                    className="w-full rounded border-2 border-primary bg-background px-2 text-center text-foreground"
                  />
                  {editingWall.error ? (
                    <p
                      className="text-center leading-tight text-destructive"
                      style={{ fontSize: 110, marginTop: 20 }}
                    >
                      {editingWall.error}
                    </p>
                  ) : null}
                </foreignObject>
              );
            }

            /**
             * Drawn as a PILL, not bare text.
             *
             * The number was already editable, but nothing said so: plain grey
             * text beside a wall reads as an annotation, and the first person to
             * use this asked whether the dimensions could be typed at all. A
             * bordered chip that lights up under the cursor answers the question
             * before it is asked.
             */
            const label = `${length} mm`;
            const pillWidth = label.length * 82 + 140;
            const pillHeight = 260;
            const pillX = labelX - (horizontal ? pillWidth / 2 : outward < 0 ? pillWidth : 0);

            return (
              <g
                key={`wall-${index}`}
                className="group cursor-pointer"
                data-dk-wall-label={index}
                onPointerDown={(event) => {
                  // Selecting the wall, not editing it: a single click that
                  // opened an input made the number impossible to just look at,
                  // and the same click is how you choose a wall to put a door
                  // in.
                  event.stopPropagation();
                  onSelectWall?.(index);
                }}
                onDoubleClick={(event) => {
                  event.stopPropagation();
                  setEditingWall({ index, value: String(length) });
                }}
              >
                <rect
                  x={pillX}
                  y={labelY - pillHeight / 2}
                  width={pillWidth}
                  height={pillHeight}
                  rx={90}
                  className="fill-background stroke-border group-hover:stroke-primary"
                  strokeWidth={14}
                />
                <text
                  x={labelX}
                  y={labelY}
                  textAnchor={horizontal ? 'middle' : outward < 0 ? 'end' : 'start'}
                  dominantBaseline="middle"
                  className="fill-muted-foreground group-hover:fill-primary"
                  style={{ fontSize: 150 }}
                >
                  {label}
                </text>
              </g>
            );
          })}

        {ghost && (
          <polygon
            points={boxCorners(ghost)
              .map((corner) => `${corner.x},${corner.y}`)
              .join(' ')}
            className="pointer-events-none fill-none stroke-primary/50"
            strokeWidth={14}
            strokeDasharray="90 70"
            data-dk-drag-ghost
          />
        )}

        {/* Wall handles are painted BEFORE the doors and the products.
            They are 160mm wide so a wall is easy to grab, which means the last
            layer painted wins every shared pixel - and while they sat on top,
            a door or a vanity standing against a wall could not be grabbed at
            all. Whatever is IN the wall now takes precedence over the wall. */}
        {/* A fat invisible line over each wall. Dragging a wall is what somebody
            means by "this wall is 200mm too far out"; doing it by moving two
            corners is fiddly and lets the wall go out of square. */}
        {outline.length >= 3 &&
          outline.map((point, index) => {
            const next = outline[(index + 1) % outline.length];
            const horizontal = Math.abs(next.x - point.x) >= Math.abs(next.y - point.y);
            return (
              <line
                key={`wall-handle-${index}`}
                x1={point.x}
                y1={point.y}
                x2={next.x}
                y2={next.y}
                stroke="transparent"
                strokeWidth={160}
                className={horizontal ? 'cursor-ns-resize' : 'cursor-ew-resize'}
                data-dk-room-wall={index}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  // Selecting on pointerdown, not click: the same gesture that
                  // drags a wall also chooses it, so "add a door" always has a
                  // wall to put one on.
                  onSelectWall?.(index);
                  onSelectOpening?.(null);
                  const start = toMillimetres(event);
                  if (!start) return;
                  wallDragOrigin.current = { x: start.x, y: start.y, outline };
                  beginDrag({ kind: 'wall', index });
                }}
              />
            );
          })}

        {/*
          Doors and windows, drawn IN the wall they belong to. A door is shown
          as a break in the wall line with its swing; a window as a lighter
          band. Both are dragged along their wall and nowhere else, because
          that is the only place they can be.
        */}
        {openings.map((opening) => {
          const start = outline[opening.wallIndex];
          const end = start ? outline[(opening.wallIndex + 1) % outline.length] : null;
          if (!start || !end) return null;
          const length = Math.hypot(end.x - start.x, end.y - start.y);
          if (length < 1e-6) return null;

          const unitX = (end.x - start.x) / length;
          const unitY = (end.y - start.y) / length;
          const from = opening.offsetMm - opening.widthMm / 2;
          const to = opening.offsetMm + opening.widthMm / 2;
          const a = { x: start.x + unitX * from, y: start.y + unitY * from };
          const b = { x: start.x + unitX * to, y: start.y + unitY * to };
          const selected = opening.id === selectedOpeningId;
          const gaps = selected
            ? openingEdgeGaps(opening, length, openings)
            : null;
          const midX = (a.x + b.x) / 2;
          const midY = (a.y + b.y) / 2;
          // Inward normal, used to hang the swing arc and the label inside.
          const normalX = -unitY;
          const normalY = unitX;

          return (
            <g
              key={opening.id}
              data-dk-opening={opening.id}
              data-dk-opening-kind={opening.kind}
              className="cursor-move"
              onPointerDown={(event) => {
                event.stopPropagation();
                onSelectOpening?.(opening.id);
                beginDrag({ kind: 'opening', id: opening.id });
              }}
            >
              {/* A fat transparent grab area over the opening, the same trick
                  the walls use. The door itself is an 8px line on screen and
                  the swing arc is thinner still; without this you have to hit
                  the line exactly, and a near miss grabs the wall instead. */}
              <line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="transparent"
                strokeWidth={240}
              />
              {/* The hole itself: the wall is painted out along this stretch. */}
              <line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                className="stroke-background"
                strokeWidth={30}
              />
              <line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                className={selected ? 'stroke-primary' : 'stroke-muted-foreground'}
                strokeWidth={selected ? 60 : 40}
                strokeDasharray={opening.kind === 'window' ? '120 90' : undefined}
              />
              {opening.kind === 'door' && (
                // A quarter arc into the room: the universal drawing convention
                // for which way a door swings and how much floor it eats.
                <path
                  d={`M ${b.x} ${b.y} A ${opening.widthMm} ${opening.widthMm} 0 0 0 ${
                    b.x + normalX * opening.widthMm
                  } ${b.y + normalY * opening.widthMm}`}
                  fill="none"
                  className="stroke-muted-foreground"
                  strokeWidth={14}
                  strokeDasharray="90 70"
                />
              )}
              <text
                x={midX + normalX * 260}
                y={midY + normalY * 260}
                textAnchor="middle"
                dominantBaseline="middle"
                className={selected ? 'fill-primary' : 'fill-muted-foreground'}
                style={{ fontSize: 120 }}
              >
                {opening.widthMm} mm
              </text>
              {gaps && (
                <g className="pointer-events-none">
                  <text
                    x={a.x - unitX * 120 + normalX * 260}
                    y={a.y - unitY * 120 + normalY * 260}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    className="fill-primary"
                    style={{ fontSize: 110 }}
                  >
                    {gaps.before}
                  </text>
                  <text
                    x={b.x + unitX * 120 + normalX * 260}
                    y={b.y + unitY * 120 + normalY * 260}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    className="fill-primary"
                    style={{ fontSize: 110 }}
                  >
                    {gaps.after}
                  </text>
                </g>
              )}
            </g>
          );
        })}

        {boxes.map((box) => {
          const corners = boxCorners(box);
          const points = corners.map((corner) => `${corner.x},${corner.y}`).join(' ');
          const centre = {
            x: corners.reduce((total, corner) => total + corner.x, 0) / 4,
            y: corners.reduce((total, corner) => total + corner.y, 0) / 4,
          };
          return (
            <g
              key={box.id}
              onPointerDown={(event) => {
                event.stopPropagation();
                onSelectBox?.(box.id);
                beginDrag({ kind: 'box', id: box.id });
              }}
              className="cursor-move"
              data-dk-plan-box={box.id}
            >
              <polygon
                points={points}
                className={cn(
                  'stroke-primary',
                  box.id === selectedBoxId ? 'fill-primary/40' : 'fill-primary/15',
                )}
                strokeWidth={16}
              />
              {/*
                Trimmed to what the footprint can hold. SVG text does not wrap
                or clip on its own, so a long product code spills across the
                whole plan and covers the room it is meant to label. Roughly
                half the glyph width per character at this size.
              */}
              <text
                x={centre.x}
                y={centre.y}
                textAnchor="middle"
                dominantBaseline="middle"
                className="fill-foreground"
                style={{ fontSize: 110 }}
              >
                {(() => {
                  const fits = Math.max(3, Math.floor(Math.min(box.width, box.depth) / 55));
                  return box.label.length > fits
                    ? `${box.label.slice(0, fits - 1)}…`
                    : box.label;
                })()}
              </text>
              <title>{box.label}</title>
            </g>
          );
        })}

        {/*
          Clearance chips: the gap left on each side of the selected product,
          along the wall it stands against. IKEA's planner leans on exactly this
          instead of collision warnings - the numbers answer "will it fit"
          before you try, and they update live while dragging.
        */}
        {selected && gaps && selectedWall !== null && (() => {
          const corners = boxCorners(selected);
          const centre = {
            x: corners.reduce((total, corner) => total + corner.x, 0) / 4,
            y: corners.reduce((total, corner) => total + corner.y, 0) / 4,
          };
          const wallStart = outline[selectedWall];
          const wallEnd = outline[(selectedWall + 1) % outline.length];
          const length = Math.hypot(wallEnd.x - wallStart.x, wallEnd.y - wallStart.y) || 1;
          const unitX = (wallEnd.x - wallStart.x) / length;
          const unitY = (wallEnd.y - wallStart.y) / length;
          // How far the footprint itself reaches along the wall, measured from
          // its corners so a rotated box is handled too, plus a small gap. The
          // chips are then ANCHORED at their inner edge rather than centred:
          // centring put a four-digit number straight over the product label.
          const projected = corners.map(
            (corner) => (corner.x - centre.x) * unitX + (corner.y - centre.y) * unitY,
          );
          const reach = Math.max(...projected) + 120;
          const horizontal = Math.abs(unitX) >= Math.abs(unitY);
          // Anchored by which way the chip is offset ON SCREEN, not by the
          // wall's index. The bottom wall runs right-to-left, so anchoring off
          // "is this wall horizontal" alone put both chips back over the label.
          const anchorFor = (direction: number): 'start' | 'end' | 'middle' => {
            if (!horizontal) return 'middle';
            return direction < 0 ? 'end' : 'start';
          };

          return (
            <g className="pointer-events-none" data-dk-clearance>
              <text
                x={centre.x - unitX * reach}
                y={centre.y - unitY * reach}
                textAnchor={anchorFor(-unitX)}
                dominantBaseline="middle"
                className="fill-primary"
                style={{ fontSize: 130 }}
              >
                {gaps.before} mm
              </text>
              <text
                x={centre.x + unitX * reach}
                y={centre.y + unitY * reach}
                textAnchor={anchorFor(unitX)}
                dominantBaseline="middle"
                className="fill-primary"
                style={{ fontSize: 130 }}
              >
                {gaps.after} mm
              </text>
            </g>
          );
        })()}

        {/*
          Actions on the thing itself, not in a far-away panel: rotate, copy,
          remove. Rotate stays a button rather than a handle on purpose - a free
          rotation handle is how a vanity ends up facing a wall, and the planner
          we studied ships no free rotation at all.
        */}
        {selected && !dragging && (onRotateBox || onDuplicateBox || onRemoveBox) && (() => {
          const corners = boxCorners(selected);
          const top = Math.min(...corners.map((corner) => corner.y));
          const centreX = corners.reduce((total, corner) => total + corner.x, 0) / 4;
          /**
           * Sized in SCREEN pixels, placed in millimetres.
           *
           * A toolbar measured in millimetres is a toolbar that grows when you
           * zoom out and shrinks to nothing when you zoom in - it drifted a
           * long way from its product and looked like a floating panel. Fixing
           * its size on screen and converting to millimetres for placement
           * keeps it the same size at every zoom and always the same short
           * distance above the thing it belongs to.
           */
          const mmPerPx = bounds.width / (svgRef.current?.getBoundingClientRect().width || 1);
          const width = 108 * mmPerPx;
          const height = 34 * mmPerPx;
          // Kept inside the drawing, but only just: it should read as attached
          // to the product, not parked at the edge of the panel.
          const clampedX = Math.min(
            Math.max(centreX - width / 2, bounds.minX + 20 * mmPerPx),
            bounds.minX + bounds.width - width - 20 * mmPerPx,
          );
          const gap = 10 * mmPerPx;
          // Above by default, below when the product is near the top edge and
          // the toolbar would fall outside the drawing.
          const aboveY = top - height - gap;
          const bottom = Math.max(...corners.map((corner) => corner.y));
          const y = aboveY < bounds.minY ? bottom + gap : aboveY;
          return (
            <foreignObject
              x={clampedX}
              y={y}
              width={width}
              height={height}
              data-dk-box-toolbar
            >
              <div
                className="flex h-full w-full items-center justify-center border border-border bg-background/95 shadow-sm"
                // Inside a foreignObject one CSS pixel IS one user unit, and a
                // user unit here is a millimetre - so every size is written in
                // millimetres-per-screen-pixel to come out the intended size on
                // screen at any zoom.
                style={{ gap: 2 * mmPerPx, borderRadius: 6 * mmPerPx, padding: 3 * mmPerPx }}
              >
                {onRotateBox && (
                  <button
                    type="button"
                    aria-label="Rotate 90 degrees"
                    className="flex items-center justify-center hover:bg-muted"
                    style={{ padding: 3 * mmPerPx, borderRadius: 4 * mmPerPx }}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => onRotateBox(selected.id)}
                  >
                    <RotateCw style={{ width: 16 * mmPerPx, height: 16 * mmPerPx }} />
                  </button>
                )}
                {onDuplicateBox && (
                  <button
                    type="button"
                    aria-label="Duplicate product"
                    className="flex items-center justify-center hover:bg-muted"
                    style={{ padding: 3 * mmPerPx, borderRadius: 4 * mmPerPx }}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => onDuplicateBox(selected.id)}
                  >
                    <Copy style={{ width: 16 * mmPerPx, height: 16 * mmPerPx }} />
                  </button>
                )}
                {onRemoveBox && (
                  <button
                    type="button"
                    aria-label="Remove product from room"
                    className="flex items-center justify-center text-destructive hover:bg-muted"
                    style={{ padding: 3 * mmPerPx, borderRadius: 4 * mmPerPx }}
                    onPointerDown={(event) => event.stopPropagation()}
                    onClick={() => onRemoveBox(selected.id)}
                  >
                    <Trash2 style={{ width: 16 * mmPerPx, height: 16 * mmPerPx }} />
                  </button>
                )}
              </div>
            </foreignObject>
          );
        })()}


        {outline.map((point, index) => (
          <circle
            key={`${point.x}-${point.y}-${index}`}
            cx={point.x}
            cy={point.y}
            r={70}
            className="cursor-grab fill-background stroke-primary"
            strokeWidth={20}
            onPointerDown={(event) => {
              event.stopPropagation();
              beginDrag({ kind: 'corner', index });
            }}
            data-dk-room-corner={index}
          />
        ))}
      </svg>

      <button
        type="button"
        onClick={resetView}
        className="absolute bottom-2 start-2 rounded border border-border bg-background/90 px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
        data-dk-reset-view
        title="Fit the room to the panel"
      >
        Fit
      </button>

      <div className="pointer-events-none absolute bottom-2 end-2 rounded bg-background/90 px-2 py-1 text-xs text-muted-foreground">
        {/* Derived, never stored (AC-R5). */}
        {area.toFixed(1)} m<sup>2</sup>
      </div>
    </div>
  );
}

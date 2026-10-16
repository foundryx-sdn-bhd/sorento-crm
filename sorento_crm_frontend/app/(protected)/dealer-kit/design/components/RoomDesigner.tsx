'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import {
  AlertTriangle,
  ArrowLeft,
  Box as BoxIcon,
  Check,
  DoorOpen,
  Frame,
  PanelTop,
  Plus,
  ReceiptText,
  Redo2,
  Copy,
  RotateCw,
  Trash2,
  Undo2,
} from 'lucide-react';
import { toast } from '@/lib/toast';

import {
  Alert,
  AlertContent,
  AlertDescription,
  AlertIcon,
  AlertTitle,
} from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { LiveNumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { snapToWall } from '@/lib/dealer-kit/roomSnap';
import {
  areaSquareMetres,
  boxesOverlap,
  type Point,
} from '@/lib/dealer-kit/roomGeometry';
import { packBoxes, resolveDrag } from '@/lib/dealer-kit/roomPacking';
import { PICKER_PAGE_SIZE, listPickerProducts } from '../../services/productPickerService';
import {
  createSelection,
  getSelection,
  saveRoom,
  setSelectionLine,
  type Room,
  type SelectionLine,
} from '../../services/selectionService';
import {
  FLOOR_FINISHES,
  WALL_FINISHES,
  floorFinishId,
  setFloorFinish,
  setWallFinish,
  wallFinishId,
  type Finishes,
} from '@/lib/dealer-kit/finishes';
import {
  defaultsFor,
  fitOpening,
  fitOpenings,
  wallLengths,
  type Opening,
  type OpeningKind,
} from '@/lib/dealer-kit/roomOpenings';
import {
  canRedo,
  canUndo,
  newHistory,
  pushHistory,
  redo,
  undo,
  type History,
} from '@/lib/dealer-kit/history';
import {
  boxesForSelection,
  placementsOf,
  quantityOf,
  removeBox,
  type PlacedBox,
} from '@/lib/dealer-kit/roomBoxes';
import { clearPicks, readPicks } from '@/lib/dealer-kit/cataloguePicks';
import { CollapsiblePanel, FocusShell, FocusToggle } from '../../components/FocusMode';
import { RoomPlan } from './RoomPlan';
import { DEFAULT_CEILING_MM, RoomScene } from './RoomScene';
import { sameDisplayText } from '@/lib/product-display';

/**
 * The room designer: pick products, shape the room, place them, confirm.
 *
 * Plan and 3D are two views of ONE state, not two editors. Dragging in the plan
 * moves the box in 3D because there is only one array of boxes - the moment
 * they become separate models they start disagreeing, and the user is the one
 * who finds out.
 *
 * **The Selection is the source of truth for WHAT, the room for WHERE.** Which
 * products and how many lives on the server and comes back priced for whoever
 * is looking; the outline and the placements are saved alongside it. Nothing
 * price-shaped is computed here - a designer that did its own arithmetic would
 * be a second price list nobody knew they were maintaining.
 *
 * Sizes come from the Selection too, so a box is at the product's real
 * dimensions when the catalogue has them and an obvious placeholder when it
 * does not (AC-V1, AC-V2).
 */

/** A 4m x 3m room to start from. Reshaping four corners beats drawing from nothing. */
const STARTING_ROOM: Point[] = [
  { x: 0, y: 0 },
  { x: 4000, y: 0 },
  { x: 4000, y: 3000 },
  { x: 0, y: 3000 },
];

/** Everything an undo step has to restore: the room, and what stands in it. */
interface RoomSnapshot {
  outline: Point[];
  placed: PlacedBox[];
  openings: Opening[];
  finishes: Finishes;
}

/** Where this design is remembered between visits, so a reload is not a restart. */
const LAST_SELECTION_KEY = 'dealer-kit:last-selection';

export function RoomDesigner() {
  const queryClient = useQueryClient();
  const [selectionId, setSelectionId] = useState<string | null>(null);
  const [outline, setOutline] = useState<Point[]>(STARTING_ROOM);
  /*
    The current room, readable from the rebuild effect without making that
    effect depend on it. Depending on `outline` would repack every box each time
    somebody dragged a wall, moving the room's contents out from under them.
  */
  const outlineRef = useRef(outline);
  outlineRef.current = outline;
  const [placed, setPlaced] = useState<PlacedBox[]>([]);
  /**
   * Product codes the room could not hold, by name.
   *
   * A room that quietly drops two of the seven things somebody chose is worse
   * than one that says so, and stacking them is worse than either.
   */
  const [unplaced, setUnplaced] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [productToAdd, setProductToAdd] = useState('');
  const [dirty, setDirty] = useState(false);
  const [focus, setFocus] = useState(false);
  /** The products panel folded away. Only offered in full screen, where the
      canvas is the point and the panel is a third of the window. */
  const [hideProducts, setHideProducts] = useState(false);
  /**
   * Floor to ceiling, in millimetres.
   *
   * The only vertical number the room has. Wall thickness is deliberately not
   * asked for: nobody knows it offhand, and at this scale it would show up as a
   * shadow line and nothing else.
   */
  const [ceilingHeightMm, setCeilingHeightMm] = useState(DEFAULT_CEILING_MM);

  /**
   * Undo, as whole snapshots of the room.
   *
   * Entries are self-contained on purpose (see lib/dealer-kit/history): the
   * planner we studied stores something cleverer and undoing a fresh addition
   * crashes it outright. One entry per GESTURE, not per frame - a drag emits a
   * state every pointermove.
   */
  const [history, setHistory] = useState<History<RoomSnapshot>>(() =>
    newHistory({ outline: STARTING_ROOM, placed: [], openings: [], finishes: {} }),
  );

  /** Doors and windows, and which one (or which wall) is being worked on. */
  const [openings, setOpenings] = useState<Opening[]>([]);
  const [selectedOpeningId, setSelectedOpeningId] = useState<string | null>(null);
  const [selectedWallIndex, setSelectedWallIndex] = useState<number | null>(null);
  /** What the surfaces look like. Stored as ids, resolved to colour on render. */
  const [finishes, setFinishes] = useState<Finishes>({});

  /**
   * The catalogue we were sent from, if any.
   *
   * Validated as an id shape before it is put back into a URL: a query
   * parameter is caller-controlled, and interpolating it unchecked into an
   * href is how a link ends up pointing somewhere it should not.
   */
  const searchParams = useSearchParams();
  const fromParam = searchParams.get('from');
  const cameFrom =
    fromParam && /^[0-9a-fA-F-]{36}$/.test(fromParam) ? fromParam : null;
  const hydrated = useRef(false);

  useEffect(() => {
    setSelectionId(window.localStorage.getItem(LAST_SELECTION_KEY));
  }, []);

  /**
   * Remembered so a chosen product survives the list being replaced by the next
   * search, and so Add knows what was picked without re-querying.
   */
  const [chosen, setChosen] = useState<{ id: string; code: string; name: string } | null>(null);
  /** Everything the picker has fetched this session, so onChange can resolve an id. */
  const seenProducts = useRef(new Map<string, { code: string; name: string }>());

  const {
    data: selection,
    isLoading: selectionLoading,
    isError: selectionMissing,
  } = useQuery({
    queryKey: ['dealer-kit', 'selection', selectionId],
    queryFn: () => getSelection(selectionId!),
    enabled: !!selectionId,
    retry: false,
  });

  /**
   * Forget a remembered design the server will not give us.
   *
   * The last design is remembered in this browser so a reload is not a restart,
   * but the row can disappear underneath it - deleted, or belonging to a
   * company the user has since switched away from. Left alone, every read 404s,
   * every add fails against the stale id, and the designer is bricked until
   * somebody clears their browser storage. Forgetting it starts a fresh one on
   * the next action, which is what the user wanted anyway.
   */
  useEffect(() => {
    if (!selectionMissing || !selectionId) return;
    window.localStorage.removeItem(LAST_SELECTION_KEY);
    setSelectionId(null);
    hydrated.current = false;
    setPlaced([]);
  }, [selectionMissing, selectionId]);

  // The server owns which products and their sizes; local state owns where they
  // stand. Rebuilding on every selection change keeps the two in step without a
  // second copy of the line list.
  useEffect(() => {
    if (!selection) return;
    setPlaced((current) => {
      const next = boxesForSelection(selection, current);
      /*
        Boxes somebody has already positioned are FIXED - a saved placement or
        one being dragged right now is a decision, and this is not the place to
        overrule it. Everything else is arriving without a position, and it used
        to arrive on a fixed 800mm grid regardless of its real size: a 1,700mm
        bath was already standing inside its neighbour before the user had
        touched anything.
      */
      const settled = new Set([
        ...current.map((box) => box.id),
        ...(selection.room?.placements ?? []).map((placement) => placement.lineId),
      ]);
      // From a ref: repacking on every wall drag would move the room's contents
      // out from under whoever is resizing it.
      const { boxes, unplaced } = packBoxes(next, outlineRef.current, settled);
      // Reported, never silently dropped or stacked. The room genuinely cannot
      // hold them and the person choosing needs to know which.
      setUnplaced(Array.from(new Set(unplaced.map((box) => box.code))));

      if (justAdded.current) {
        // The last copy of that product is the one just added.
        const added = [...boxes].reverse().find((box) => box.productId === justAdded.current);
        if (added) {
          setSelectedId(added.id);
          justAdded.current = null;
        }
      }
      return boxes;
    });
    if (!hydrated.current && selection.room?.outline?.length) {
      setOutline(selection.room.outline);
      if (selection.room.ceilingHeightMm) setCeilingHeightMm(selection.room.ceilingHeightMm);
      setOpenings(selection.room.openings ?? []);
      setFinishes(selection.room.finishes ?? {});
      hydrated.current = true;
    }
  }, [selection]);

  /**
   * Products ticked in a published catalogue, turned into real lines.
   *
   * The catalogue is anonymous, so the picks arrive in localStorage rather than
   * on a Selection. They are consumed ONCE and cleared immediately: a list that
   * survived would re-add itself every time the designer opened, and the user
   * would be deleting the same basin forever.
   *
   * Quantities are set absolutely (1 each) rather than incremented, so a
   * product already in the design is not silently doubled.
   */
  const consumedPicks = useRef(false);
  /** The product just added, waiting for its box to be rebuilt. */
  const justAdded = useRef<string | null>(null);

  const ensureSelection = useCallback(async (): Promise<string> => {
    if (selectionId) return selectionId;
    const created = await createSelection();
    window.localStorage.setItem(LAST_SELECTION_KEY, created.id);
    setSelectionId(created.id);
    queryClient.setQueryData(['dealer-kit', 'selection', created.id], created);
    return created.id;
  }, [selectionId, queryClient]);

  useEffect(() => {
    if (consumedPicks.current) return;
    if (searchParams.get('picks') !== '1') return;
    const picks = readPicks();
    if (picks.length === 0) return;

    consumedPicks.current = true;
    clearPicks();

    void (async () => {
      try {
        const id = await ensureSelection();
        for (const productId of picks) {
          await setSelectionLine(id, productId, 1);
        }
        queryClient.invalidateQueries({ queryKey: ['dealer-kit', 'selection', id] });
        toast.success(
          `${picks.length} product${picks.length === 1 ? '' : 's'} added from the catalogue`,
        );
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : 'Could not add the products you chose',
        );
      }
    })();
  }, [searchParams, ensureSelection, queryClient]);

  const lineMutation = useMutation({
    mutationFn: async ({ productId, quantity }: { productId: string; quantity: number }) => {
      const id = await ensureSelection();
      return setSelectionLine(id, productId, quantity);
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(['dealer-kit', 'selection', updated.id], updated);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const roomMutation = useMutation({
    mutationFn: async (room: Room) => {
      const id = await ensureSelection();
      return saveRoom(id, room);
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(['dealer-kit', 'selection', updated.id], updated);
      setDirty(false);
      toast.success('Design saved');
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const addProduct = useCallback(() => {
    if (!chosen) return;
    const line = selection?.lines.find((row) => row.productId === chosen.id);
    // Remembered so the new box can be SELECTED once it exists: an item that
    // arrives already selected shows its toolbar and its clearances, which is
    // how somebody finds out either exists.
    justAdded.current = chosen.id;
    lineMutation.mutate({ productId: chosen.id, quantity: (line?.quantity ?? 0) + 1 });
    setProductToAdd('');
    setChosen(null);
  }, [chosen, selection, lineMutation]);

  const moveBox = useCallback(
    (boxId: string, x: number, y: number, rotation: number) => {
      setDirty(true);
      setPlaced((current) => {
        const box = current.find((candidate) => candidate.id === boxId);
        if (!box) return current;

        /*
          Rotation arrives with the position because backing onto a wall turns
          the product: the plan decides orientation, not the user.

          A wall is TIDIED (a drop half through one is a clear intent the system
          can fix, AC-V4). Another unit is REFUSED. The two are not the same
          thing: nudging a box off a wall lands it where the user meant, while
          nudging it off a neighbour would land it somewhere they did not - so
          an overlapping move is simply never committed, and `resolveDrag`
          slides along the obstruction instead of stopping dead against it.
        */
        const settled = resolveDrag(box, { x, y, rotation }, current, outline);
        return current.map((candidate) =>
          candidate.id === boxId ? ({ ...candidate, ...settled } as PlacedBox) : candidate,
        );
      });
    },
    [outline],
  );

  /**
   * A product dropped in the 3D view gets the same wall magnetism as one
   * dropped in the plan.
   *
   * The plan does the snapping before it reports a position, so without this
   * the two views would behave differently for the same gesture - and the one
   * that felt broken would be whichever the user tried second.
   */
  const moveBoxSnapped = useCallback(
    (boxId: string, x: number, y: number, rotation: number) => {
      const box = placed.find((candidate) => candidate.id === boxId);
      if (!box) return;
      const snapped = snapToWall({ ...box, x, y, rotation }, outline);
      const result = snapped?.box ?? { x, y, rotation };
      moveBox(boxId, result.x, result.y, result.rotation);
    },
    [placed, outline, moveBox],
  );

  const changeOutline = useCallback((next: Point[]) => {
    setOutline(next);
    // A door belongs to a wall, so a shorter wall either carries its door
    // inward or cannot hold it at all. Leaving it where it was would draw an
    // opening hanging in space outside the room.
    setOpenings((current) => fitOpenings(current, wallLengths(next)));
    setDirty(true);
  }, []);

  const rotateSelected = useCallback(() => {
    setDirty(true);
    setHistory((current) => pushHistory(current, { outline, placed, openings, finishes }));
    setPlaced((current) =>
      current.map((box) =>
        box.id === selectedId ? { ...box, rotation: (box.rotation + 90) % 360 } : box,
      ),
    );
  }, [selectedId, outline, placed, openings, finishes]);

  const removeBoxById = useCallback(
    (boxId: string) => {
      const box = placed.find((candidate) => candidate.id === boxId);
      if (!box) return;

      // Take the clicked copy out locally FIRST, renumbering what is left, then
      // tell the server the new count. Sending only "one fewer" would leave the
      // rebuild deleting the last copy instead of the one they clicked.
      setHistory((current) => pushHistory(current, { outline, placed, openings, finishes }));
      const remaining = removeBox(placed, box.id);
      setPlaced(remaining);
      setSelectedId((current) => (current === box.id ? null : current));
      setDirty(true);
      lineMutation.mutate({
        productId: box.productId,
        quantity: quantityOf(remaining, box.productId),
      });
    },
    [placed, lineMutation, outline, openings, finishes],
  );

  const removeSelected = useCallback(() => {
    if (selectedId) removeBoxById(selectedId);
  }, [selectedId, removeBoxById]);

  /** Record the room as it stands now, as one undoable step. */
  const commit = useCallback(() => {
    setHistory((current) => pushHistory(current, { outline, placed, openings, finishes }));
  }, [outline, placed, openings, finishes]);

  /**
   * Restore a snapshot, and tell the server about any product it changes.
   *
   * Undoing a delete has to put the LINE back, not just the box: the room is
   * local but what was chosen lives on the server, and a room showing two
   * basins against a selection that says one is the kind of disagreement
   * nobody notices until the quote is wrong. Quantities are pushed one product
   * at a time and the cache is refreshed once at the end, so two products
   * changing in the same step cannot overwrite each other's response.
   */
  const applySnapshot = useCallback(
    async (snapshot: RoomSnapshot) => {
      setOutline(snapshot.outline);
      setPlaced(snapshot.placed);
      setOpenings(snapshot.openings ?? []);
      setFinishes(snapshot.finishes ?? {});
      setSelectedId(null);
      setSelectedOpeningId(null);
      setDirty(true);

      if (!selection) return;
      const wanted = new Map<string, number>();
      for (const box of snapshot.placed) {
        wanted.set(box.productId, (wanted.get(box.productId) ?? 0) + 1);
      }
      for (const line of selection.lines) {
        if (!wanted.has(line.productId)) wanted.set(line.productId, 0);
      }

      const changed = [...wanted.entries()].filter(([productId, quantity]) => {
        const line = selection.lines.find((row) => row.productId === productId);
        return (line?.quantity ?? 0) !== quantity;
      });
      if (changed.length === 0) return;

      try {
        for (const [productId, quantity] of changed) {
          await setSelectionLine(selection.id, productId, quantity);
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Could not undo that');
      } finally {
        queryClient.invalidateQueries({ queryKey: ['dealer-kit', 'selection', selection.id] });
      }
    },
    [selection, queryClient],
  );

  const undoStep = useCallback(() => {
    /**
     * The edit in hand comes off first.
     *
     * Snapshots are taken BEFORE a change, so `present` is the room as it was
     * before whatever the user just did. If the live room has moved on from
     * it, one undo means "put that back" - stepping into the past instead
     * would swallow two edits at once, which is what it did until this check
     * existed. The live state goes onto the redo trail so it can come back.
     */
    const live: RoomSnapshot = { outline, placed, openings, finishes };
    if (JSON.stringify(live) !== JSON.stringify(history.present)) {
      setHistory({ ...history, future: [live, ...history.future] });
      void applySnapshot(history.present);
      return;
    }

    const next = undo(history);
    if (next === history) return;
    setHistory(next);
    void applySnapshot(next.present);
  }, [history, applySnapshot, outline, placed, openings, finishes]);

  const redoStep = useCallback(() => {
    const next = redo(history);
    if (next === history) return;
    setHistory(next);
    void applySnapshot(next.present);
  }, [history, applySnapshot]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      // Never steal Ctrl-Z from a field somebody is typing in.
      if (target && /^(INPUT|TEXTAREA)$/.test(target.tagName)) return;
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'z') return;
      event.preventDefault();
      if (event.shiftKey) redoStep();
      else undoStep();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [undoStep, redoStep]);

  /**
   * Another one of the same product, offset so it is visibly a second copy.
   *
   * Placement comes from the rebuild, which puts the new copy at its first-guess
   * position; the user then drags it where it belongs, and the wall magnet does
   * the orienting.
   */
  const duplicateBox = useCallback(
    (boxId: string) => {
      const box = placed.find((candidate) => candidate.id === boxId);
      if (!box) return;
      commit();
      lineMutation.mutate({
        productId: box.productId,
        quantity: quantityOf(placed, box.productId) + 1,
      });
    },
    [placed, commit, lineMutation],
  );

  /**
   * Stamp a door or window into the selected wall.
   *
   * Placed at the middle of the wall at a standard size, because that is
   * almost always somewhere sensible and moving it is one drag. Refused when
   * the wall is too short for the opening rather than narrowing it silently -
   * a size nobody chose is a size somebody orders to.
   */
  const addOpening = useCallback(
    (kind: OpeningKind) => {
      if (selectedWallIndex === null) {
        toast.error('Pick a wall first, then add the door or window to it.');
        return;
      }
      const lengths = wallLengths(outline);
      const length = lengths[selectedWallIndex];
      const sizes = defaultsFor(kind);
      const candidate: Opening = {
        id: `opening-${selectedWallIndex}-${Math.round(length)}-${openings.length + 1}`,
        kind,
        wallIndex: selectedWallIndex,
        offsetMm: length / 2,
        ...sizes,
      };
      const fitted = fitOpening(candidate, length);
      if (!fitted) {
        toast.error('That wall is too short for this opening.');
        return;
      }
      commit();
      setOpenings((current) => [...current, fitted]);
      setSelectedOpeningId(fitted.id);
      setDirty(true);
    },
    [selectedWallIndex, outline, openings.length, commit],
  );

  const moveOpening = useCallback(
    (openingId: string, offsetMm: number, wallIndex: number) => {
      const lengths = wallLengths(outline);
      setDirty(true);
      setOpenings((current) =>
        current.map((opening) => {
          if (opening.id !== openingId) return opening;
          // Typed or dragged, it still has to fit the wall it lands on: an
          // offset past the end would draw a door hanging in space.
          const fitted = fitOpening(
            { ...opening, offsetMm, wallIndex },
            lengths[wallIndex] ?? 0,
          );
          return fitted ?? opening;
        }),
      );
    },
    [outline],
  );

  /** Resize the selected opening, refusing anything its wall cannot hold. */
  const resizeOpening = useCallback(
    (patch: Partial<Pick<Opening, 'widthMm' | 'heightMm' | 'sillMm'>>) => {
      const lengths = wallLengths(outline);
      commit();
      setOpenings((current) =>
        current.map((opening) => {
          if (opening.id !== selectedOpeningId) return opening;
          const fitted = fitOpening({ ...opening, ...patch }, lengths[opening.wallIndex] ?? 0);
          return fitted ?? opening;
        }),
      );
      setDirty(true);
    },
    [selectedOpeningId, outline, commit],
  );

  const removeOpening = useCallback(() => {
    if (!selectedOpeningId) return;
    commit();
    setOpenings((current) => current.filter((opening) => opening.id !== selectedOpeningId));
    setSelectedOpeningId(null);
    setDirty(true);
  }, [selectedOpeningId, commit]);

  /**
   * Put the canvas back to an empty room.
   *
   * The designer reopens the last design so a reload is not a restart, which
   * means without this there is no way to start a second one - the first design
   * would follow the user forever. The old design is NOT deleted: it is saved
   * work, and forgetting it here is not the same as throwing it away.
   */
  const startFresh = useCallback(() => {
    window.localStorage.removeItem(LAST_SELECTION_KEY);
    hydrated.current = false;
    setSelectionId(null);
    setPlaced([]);
    setOutline(STARTING_ROOM);
    setOpenings([]);
    setFinishes({});
    setSelectedId(null);
    setSelectedOpeningId(null);
    setSelectedWallIndex(null);
    setDirty(false);
    // A new design starts with no history: undoing into the previous design
    // would silently resurrect work the user just set aside.
    setHistory(newHistory({ outline: STARTING_ROOM, placed: [], openings: [], finishes: {} }));
  }, []);

  const save = useCallback(() => {
    roomMutation.mutate({
      outline,
      placements: placementsOf(placed),
      openings,
      finishes,
      ceilingHeightMm,
    });
  }, [outline, placed, openings, finishes, ceilingHeightMm, roomMutation]);

  const collisions = useMemo(() => {
    const clashing = new Set<string>();
    for (let i = 0; i < placed.length; i += 1) {
      for (let j = i + 1; j < placed.length; j += 1) {
        if (boxesOverlap(placed[i], placed[j])) {
          clashing.add(placed[i].id);
          clashing.add(placed[j].id);
        }
      }
    }
    return clashing;
  }, [placed]);

  const area = areaSquareMetres(outline);
  const selectedOpening = openings.find((opening) => opening.id === selectedOpeningId) ?? null;
  const selectedBox = placed.find((box) => box.id === selectedId) ?? null;
  const estimated = placed.filter((box) => box.isEstimated);
  const estimatedNames = Array.from(new Set(estimated.map((box) => box.code)));
  const lines = selection?.lines ?? [];
  const busy = lineMutation.isPending || roomMutation.isPending;

  return (
    <FocusShell active={focus} onExit={() => setFocus(false)}>
    <div className="flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
      <div className="min-w-0 flex-1">
        <Card>
          <CardHeader className="flex flex-col gap-3 pb-3 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle className="text-sm">The room</CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              {dirty && (
                <span className="text-xs text-muted-foreground">Unsaved changes</span>
              )}
              {cameFrom && (
                // Arriving from a catalogue means there is somewhere to go back
                // to. Without this the only way back is the sidebar, which
                // loses which catalogue you were reading.
                <Button size="sm" variant="outline" asChild>
                  <Link href={`/dealer-kit/pages/${cameFrom}`}>
                    <ArrowLeft className="size-4" />
                    Back to catalogue
                  </Link>
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                aria-label="Undo"
                title="Undo (Ctrl+Z)"
                disabled={!canUndo(history) || busy}
                onClick={undoStep}
              >
                <Undo2 className="size-4" />
              </Button>
              <Button
                size="sm"
                variant="outline"
                aria-label="Redo"
                title="Redo (Ctrl+Shift+Z)"
                disabled={!canRedo(history) || busy}
                onClick={redoStep}
              >
                <Redo2 className="size-4" />
              </Button>
              <FocusToggle active={focus} onToggle={setFocus} label="room" />
              <Button size="sm" variant="outline" onClick={startFresh} disabled={busy}>
                <Plus className="size-4" />
                New design
              </Button>
              <Button size="sm" variant="outline" asChild disabled={!selectionId}>
                <Link
                  href={
                    selectionId
                      ? `/dealer-kit/design/summary?selection=${selectionId}`
                      : '/dealer-kit/design/summary'
                  }
                >
                  <ReceiptText className="size-4" />
                  Summary
                </Link>
              </Button>
              <Button size="sm" variant={dirty ? 'primary' : 'outline'} onClick={save} disabled={busy}>
                <Check className="size-4" />
                {busy ? 'Saving' : 'Save design'}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <Tabs defaultValue="plan">
              <TabsList variant="default">
                <TabsTrigger value="plan">Plan</TabsTrigger>
                <TabsTrigger value="3d">3D</TabsTrigger>
              </TabsList>

              <TabsContent value="plan" className="pt-3">
                <RoomPlan
                  outline={outline}
                  boxes={placed}
                  selectedBoxId={selectedId}
                  onOutlineChange={changeOutline}
                  onMoveBox={moveBox}
                  onSelectBox={setSelectedId}
                  onRotateBox={(boxId) => {
                    setSelectedId(boxId);
                    rotateSelected();
                  }}
                  onDuplicateBox={duplicateBox}
                  onRemoveBox={(boxId) => {
                    setSelectedId(boxId);
                    removeSelected();
                  }}
                  onCommit={commit}
                  openings={openings}
                  selectedOpeningId={selectedOpeningId}
                  onSelectOpening={setSelectedOpeningId}
                  onMoveOpening={moveOpening}
                  selectedWallIndex={selectedWallIndex}
                  onSelectWall={setSelectedWallIndex}
                  finishes={finishes}
                />

                {/*
                  Openings are added to a WALL, so the wall comes first. Naming
                  the chosen wall by its length rather than an index keeps the
                  panel readable - "the 4000mm wall" is a thing you can see.
                */}
                <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-border p-2">
                  <span className="text-xs text-muted-foreground">
                    {selectedWallIndex === null
                      ? 'Click a wall to add a door or window'
                      : `Wall ${selectedWallIndex + 1} (${Math.round(
                          wallLengths(outline)[selectedWallIndex] ?? 0,
                        )} mm)`}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={selectedWallIndex === null}
                    onClick={() => addOpening('door')}
                  >
                    <DoorOpen className="size-4" />
                    Door
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={selectedWallIndex === null}
                    onClick={() => addOpening('window')}
                  >
                    <PanelTop className="size-4" />
                    Window
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={selectedWallIndex === null}
                    onClick={() => addOpening('opening')}
                  >
                    <Frame className="size-4" />
                    Opening
                  </Button>
                </div>

                {/*
                  Finishes are per SURFACE, like the planner we studied: one
                  wall at a time, and the floor on its own. "Apply to all walls"
                  is deliberately absent - a bathroom with one feature wall is
                  the normal case, not the exception.
                */}
                <div className="mt-2 space-y-2 rounded-md border border-border p-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="w-24 text-xs text-muted-foreground">Floor</span>
                    {FLOOR_FINISHES.map((finish) => (
                      <button
                        key={finish.id}
                        type="button"
                        aria-label={`Floor: ${finish.label}`}
                        aria-pressed={floorFinishId(finishes) === finish.id}
                        title={finish.label}
                        data-dk-floor-finish={finish.id}
                        className={`size-6 rounded border-2 ${
                          floorFinishId(finishes) === finish.id
                            ? 'border-primary'
                            : 'border-border'
                        }`}
                        style={{ backgroundColor: finish.color }}
                        onClick={() => {
                          commit();
                          setFinishes((current) => setFloorFinish(current, finish.id));
                          setDirty(true);
                        }}
                      />
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="w-24 text-xs text-muted-foreground">
                      {selectedWallIndex === null
                        ? 'Wall (pick one)'
                        : `Wall ${selectedWallIndex + 1}`}
                    </span>
                    {WALL_FINISHES.map((finish) => (
                      <button
                        key={finish.id}
                        type="button"
                        aria-label={`Wall: ${finish.label}`}
                        aria-pressed={
                          selectedWallIndex !== null &&
                          wallFinishId(finishes, selectedWallIndex) === finish.id
                        }
                        title={finish.label}
                        disabled={selectedWallIndex === null}
                        data-dk-wall-finish-swatch={finish.id}
                        className={`size-6 rounded border-2 disabled:opacity-40 ${
                          selectedWallIndex !== null &&
                          wallFinishId(finishes, selectedWallIndex) === finish.id
                            ? 'border-primary'
                            : 'border-border'
                        }`}
                        style={{ backgroundColor: finish.color }}
                        onClick={() => {
                          if (selectedWallIndex === null) return;
                          commit();
                          setFinishes((current) =>
                            setWallFinish(current, selectedWallIndex, finish.id),
                          );
                          setDirty(true);
                        }}
                      />
                    ))}
                  </div>
                </div>

                {selectedOpening && (
                  <div
                    className="mt-2 flex flex-wrap items-end gap-3 rounded-md border border-border p-2"
                    data-dk-opening-editor
                  >
                    <div className="flex flex-col gap-1">
                      <Label htmlFor="dk-opening-width" className="text-xs">
                        Width
                      </Label>
                      <LiveNumberInput
                        key={selectedOpening.id}
                        id="dk-opening-width"
                        className="h-8 w-24"
                        min={1}
                        rules={{ min: 1 }}
                        value={selectedOpening.widthMm}
                        onCommit={(widthMm) => resizeOpening({ widthMm })}
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <Label htmlFor="dk-opening-height" className="text-xs">
                        Height
                      </Label>
                      <LiveNumberInput
                        key={selectedOpening.id}
                        id="dk-opening-height"
                        className="h-8 w-24"
                        min={1}
                        rules={{ min: 1 }}
                        value={selectedOpening.heightMm}
                        onCommit={(heightMm) => resizeOpening({ heightMm })}
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      {/* Position as a NUMBER, not only as a drag. The 3D view
                          has no plan to drag along, and a door 1200 from the
                          corner is how a builder describes it anyway. */}
                      <Label htmlFor="dk-opening-offset" className="text-xs">
                        From corner
                      </Label>
                      <LiveNumberInput
                        key={selectedOpening.id}
                        id="dk-opening-offset"
                        className="h-8 w-24"
                        min={0}
                        rules={{ min: 0 }}
                        value={Math.round(selectedOpening.offsetMm)}
                        onCommit={(offsetMm) =>
                          moveOpening(selectedOpening.id, offsetMm, selectedOpening.wallIndex)
                        }
                      />
                    </div>
                    <div className="flex flex-col gap-1">
                      <Label htmlFor="dk-opening-wall" className="text-xs">
                        Wall
                      </Label>
                      <select
                        id="dk-opening-wall"
                        className="h-8 w-24 rounded-md border border-border bg-background px-2 text-sm"
                        value={selectedOpening.wallIndex}
                        onChange={(event) =>
                          moveOpening(
                            selectedOpening.id,
                            selectedOpening.offsetMm,
                            Number(event.target.value),
                          )
                        }
                      >
                        {wallLengths(outline).map((length, index) => (
                          <option key={index} value={index}>
                            {index + 1} ({Math.round(length)} mm)
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="flex flex-col gap-1">
                      <Label htmlFor="dk-opening-sill" className="text-xs">
                        Sill
                      </Label>
                      <LiveNumberInput
                        key={selectedOpening.id}
                        id="dk-opening-sill"
                        className="h-8 w-24"
                        min={0}
                        rules={{ min: 0 }}
                        value={selectedOpening.sillMm}
                        onCommit={(sillMm) => resizeOpening({ sillMm })}
                      />
                    </div>
                    <span className="text-xs text-muted-foreground">mm</span>
                    <Button size="sm" variant="outline" onClick={removeOpening}>
                      <Trash2 className="size-4" />
                      Remove
                    </Button>
                  </div>
                )}
                {/* The gestures, said once, where they are used. Everything
                    here was already possible and none of it was discoverable. */}
                <p className="mt-2 text-xs text-muted-foreground">
                  Click a wall length to type it. Drag a wall or a corner to reshape the room.
                  Click a product to select it - its toolbar and the gaps either side of it
                  appear on the plan. Middle-drag (or shift-drag) to pan, scroll to zoom, and
                  Fit puts the whole room back in view.
                </p>
              </TabsContent>

              <TabsContent value="3d" className="pt-3">
                <RoomScene
                  outline={outline}
                  boxes={placed}
                  selectedBoxId={selectedId}
                  /*
                    ON the object, not under the canvas. The plan already puts
                    rotate, copy and remove over the thing you clicked, and a
                    bar at the bottom of a 3D view makes you look away from it
                    to act on it. Icon-only for the same reason the plan's is:
                    three labelled buttons over a 600mm box cover the box.
                  */
                  selectionToolbar={
                    selectedBox ? (
                      <>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="size-7 p-0"
                          onClick={rotateSelected}
                          title="Rotate"
                          aria-label={`Rotate ${selectedBox.label}`}
                        >
                          <RotateCw className="size-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="size-7 p-0"
                          onClick={() => duplicateBox(selectedBox.id)}
                          disabled={busy}
                          title="Duplicate"
                          aria-label={`Duplicate ${selectedBox.label}`}
                        >
                          <Copy className="size-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="size-7 p-0"
                          onClick={removeSelected}
                          disabled={busy}
                          title="Remove"
                          aria-label={`Remove ${selectedBox.label}`}
                        >
                          <Trash2 className="size-3.5 text-destructive" />
                        </Button>
                      </>
                    ) : null
                  }
                  onSelectBox={setSelectedId}
                  ceilingHeightMm={ceilingHeightMm}
                  openings={openings}
                  finishes={finishes}
                  onMoveBox={moveBoxSnapped}
                  onMoveOpening={moveOpening}
                  onSelectOpening={setSelectedOpeningId}
                  onCommit={commit}
                />
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Label htmlFor="dk-ceiling-height" className="text-xs text-muted-foreground">
                    Ceiling height
                  </Label>
                  <div className="w-28">
                    <LiveNumberInput
                      id="dk-ceiling-height"
                      min={1000}
                      rules={{ min: 1000, max: 6000 }}
                      max={6000}
                      step={50}
                      className="h-8"
                      value={ceilingHeightMm}
                      // Typed text stays as typed; only a figure inside the range is kept.
                      onCommit={(typed) => {
                        setCeilingHeightMm(typed);
                        setDirty(true);
                      }}
                    />
                  </div>
                  <span className="text-xs text-muted-foreground">mm</span>
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  Drag to orbit, scroll to zoom. Walls between you and the room drop away as
                  you turn. Each product is a box at its real size.
                </p>
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      </div>

      <aside
        className={`w-full shrink-0 ${focus && hideProducts ? 'lg:w-auto' : 'lg:w-80'}`}
      >
        <CollapsiblePanel
          title="Products in this room"
          collapsed={focus && hideProducts}
          onToggle={setHideProducts}
          side="end"
          enabled={focus}
        >
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Products in this room</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 pb-4">
            <div className="flex gap-2">
              <div className="min-w-0 flex-1">
                <SearchableSelect
                  id="dk-design-add-product"
                  value={productToAdd}
                  onChange={(id) => {
                    setProductToAdd(id);
                    const seen = seenProducts.current.get(id);
                    setChosen(seen ? { id, ...seen } : null);
                  }}
                  // Server-searched and paged. Static mode would cap the picker
                  // at one page of a 22,000-product catalogue.
                  fetchOptions={async (query, pageIndex) => {
                    const rows = await listPickerProducts(query, pageIndex);
                    for (const product of rows) {
                      seenProducts.current.set(product.id, {
                        code: product.code,
                        name: product.name,
                      });
                    }
                    return rows.map((product) => ({
                      value: product.id,
                      label: product.code,
                      description: product.name,
                    }));
                  }}
                  paginated
                  pageSize={PICKER_PAGE_SIZE}
                  selectedOption={
                    chosen
                      ? { value: chosen.id, label: chosen.code, description: chosen.name }
                      : undefined
                  }
                  renderOption={(option) => (
                    <span className="min-w-0">
                      <span className="block truncate font-mono text-xs">{option.label}</span>
                      {option.description && !sameDisplayText(option.label, option.description) ? (
                        <span className="block truncate text-xs text-muted-foreground">
                          {option.description}
                        </span>
                      ) : null}
                    </span>
                  )}
                  placeholder="Search products"
                />
              </div>
              <Button
                size="sm"
                aria-label="Add product to room"
                disabled={!productToAdd || busy}
                onClick={addProduct}
              >
                <Plus className="size-4" />
              </Button>
            </div>

            {selectionLoading && <Skeleton className="h-16 w-full" />}

            {!selectionLoading && lines.length === 0 && (
              <div className="rounded-lg border border-dashed border-border p-4 text-center">
                <BoxIcon className="mx-auto size-5 text-muted-foreground" />
                <p className="mt-2 text-xs font-medium text-foreground">Nothing placed yet</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Add a product and it appears in the room at its real size.
                </p>
              </div>
            )}

            {lines.map((line) => (
              <SelectionRow
                key={line.lineId}
                line={line}
                currency={selection?.currency ?? 'MYR'}
                selected={placed.some(
                  (box) => box.productId === line.productId && box.id === selectedId,
                )}
                clashing={placed.some(
                  (box) => box.productId === line.productId && collisions.has(box.id),
                )}
                removing={busy}
                onSelect={() => {
                  const box = placed.find((candidate) => candidate.productId === line.productId);
                  if (box) setSelectedId(box.id);
                }}
                onRemove={() => {
                  // The LAST copy of this product, matching what the plan does
                  // when nothing is selected: `removeBox` renumbers the
                  // survivors, so the one that disappears is the one the count
                  // says went.
                  const copies = placed.filter(
                    (candidate) => candidate.productId === line.productId,
                  );
                  const target = copies[copies.length - 1];
                  if (target) removeBoxById(target.id);
                }}
              />
            ))}

            {unplaced.length > 0 && (
              <Alert variant="warning" appearance="light" data-dk-unplaced>
                <AlertIcon>
                  <AlertTriangle />
                </AlertIcon>
                <AlertContent>
                  <AlertTitle className="text-xs">
                    {unplaced.length === 1 ? 'One item' : `${unplaced.length} items`} did not fit
                  </AlertTitle>
                  <AlertDescription className="text-xs">
                    {/* Named, for the same reason estimated sizes are named: "some
                        items" leaves the user hunting for which. Two things are
                        never put in the same space, so the honest answer to a room
                        that is too small is to say so rather than to stack them. */}
                    {unplaced.join(', ')} could not be placed without overlapping
                    something else. Make the room larger, or remove something.
                  </AlertDescription>
                </AlertContent>
              </Alert>
            )}

            {estimatedNames.length > 0 && (
              <Alert>
                <AlertIcon>
                  <AlertTriangle />
                </AlertIcon>
                {/* Title and description MUST share one AlertContent: the shared
                    Alert is a horizontal flex, so as bare siblings they become
                    two narrow columns and every word wraps. */}
                <AlertContent>
                  <AlertTitle className="text-xs">Sizes are estimated</AlertTitle>
                  <AlertDescription className="text-xs">
                    {/* Naming them matters: "one product" leaves the user hunting
                        for which box is the lie (AC-V2). */}
                    {estimatedNames.join(', ')} {estimatedNames.length === 1 ? 'is' : 'are'}{' '}
                    drawn at a default size because the catalogue has no dimensions.
                  </AlertDescription>
                </AlertContent>
              </Alert>
            )}

            {selection && selection.unavailableCount > 0 && (
              <Alert variant="destructive" appearance="light">
                <AlertIcon>
                  <AlertTriangle />
                </AlertIcon>
                <AlertContent>
                  <AlertTitle className="text-xs">
                    {selection.unavailableCount} product
                    {selection.unavailableCount === 1 ? '' : 's'} cannot be ordered
                  </AlertTitle>
                  <AlertDescription className="text-xs">
                    They stay in the design so you can see what changed, and they are left out
                    of the total.
                  </AlertDescription>
                </AlertContent>
              </Alert>
            )}

            <div className="mt-2 space-y-1 border-t border-border pt-2 text-xs text-muted-foreground">
              <div className="flex justify-between">
                <span>Room area</span>
                <span>{area.toFixed(1)} m²</span>
              </div>
              <div className="flex justify-between">
                <span>Products</span>
                <span>{placed.length}</span>
              </div>
              {selection?.total && (
                <div className="flex justify-between pt-1 text-sm font-medium text-foreground">
                  <span>Total</span>
                  <span>
                    {selection.currency} {selection.total}
                  </span>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
        </CollapsiblePanel>
      </aside>
    </div>
    </FocusShell>
  );
}

function SelectionRow({
  line,
  selected,
  clashing,
  onSelect,
  onRemove,
  removing,
  currency,
}: {
  line: SelectionLine;
  selected: boolean;
  clashing: boolean;
  onSelect: () => void;
  /**
   * Take one copy of this product out.
   *
   * On the ROW, not in a pair of buttons under the list. Rotating belongs to
   * the object in the plan and the scene - it is a spatial decision made while
   * looking at the room - but removing is a decision about the LIST, and a
   * shared button under it acts on whichever row happens to be selected, which
   * is one misclick away from deleting the wrong product.
   */
  onRemove: () => void;
  removing: boolean;
  /** Prices are never printed bare: "1020.00" is not a price, it is a number. */
  currency: string;
}) {
  return (
    // A div and not a button: a remove control inside a button is invalid HTML
    // and the browser's own recovery from it is to move the inner button out,
    // which puts the trash icon somewhere nobody put it.
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      aria-label={`Select ${line.productCode ?? line.productName}`}
      className={`flex cursor-pointer items-center justify-between gap-2 rounded-md border px-2 py-1.5 text-start ${
        selected ? 'border-primary bg-primary/5' : 'border-border'
      }`}
    >
      <span className="min-w-0">
        <span className="block truncate font-mono text-xs">
          {line.productCode ?? line.productName}
          {line.quantity > 1 && <span className="text-muted-foreground"> ×{line.quantity}</span>}
        </span>
        <span className="block text-xs text-muted-foreground">
          {line.dimensionsMm
            ? `${line.dimensionsMm.length} x ${line.dimensionsMm.width} x ${line.dimensionsMm.height} mm`
            : 'No dimensions in the catalogue'}
          {line.lineTotal ? ` · ${currency} ${line.lineTotal}` : ''}
        </span>
      </span>
      <span className="flex shrink-0 gap-1">
        {!line.isAvailable && (
          <Badge variant="destructive" className="text-xs">
            {line.unavailableReason === 'discontinued' ? 'Discontinued' : 'Unavailable'}
          </Badge>
        )}
        {clashing && (
          <Badge variant="warning" className="text-xs">
            Overlapping
          </Badge>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="size-7 shrink-0 p-0"
          disabled={removing}
          onClick={(event) => {
            // The row selects; the icon removes. Without this the click reaches
            // the row underneath and selects the product it just deleted.
            event.stopPropagation();
            onRemove();
          }}
          title="Remove"
          aria-label={`Remove ${line.productCode ?? line.productName}`}
        >
          <Trash2 className="size-3.5 text-destructive" />
        </Button>
      </span>
    </div>
  );
}

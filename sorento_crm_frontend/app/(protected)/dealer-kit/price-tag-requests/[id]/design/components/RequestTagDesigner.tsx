'use client';

/**
 * Designing a request's tags IS the template editor (D51).
 *
 * The captain's words: "the design page is different from my template design...
 * they should be the same layout, and how can I pull out the template from the
 * template I have designed." So this page hosts `TagCanvasEditor` - the same
 * toolbar, Layers panel, Inspector and D33-D44 interaction model - with the
 * request's lines as a rail above the Layers panel and the SELECTED line's tag
 * on the artboard.
 *
 * A line's tag is a `PlacedTag` cloned from a template. Editing it never
 * touches the template: the clone is what gets saved into the request's tag
 * sheet document. Switching lines remounts the editor on the other tag, which
 * is why the host keeps every tag's layers current rather than waiting for a
 * Save that would come too late.
 *
 * Arranging the sheets is a consequence rather than a chore: on save every
 * line's tag is laid out in line order, quantity times, and the Arrange half is
 * there to look at it and to drag a copy if somebody wants to.
 *
 * Autosave (D22, S8): every committed change - a layer edit, an arranged
 * pin - re-runs the `doc` memo below, and an effect on THAT schedules a
 * debounced save through `onAutosave`, which writes the request's DRAFT.
 * `onSave` - the manual button, Mark design ready, Print sheet - is a
 * different act on a different route: it snapshots the design into an
 * immutable version, which is what export and proof rendering read (B1).
 * Autosaving through that route wrote a version per second and buried the
 * deliberate saves in noise.
 *
 * `flush()` (the debounce's own pending value, sent now, awaited) covers every
 * moment a ~1s wait is too slow to trust: switching Design/Arrange, switching
 * lines, the manual Save button making sure it is not racing an autosave, and
 * leaving the page - which is this component unmounting (the back link, the
 * sidebar, the browser's own Back) plus `pagehide` for a refresh or a close.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import {
  ChevronLeft,
  Check,
  Copy,
  Download,
  History,
  LayoutTemplate,
  Loader2,
  MessageSquare,
  Eye,
  EyeOff,
  Maximize2,
  Minimize2,
  Package,
  Save,
  RefreshCw,
  Trash2,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type {
  LineTagData,
  PlacedTag,
  TagBindingData,
  TagLayer,
  TagSheetDoc,
  TagTemplate,
  TagTemplateDoc,
  TagTemplateFamily,
} from '@/lib/dealer-kit/tag-template-types';
import { familyLabel } from '@/lib/dealer-kit/tag-template-types';
import { lineFamily } from '@/lib/dealer-kit/line-family';
import {
  applyDesignToAllTags,
  applyDesignToSiblings,
  autoArrange,
  DEFAULT_IMPOSITION,
  defaultTemplateFor,
  resizeAllTags,
  resizeTag,
  STARTER_TEMPLATE_ID,
  starterTemplateFor,
  tagForTag,
  tagSizeBounds,
  tagSizePresets,
  tagsFromDoc,
  type ArrangeItem,
  type SheetGridConfig,
  type TagRequestTag,
} from '@/lib/dealer-kit/request-tags';
import { formatTagPrice } from '@/lib/dealer-kit/price-badge';
import { layerDisplayName } from '@/lib/dealer-kit/product-block';
import { TagCanvasEditor } from '@/app/(protected)/dealer-kit/tag-templates/components/TagCanvasEditor';
import type { ToolbarTrailingAction } from '@/app/(protected)/dealer-kit/tag-templates/components/CanvasToolbar';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { useKitLibrary } from '@/app/(protected)/dealer-kit/tag-templates/components/useTagBindings';
import { TagSizeControl } from '@/app/(protected)/dealer-kit/components/TagSizeControl';
import { useAutosave } from '@/hooks/useAutosave';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { ArrangeSheetView } from './ArrangeSheetView';
import { TemplatePickDialog } from './TemplatePickDialog';
import { SaveAsTemplateDialog } from './SaveAsTemplateDialog';
import { UpdateTemplateDialog } from './UpdateTemplateDialog';
import {
  getPriceTagRequest,
  resolveRequestTags,
  transitionPriceTagRequest,
  exportTagSheet,
  markReadyForCollection,
  type PriceTagRequestDetail,
  type PriceTagRequestLine,
  type PriceTagRequestTag,
} from '../../../../services/priceTagRequestService';
import {
  listReviewComments,
  setReviewCommentResolved,
} from '../../../../services/priceTagReviewService';
import {
  listRequestVersions,
  getRequestVersion,
  recheckTagDataChanges,
  resolveTagPin,
  restoreRequestVersion,
} from '../../../../services/priceTagDataService';
import { useTagDataChanges, tagDataChangesKey } from '../../../hooks/useTagDataChanges';
import {
  useDismissTagDataUpdate,
  useUpdateRequestTag,
} from '../../../hooks/useRequestTagMutations';
import { isTerminalPriceTagStatus } from '@/lib/dealer-kit/print-collection';
import {
  AUTO_UPDATE_STATUSES,
  type TagDataChangeSet,
} from '@/lib/dealer-kit/product-data-changes';
import ProductDataReviewDialog from '@/components/dealer-kit/ProductDataReviewDialog';
import ProductDataUpdatedDialog from '@/components/dealer-kit/ProductDataUpdatedDialog';
import RequestVersionsSheet from '@/components/dealer-kit/RequestVersionsSheet';
import DesignLightbox from '@/components/dealer-kit/DesignLightbox';
import type { TagSheetDesignPayload } from '@/lib/dealer-kit/design-payload';
import {
  canvasPinsForTag,
  openComments,
  openCountByTag,
  type ReviewComment,
} from '@/lib/dealer-kit/review-comments';
import {
  listPublishedTemplates,
  publishTemplate,
  updateTemplate as updateTagTemplate,
} from '../../../../services/tagTemplateService';
import { FocusShell, FocusToggle } from '../../../../components/FocusMode';
import { AutosaveIndicator } from '../../../../components/AutosaveIndicator';
import {
  useDeleteTagSizePreset,
  useTagSizesQuery,
} from '../../../../tag-sizes/hooks/useTagSizes';
import { SaveAsSizeDialog } from './SaveAsSizeDialog';
import { distinctName } from '@/lib/product-display';

let idSeq = 0;
function newTagId(): string {
  idSeq += 1;
  return `tag-${Date.now()}-${idSeq}`;
}

/**
 * D7/AC-S4-6: "Apply to all" can copy a layer whose subject points at a part
 * index a THINNER target line does not have - the source tag had a combo
 * with parts, the target has fewer (or none). Walks the freshly-applied
 * tags and resets any such layer's subject to the parent (`-1`, the same
 * value `subjectOf` already falls back to for an out-of-range index at
 * render time - this just makes the DOCUMENT agree, so the Inspector shows
 * "Parent" rather than a stale index the next time somebody opens it).
 * Returns the touched layers' display names, deduped, for the toast.
 */
function fallBackOutOfRangeSubjects(
  tags: Record<string, PlacedTag>,
  targetTagIds: string[],
  resolved: Map<string, LineTagData>,
): { tags: Record<string, PlacedTag>; affected: string[] } {
  const next = { ...tags };
  const affected: string[] = [];
  for (const tagId of targetTagIds) {
    const tag = next[tagId];
    if (!tag) continue;
    const partCount = resolved.get(tagId)?.parts.length ?? 0;
    let changed = false;
    const layers = tag.layers.map((layer) => {
      const props = layer.props;
      const subjectPart = 'subjectPart' in props ? props.subjectPart : undefined;
      if (typeof subjectPart !== 'number' || subjectPart < 0 || subjectPart < partCount) {
        return layer;
      }
      changed = true;
      const name = layerDisplayName(layer);
      if (!affected.includes(name)) affected.push(name);
      return { ...layer, props: { ...props, subjectPart: -1 } as TagLayer['props'] };
    });
    if (changed) next[tagId] = { ...tag, layers };
  }
  return { tags: next, affected };
}

interface Props {
  request: PriceTagRequestDetail;
  initialDoc: TagSheetDoc | null;
  /** The deliberate save: snapshots a new version, toasts, rethrows (B1/B3). */
  onSave: (doc: TagSheetDoc) => Promise<void>;
  /** The autosave: writes the draft, silent, rethrows so the indicator can
   *  report a failure. `keepalive` is set only on the page-teardown flush. */
  onAutosave: (doc: TagSheetDoc, options?: { keepalive?: boolean }) => Promise<void>;
}

export function RequestTagDesigner({
  request: initialRequest,
  initialDoc,
  onSave,
  onAutosave,
}: Props) {
  /**
   * Held in state, not read straight off the prop: Split (D3) changes the
   * request's own TAG set, and the rail, the arrangement and the resolver all
   * read it. The prop re-seeds it when the route hands over a different
   * request.
   */
  const [request, setRequest] = useState(initialRequest);
  useEffect(() => setRequest(initialRequest), [initialRequest]);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [mode, setMode] = useState<'design' | 'arrange'>('design');
  /** Full screen (D11, AC-S6-1): the same `FocusShell` the room designer uses.
   *  Both Design and Arrange sit inside it - the toggle is one control for
   *  either half, not a per-mode setting. */
  const [focus, setFocus] = useState(false);
  const [templates, setTemplates] = useState<TagTemplate[]>([]);
  const [templatesStatus, setTemplatesStatus] = useState<'loading' | 'loaded' | 'error'>(
    'loading',
  );
  const [resolvedRows, setResolvedRows] = useState<LineTagData[] | null>(null);
  const [pricesStatus, setPricesStatus] = useState<'loading' | 'loaded' | 'error'>('loading');

  /** One placement per REQUEST TAG, keyed by tag id (D3). The live layers
   *  live here. */
  const [tags, setTags] = useState<Record<string, PlacedTag>>(() => {
    const map: Record<string, PlacedTag> = {};
    for (const [tagId, tag] of tagsFromDoc(initialDoc)) map[tagId] = tag;
    return map;
  });
  /**
   * A per-A4 grid typed into the Tag Size panel THIS session, for a size
   * that names no template/preset to persist it on yet (S7, AC-S7-14) -
   * keyed `${width_mm}x${height_mm}` so it applies the moment a group of
   * that size is arranged, without waiting for a save. `gridForSize` below
   * checks this FIRST, ahead of the template/preset lookup.
   */
  const [customSheetGrids, setCustomSheetGrids] = useState<Record<string, SheetGridConfig>>({});
  /**
   * The size "Apply to all lines" (D24, S9) last set, persisted in the doc
   * (S9 review B2) so it also applies to a line that has not been opened
   * yet: without this, `applyTemplate` below would clone a not-yet-opened
   * line at its TEMPLATE's own print size, silently undoing what Apply to
   * all just did the moment somebody opened line 3.
   */
  const [defaultTagSize, setDefaultTagSize] = useState<{
    width_mm: number;
    height_mm: number;
  } | null>(initialDoc?.default_tag_size ?? null);

  /**
   * Which REQUEST TAG is open on the canvas.
   *
   * Not called `selectedTagId`: that name is already taken, one state below,
   * by the Arrange view's selected PLACED COPY (`<tagId>-c0`). The two are
   * different things and naming them the same would be a bug waiting to be
   * written.
   */
  const [selectedRequestTagId, setSelectedRequestTagId] = useState<string | null>(null);
  const [activeSheetIndex, setActiveSheetIndex] = useState(0);
  const [arrangeZoom, setArrangeZoom] = useState(1);
  const [selectedTagId, setSelectedTagId] = useState<string | null>(null);

  const [pickerLineId, setPickerLineId] = useState<string | null>(null);
  /** "Save as template" (S4, D1): the currently designed tag, published in one go. */
  const [saveTemplateOpen, setSaveTemplateOpen] = useState(false);
  /** "Update <template>" (S6, D6): republish the source template from this line's design. */
  const [updateTemplateOpen, setUpdateTemplateOpen] = useState(false);
  const [updatingTemplate, setUpdatingTemplate] = useState(false);

  /**
   * The one bulk apply worth undoing (S5, AC-S5-3): "Apply this design to all
   * lines", or the template picker's "Apply to all lines" checkbox, or a
   * single-line template replace (D11 - the confirm dialog that used to guard
   * an edited tag is gone; this is the safety net instead). A ref, not state:
   * nothing on screen reads it directly, the toast's own "Undo" action and the
   * Cmd/Ctrl+Z handler below are the only two callers, and it is deliberately
   * ONE slot rather than a stack - the next tags edit of any kind retires it,
   * the same way a single `Ctrl+Z` only ever means "undo the last thing".
   */
  const bulkUndoRef = useRef<Record<string, PlacedTag> | null>(null);

  // The salesperson's pinned change requests (r9 S2/D6). Fetched once: they
  // only change when a round is sent, which happens on the portal.
  const [reviewComments, setReviewComments] = useState<ReviewComment[]>([]);
  /** The `Comments` toolbar toggle. Armed by the first open pin (D6). */
  // Armed BY an open pin (D6), not on by default: a round that has been worked
  // off leaves markers sitting over the artwork marketing is now editing, with
  // a count of zero beside them, and that has to be turned off by hand every
  // time the designer opens. Set when the comments arrive, since at mount there
  // are none to count.
  const [commentsVisible, setCommentsVisible] = useState(false);
  /** The request's design history (r9 S5/D19). */
  const [historyOpen, setHistoryOpen] = useState(false);
  /** The version being read, and ITS own document - never the live one
   *  (matches `RequestDesignSection`'s own History View). */
  const [viewing, setViewing] = useState<{
    version: number;
    payload: TagSheetDesignPayload;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [transitioning, setTransitioning] = useState(false);
  const [printing, setPrinting] = useState(false);

  const library = useKitLibrary();

  // -- Loading ---------------------------------------------------------------

  // A failed fetch gets an explicit, stays-put error state with Retry (AC-S3-3)
  // rather than a toast that vanishes and leaves the canvas silent. Only
  // PUBLISHED templates are eligible for request design (AC-S5-2).
  //
  // The loading and error states gate the canvas, so only the FIRST read may
  // use them (R2, #726). A later re-read - after "Save as new template", after
  // S6's Update - refreshes the list in place: swapping the canvas out for
  // "Loading templates..." mid-session unmounts the editor, and a canvas that
  // comes back is a canvas that has lost its selection, its zoom and its undo
  // history, and that re-seeds its layers from the `doc` prop. A refresh that
  // fails keeps the list already on screen rather than replacing a working
  // canvas with a Retry button.
  const templatesLoadedOnceRef = useRef(false);
  const loadTemplates = useCallback(() => {
    if (!templatesLoadedOnceRef.current) setTemplatesStatus('loading');
    listPublishedTemplates()
      .then((rows) => {
        templatesLoadedOnceRef.current = true;
        setTemplates(rows);
        setTemplatesStatus('loaded');
      })
      .catch(() => {
        if (!templatesLoadedOnceRef.current) setTemplatesStatus('error');
      });
  }, []);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  // Same mechanism as loadTemplates just above: a failed resolve gets an
  // explicit, stays-put error state with Retry, not a toast that vanishes and
  // leaves the canvas open on blank data with no visible cause.
  const loadPrices = useCallback(() => {
    setPricesStatus('loading');
    resolveRequestTags(request.id)
      .then((rows) => {
        setResolvedRows(rows);
        setPricesStatus('loaded');
      })
      .catch(() => setPricesStatus('error'));
  }, [request.id]);

  useEffect(() => {
    loadPrices();
  }, [loadPrices]);

  // The change requests the salesperson pinned on the last proof (r9 S2/D6).
  // A failure leaves the canvas without markers rather than without a canvas.
  useEffect(() => {
    let cancelled = false;
    listReviewComments(request.id)
      .then((rows) => {
        if (cancelled) return;
        setReviewComments(rows);
        setCommentsVisible(openComments(rows).length > 0);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [request.id]);

  /**
   * D13 (AC-S9-2/S9-3): Done/Reopen from the canvas pin popover itself -
   * the request detail page's `RequestDesignSection` already has this
   * decision, the designer is where the fix is made so it belongs here too.
   * No new endpoint: the same PATCH review-comments call, then a re-fetch so
   * the marker greys, the rail's per-tag count drops and the "Mark design
   * ready (N open)" CTA updates together, off the SAME `reviewComments`
   * state the markers read.
   */
  const handleReviewPinResolve = useCallback(
    async (pinId: string, resolved: boolean) => {
      try {
        await setReviewCommentResolved(request.id, pinId, resolved);
        const rows = await listReviewComments(request.id);
        setReviewComments(rows);
      } catch {
        toast.error('Could not update the change request');
      }
    },
    [request.id],
  );

  // The r4 refresh-on-focus is GONE (r9 S5/D18).
  //
  // It re-resolved every tag whenever this tab regained focus, which is the
  // exact behaviour the product-data pin exists to stop: a price edited in
  // master data would walk onto an open canvas with nobody deciding. What
  // replaces it is the red dot on the LINES rail and the Review dialog below -
  // the same change, shown, with Keep current and Update tag as the two ways
  // out of it.

  // What master data has moved under the pinned tags (r9 S5/D18), polled
  // every 30s (AC-C1/AC-C2) instead of loaded once on mount - a product
  // edited in another tab now reaches this rail's red dot with no reload.
  const queryClient = useQueryClient();
  const [reviewTagId, setReviewTagId] = useState<string | null>(null);

  const { data: dataChanges = [] } = useTagDataChanges(request.id, {
    enabled: !isTerminalPriceTagStatus(request.status, request.print_by ?? null),
  });

  const changesByTag = useMemo(() => {
    const map = new Map<string, TagDataChangeSet>();
    for (const set of dataChanges) {
      if (set.changes.length > 0) map.set(set.tag_id, set);
    }
    return map;
  }, [dataChanges]);

  /**
   * r10 S8: tags whose pin was moved by an auto-update and nobody has
   * dismissed yet - the SAME red dot, now meaning "updated, not yet seen".
   * Read off the request's own tags, not the poll: once the pin has moved
   * the live diff is empty again, so the poll cannot say it happened.
   */
  const updatedTags = useMemo(() => {
    const map = new Map<string, PriceTagRequestTag>();
    for (const line of request.lines) {
      for (const tag of line.tags ?? []) {
        if (tag.data_updated_at) map.set(tag.id, tag);
      }
    }
    return map;
  }, [request.lines]);

  /**
   * "Check product data" (owner round finding 3): a Keep silences ONE drift
   * by recording its hash, and there was no way to ask again, so a red dot
   * silenced once stayed silent forever - even for a later, unrelated edit
   * that would have tripped the gate on its own. This re-arms every tag.
   */
  const recheckDataChanges = useCallback(async () => {
    try {
      const rows = await recheckTagDataChanges(request.id);
      queryClient.setQueryData(tagDataChangesKey(request.id), rows);
      const changed = rows.filter((set) => set.changes.length > 0).length;
      toast.success(
        changed > 0
          ? `${changed} tag${changed === 1 ? '' : 's'} changed`
          : 'Product data is up to date',
      );
    } catch {
      toast.error('Could not check product data');
    }
  }, [request.id, queryClient]);

  const decideTagPin = useCallback(
    async (tagId: string, action: 'update' | 'keep') => {
      try {
        await resolveTagPin(request.id, tagId, action);
        await queryClient.invalidateQueries({ queryKey: tagDataChangesKey(request.id) });
        if (action === 'update') {
          // The pin moved, so the canvas has to redraw against the new values.
          const rows = await resolveRequestTags(request.id);
          setResolvedRows(rows);
        }
        toast.success(action === 'update' ? 'Tag updated' : 'Kept the current tag');
      } catch {
        toast.error('Could not apply that decision');
      }
    },
    [request.id, queryClient],
  );

  /** One resolved row per TAG, keyed by tag id (D3). */
  const resolved = useMemo(() => {
    const map = new Map<string, LineTagData>();
    for (const row of resolvedRows ?? []) map.set(row.tag_id, row);
    return map;
  }, [resolvedRows]);

  /**
   * Every tag on the request, flattened with the line it prints and that
   * line's 1-based position - which is where its "1a" label comes from.
   *
   * A line with no tags yet (a request the backend has not tagged) still gets
   * one entry so the rail is never empty on a request that has lines.
   */
  const requestTags = useMemo(() => {
    const out: { tag: PriceTagRequestTag; line: PriceTagRequestLine; lineIndex: number }[] = [];
    request.lines.forEach((line, lineIndex) => {
      for (const tag of line.tags ?? []) out.push({ tag, line, lineIndex });
    });
    return out;
  }, [request.lines]);

  /** The same list in the shape `request-tags.ts` reads. */
  const tagRefs: TagRequestTag[] = useMemo(
    () =>
      requestTags.map(({ tag, line }) => ({
        id: tag.id,
        quantity: tag.quantity,
        print_excluded: Boolean(tag.print_excluded),
        line,
      })),
    [requestTags],
  );

  // -- Tags ------------------------------------------------------------------

  // A request-level default (S9 review B2) wins over the template's own
  // print size the moment a line clones one - "Apply to all lines" is
  // supposed to mean every line, including one nobody has opened yet.
  const applyTemplate = useCallback(
    (requestTag: TagRequestTag, template: TagTemplate) => {
      setTags((prev) => {
        let tag = tagForTag(requestTag, template, newTagId());
        if (defaultTagSize) {
          tag = resizeTag(tag, defaultTagSize.width_mm, defaultTagSize.height_mm);
        }
        return { ...prev, [requestTag.id]: tag };
      });
    },
    [defaultTagSize],
  );

  // The first tag opens by itself: this page exists to design, and a canvas
  // waiting to be told which tag is a click nobody needs to make. A row's own
  // Design action on the detail page's Lines tab preselects THAT tag via
  // `?tag=<tagId>` - honoured only on the initial pick, same as the fallback it
  // replaces. `?line=<lineId>` still works and resolves to that line's FIRST
  // tag, because every link written before S3 names a line.
  useEffect(() => {
    if (selectedRequestTagId || requestTags.length === 0) return;
    const requestedTagId = searchParams.get('tag');
    const requestedLineId = searchParams.get('line');
    const byTag = requestedTagId
      ? requestTags.find((entry) => entry.tag.id === requestedTagId)
      : undefined;
    const byLine = requestedLineId
      ? requestTags.find((entry) => entry.line.id === requestedLineId)
      : undefined;
    setSelectedRequestTagId((byTag ?? byLine ?? requestTags[0]).tag.id);
    // The link did its job the moment it picked a tag - a refresh from here on
    // should land on whatever tag is actually open (Design/Arrange can move
    // it), not snap back to the one the URL named. `pathname` alone has no
    // query string, so this is a plain drop of the deep param.
    if (requestedTagId || requestedLineId) router.replace(pathname, { scroll: false });
  }, [selectedRequestTagId, requestTags, searchParams, router, pathname]);

  // A line with no tag yet is cloned from its family's default template. It
  // waits for BOTH the templates and the prices to settle (loaded OR error -
  // an error state has its own Retry, not a silent stall): the family comes
  // off the resolved code, so cloning early would pick the ala carte fallback
  // for everything.
  //
  // Zero PUBLISHED templates is not an error: the line starts from a
  // product-block (or, for a set line, a set block) starter bound to its own
  // item instead of dead-ending on "Preparing this line..." forever
  // (D6/D13, #476).
  //
  // This clone is NOT a user change (S3), so it only moves the autosave's
  // baseline - see the autosave effect below for why. `autoCloneRef` is how it
  // says so: set immediately before the state update whose `doc` the autosave
  // effect will then see.
  const autoCloneRef = useRef(false);
  useEffect(() => {
    if (templatesStatus === 'loading' || templatesStatus === 'error') return;
    if (pricesStatus === 'loading' || pricesStatus === 'error') return;
    // AC-S7-16: EVERY request tag with no design yet is cloned here, not
    // just the selected one - a split line's second tag (D6: an open group
    // resolved into its own tag per candidate at submit) must reach
    // `arrangeItems` the moment Arrange opens, even when nobody has ever
    // clicked into it on the canvas. One batched `setTags` update below, not
    // one `applyTemplate` call per tag - N sequential updates would fire the
    // autosave effect (which reads `tags`) once per tag instead of once.
    const missing = requestTags.filter((row) => !tags[row.tag.id]);
    if (missing.length === 0) return;
    const patch: Record<string, PlacedTag> = {};
    for (const { tag: requestTag, line } of missing) {
      const tagData = resolved.get(requestTag.id);
      const template =
        defaultTemplateFor(line, templates, tagData?.code) ??
        starterTemplateFor(line, tagData, newTagId);
      let tag = tagForTag(
        { id: requestTag.id, quantity: requestTag.quantity, line },
        template,
        newTagId(),
      );
      if (defaultTagSize) {
        tag = resizeTag(tag, defaultTagSize.width_mm, defaultTagSize.height_mm);
      }
      patch[requestTag.id] = tag;
    }
    autoCloneRef.current = true;
    setTags((prev) => ({ ...prev, ...patch }));
  }, [
    requestTags,
    tags,
    templates,
    templatesStatus,
    pricesStatus,
    resolved,
    defaultTagSize,
  ]);

  const selectedTag = selectedRequestTagId ? tags[selectedRequestTagId] ?? null : null;

  /**
   * The document the canvas opens on: ALWAYS the tag as `tags` holds it right
   * now, never a snapshot (R2, #726).
   *
   * The editor reads `doc.layers` once, on mount, into its own state, and
   * ignores the prop from then on - so what this hands over only matters at
   * the moment the canvas mounts, and at that moment the only right answer is
   * the tag's LIVE layers. This used to be a ref frozen at the tag's id, on
   * the assumption that "mounts" and "the tag id changes" were the same
   * event. They are not: the ternary below swaps the editor out for a
   * message whenever Arrange is showing, or the templates or the prices are
   * loading, or either errored, and swaps it back in afterwards - same tag,
   * same id, a real unmount and remount. The frozen ref then handed the
   * canvas the layers as they stood when the line was FIRST selected, the
   * canvas came back holding them, and its own `onLayersChange` wrote them
   * over the live ones, so the next autosave persisted a design the user had
   * already moved on from. Measured on the lane: drag a barcode, Update
   * template, and the refetch that follows the publish put the barcode back
   * where it started (the ref carried an explicit exception for the Arrange
   * switch, which is why only the loading paths still bit).
   *
   * Width/height were never part of that identity (S9 review B1) and still
   * are not: the editor reads `doc.width_mm`/`height_mm` straight off the
   * prop on every render, so a resize reaches the artboard WITHOUT a remount,
   * and the key on tag id alone means it never unmounts a focused input in
   * the Tag Size control.
   */
  const selectedDoc: TagTemplateDoc | null = selectedTag
    ? {
        layers: selectedTag.layers,
        width_mm: selectedTag.width_mm,
        height_mm: selectedTag.height_mm,
      }
    : null;

  /** What the canvas draws against: the LINE, with its marketing override. */
  const boundData: TagBindingData | null = useMemo(() => {
    if (!selectedRequestTagId) return null;
    const row = resolved.get(selectedRequestTagId);
    return row ? { kind: 'line', line: row } : null;
  }, [selectedRequestTagId, resolved]);

  // -- Tag size control (D24, S9; lifted to a shared component, S1) -----------

  const sizePresets = useMemo(() => tagSizePresets(templates), [templates]);
  const savedSizesQuery = useTagSizesQuery();
  const deleteSavedSize = useDeleteTagSizePreset();
  const [saveSizeOpen, setSaveSizeOpen] = useState(false);
  const tagSizeBoundsForRequest = useMemo(() => tagSizeBounds(), []);

  /**
   * The per-A4 grid CONFIGURED for a size group at arrange time (S7,
   * AC-S7-15): this session's own typed-but-unsaved value first, else the
   * group's own template's `print_size.sheet` when it names this exact size,
   * else a saved size preset with the same size, else null (arrange
   * derives).
   */
  // Read through refs, not the state values directly (S7): `templates` and
  // `savedSizesQuery.data` load ASYNCHRONOUSLY after mount, and a `doc`
  // memoized off `gridForSize`'s own identity would otherwise get a fresh
  // reference the moment either arrives - which the autosave-scheduling
  // effect below reads as a real edit and persists nothing-changed. Only
  // `customSheetGrids` (an actual typed edit) stays a real dependency.
  const templatesRef = useRef(templates);
  useEffect(() => {
    templatesRef.current = templates;
  }, [templates]);
  const savedSizesRef = useRef(savedSizesQuery.data);
  useEffect(() => {
    savedSizesRef.current = savedSizesQuery.data;
  }, [savedSizesQuery.data]);

  const gridForSize = useCallback(
    (width_mm: number, height_mm: number, templateId: string): SheetGridConfig | null => {
      const key = `${width_mm}x${height_mm}`;
      if (customSheetGrids[key]) return customSheetGrids[key];
      const template = templatesRef.current.find((t) => t.id === templateId);
      if (
        template?.print_size.sheet &&
        template.print_size.width_mm === width_mm &&
        template.print_size.height_mm === height_mm
      ) {
        return template.print_size.sheet;
      }
      const preset = (savedSizesRef.current ?? []).find(
        (s) => s.width_mm === width_mm && s.height_mm === height_mm && s.sheet_cols && s.sheet_rows,
      );
      if (preset?.sheet_cols && preset?.sheet_rows) {
        return { cols: preset.sheet_cols, rows: preset.sheet_rows, turn: preset.sheet_turn ?? false };
      }
      return null;
    },
    [customSheetGrids],
  );

  /**
   * A grid typed into the panel (S7, AC-S7-14): held for this session only
   * (`customSheetGrids`), so arrange reflects it immediately without waiting
   * on a save. Persisting it onto an EXISTING saved preset is a later slice -
   * there is no update call for it here yet - a size with no preset carries
   * it forward when "Save as size" is used (the dialog reads
   * `customSheetGrids` directly, below).
   */
  const handleSheetGridChange = useCallback(
    (width_mm: number, height_mm: number, grid: SheetGridConfig | null) => {
      const key = `${width_mm}x${height_mm}`;
      setCustomSheetGrids((prev) => {
        if (!grid) {
          return Object.fromEntries(Object.entries(prev).filter(([k]) => k !== key));
        }
        return { ...prev, [key]: grid };
      });
    },
    [],
  );

  const handleResizeTag = useCallback(
    (width_mm: number, height_mm: number) => {
      const tagId = selectedRequestTagId;
      if (!tagId) return;
      bulkUndoRef.current = null;
      setTags((prev) => {
        const tag = prev[tagId];
        if (!tag) return prev;
        return { ...prev, [tagId]: resizeTag(tag, width_mm, height_mm) };
      });
    },
    [selectedRequestTagId],
  );

  // Resizes every ALREADY-CLONED tag now, and remembers the size as the
  // request's default (S9 review B2) so a line opened later clones at this
  // size too, via `applyTemplate` above - not the template's own print size.
  const handleResizeAllTags = useCallback((width_mm: number, height_mm: number) => {
    bulkUndoRef.current = null;
    setTags((prev) => resizeAllTags(prev, width_mm, height_mm));
    setDefaultTagSize({ width_mm, height_mm });
  }, []);

  const handleLayersChange = useCallback(
    (layers: TagLayer[]) => {
      const tagId = selectedRequestTagId;
      if (!tagId) return;
      setTags((prev) => {
        const tag = prev[tagId];
        if (!tag || tag.layers === layers) return prev;
        bulkUndoRef.current = null;
        return { ...prev, [tagId]: { ...tag, layers } };
      });
    },
    [selectedRequestTagId],
  );

  /**
   * Undoes the one pending bulk apply (S5, AC-S5-3): the toast's own "Undo"
   * action and the Cmd/Ctrl+Z handler below are the only two callers. A
   * second press once the slot is empty is a no-op, same as pressing Undo
   * on a toast that already dismissed itself.
   */
  const undoBulkApply = useCallback(() => {
    const snapshot = bulkUndoRef.current;
    if (!snapshot) return;
    bulkUndoRef.current = null;
    setTags(snapshot);
  }, []);

  /**
   * A single line's tag is replaced with `template`, immediately - the
   * "Replace this tag with the template?" confirm is gone (D11, AC-S5-7);
   * Undo is the safety net instead, same toast + Cmd/Ctrl+Z as the two bulk
   * paths below.
   */
  const chooseTemplate = useCallback(
    (tagId: string, templateId: string) => {
      const ref = tagRefs.find((r) => r.id === tagId);
      const template = templates.find((t) => t.id === templateId);
      if (!ref || !template) return;
      bulkUndoRef.current = tags;
      applyTemplate(ref, template);
      toast.success('Template applied', {
        action: { label: 'Undo', onClick: undoBulkApply },
      });
      setPickerLineId(null);
      setSelectedRequestTagId(tagId);
    },
    [tagRefs, templates, tags, applyTemplate, undoBulkApply],
  );

  /**
   * The picker's "Apply to all lines" checkbox (AC-S5-4): every line gets a
   * PRISTINE clone of the chosen template via `tagForLine` - the same clone a
   * line gets when it is opened for the first time, not the design that is
   * currently on `focusLineId`'s canvas. That is the whole difference from
   * `handleApplyDesignToAll` below: this spreads a TEMPLATE, that spreads a
   * DESIGN.
   */
  const chooseTemplateForAllLines = useCallback(
    (templateId: string, focusTagId: string) => {
      const template = templates.find((t) => t.id === templateId);
      if (!template) return;
      bulkUndoRef.current = tags;
      const next: Record<string, PlacedTag> = { ...tags };
      for (const ref of tagRefs) {
        let tag = tagForTag(ref, template, newTagId());
        if (defaultTagSize) {
          tag = resizeTag(tag, defaultTagSize.width_mm, defaultTagSize.height_mm);
        }
        next[ref.id] = tag;
      }
      setTags(next);
      toast.success(`Applied to ${tagRefs.length} tag${tagRefs.length === 1 ? '' : 's'}`, {
        action: { label: 'Undo', onClick: undoBulkApply },
      });
      setPickerLineId(null);
      setSelectedRequestTagId(focusTagId);
    },
    [templates, tags, tagRefs, defaultTagSize, undoBulkApply],
  );

  const handleTemplateChosen = useCallback(
    (templateId: string, applyToAll: boolean) => {
      const tagId = pickerLineId;
      if (!tagId) return;
      if (applyToAll) {
        chooseTemplateForAllLines(templateId, tagId);
      } else {
        chooseTemplate(tagId, templateId);
      }
    },
    [pickerLineId, chooseTemplate, chooseTemplateForAllLines],
  );

  /**
   * The Lines rail's "Apply this design to all lines" (AC-S5-1/2/3): the
   * SELECTED line's tag, edits included, cloned onto every other line -
   * `applyDesignToAllLines` does the rebinding and the fresh ids, this is
   * only the undo snapshot + toast plumbing shared with the two paths above.
   */
  const handleApplyDesignToAll = useCallback(() => {
    if (!selectedRequestTagId || !tags[selectedRequestTagId]) return;
    const count = tagRefs.length - 1;
    if (count <= 0) return;
    bulkUndoRef.current = tags;
    const applied = applyDesignToAllTags(tags, tagRefs, selectedRequestTagId, newTagId);
    const targetIds = tagRefs
      .map((ref) => ref.id)
      .filter((id) => id !== selectedRequestTagId);
    const { tags: fixed, affected } = fallBackOutOfRangeSubjects(applied, targetIds, resolved);
    setTags(fixed);
    toast.success(`Applied to ${count} tag${count === 1 ? '' : 's'}`, {
      action: { label: 'Undo', onClick: undoBulkApply },
    });
    // AC-S4-6: a layer pointed at a part a thinner target line does not have
    // fell back to the parent - named so the designer knows to check it,
    // not silently.
    if (affected.length > 0) {
      toast.warning(
        `Fell back to the parent product (fewer parts on the target line): ${affected.join(', ')}`,
      );
    }
  }, [selectedRequestTagId, tags, tagRefs, undoBulkApply, resolved]);

  // Cmd/Ctrl+Z restores the one pending bulk apply (AC-S5-3) - only while
  // there is one: with nothing armed, the key falls through untouched to
  // whatever else on the page wants it (the canvas's own layer-history
  // undo). Captured on `window`'s CAPTURE phase, ahead of that handler,
  // so this can `stopPropagation()` and be the only one of the two that
  // fires - the alternative, both firing, would undo a canvas edit AND
  // restore every other line's tag off one keystroke.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const isInput =
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement;
      if (isInput) return;
      if (!bulkUndoRef.current) return;
      const modifier = e.ctrlKey || e.metaKey;
      if (!modifier || e.key.toLowerCase() !== 'z' || e.shiftKey) return;
      e.preventDefault();
      e.stopPropagation();
      undoBulkApply();
    };
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [undoBulkApply]);

  // -- The document ----------------------------------------------------------

  const arrangeItems: ArrangeItem[] = useMemo(
    () =>
      tagRefs.flatMap((ref) => {
        const tag = tags[ref.id];
        return tag ? [{ tag, quantity: ref.quantity, print_excluded: ref.print_excluded }] : [];
      }),
    [tagRefs, tags],
  );

  // Arrange, size-grouped (S7): each size's own sheets, packed at zero gap
  // inside the fixed 5mm margin (or a configured grid), before the next size
  // starts a fresh sheet. `placement` is display-only metadata alongside
  // `doc.sheets` (AC-S7-6) - never stored in the doc.
  const arranged = useMemo(
    () => autoArrange(arrangeItems, gridForSize),
    [arrangeItems, gridForSize],
  );

  const doc: TagSheetDoc = useMemo(
    () => ({
      kind: 'tag_sheet',
      imposition: DEFAULT_IMPOSITION,
      sheets: arranged.sheets,
      default_tag_size: defaultTagSize,
    }),
    [arranged, defaultTagSize],
  );

  /** Template name by id, for the Arrange view's per-sheet line (AC-S7-6) -
   *  `templates` is already loaded for the size presets above. */
  const templateNameById = useMemo(() => {
    const map: Record<string, string> = { [STARTER_TEMPLATE_ID]: 'Starter' };
    for (const t of templates) map[t.id] = t.name;
    return map;
  }, [templates]);

  // -- Autosave (D22, S8) ------------------------------------------------------

  // Set the moment the page is going away, and read by the save below so ONLY
  // the teardown request is sent `keepalive` (a keepalive body is capped at
  // 64KB, which a busy tag sheet exceeds - see the service).
  const teardownRef = useRef(false);
  const { status, savedAt, schedule, flush, retry } = useAutosave<TagSheetDoc>(
    useCallback(
      (next: TagSheetDoc) => onAutosave(next, { keepalive: teardownRef.current }),
      [onAutosave],
    ),
  );

  // Every REAL change to `doc` schedules a debounced save - a layer edit
  // (through `tags`), a resize, a quantity change (through `arrangeItems`).
  // Two changes are NOT edits and must persist nothing:
  //
  //  * the very first `doc` (whatever `initialDoc` seeded, or the empty
  //    starting point) - already exactly what the server has;
  //  * the starter/template CLONE the effect above performs for a line that
  //    has no tag yet (S3). That is the page deciding what to draw, not the
  //    user deciding anything, so merely OPENING a request with undesigned
  //    lines - or clicking down the rail to look at them - used to write a
  //    design for every one of them. The clone announces itself through
  //    `autoCloneRef` and only moves the baseline. Choosing a template from
  //    the picker goes through `chooseTemplate`, never through here, so a real
  //    choice still saves.
  //
  // Guards on the VALUE, not a boolean "have I run yet" flag: dev's
  // StrictMode fires a fresh mount's effects twice (mount, cleanup, mount
  // again) to catch effects that are not idempotent, and a boolean flag
  // would already read "yes" on that second firing, autosaving the
  // unchanged initial doc a second time. Comparing against the last `doc`
  // this effect actually saw covers both: an unmoved re-fire is the same
  // reference and is skipped either way.
  const lastSeenDocRef = useRef<TagSheetDoc | undefined>(undefined);
  useEffect(() => {
    if (lastSeenDocRef.current === doc) return;
    const isInitial = lastSeenDocRef.current === undefined;
    const isAutoClone = autoCloneRef.current;
    autoCloneRef.current = false;
    lastSeenDocRef.current = doc;
    if (isInitial || isAutoClone) return;
    schedule(doc);
  }, [doc, schedule]);

  // Leaving the page, both ways it can happen (S1/S2).
  //
  // A route change inside the app - the back link, a sidebar click, the
  // browser's own Back - unmounts this component, so the cleanup below is
  // where that edit gets its last chance. `pagehide` covers what no React
  // lifecycle sees: a refresh, a closed tab, a jump to another site. It
  // replaces `beforeunload`, which fires too early to be the whole story and
  // is throttled or skipped outright on mobile Safari, where a backgrounded
  // tab is discarded without one.
  //
  // Both set `teardownRef` first so the request goes out `keepalive` and
  // survives the document it was made from.
  useEffect(() => {
    // A mounted page is not tearing down. StrictMode's mount/cleanup/mount
    // pass would otherwise leave the flag set for the whole session, and
    // every ordinary autosave after it would go out keepalive - which fails
    // outright once the document passes 64KB.
    teardownRef.current = false;
    const handler = () => {
      teardownRef.current = true;
      void flush();
    };
    window.addEventListener('pagehide', handler);
    return () => {
      window.removeEventListener('pagehide', handler);
      handler();
    };
  }, [flush]);

  /**
   * The deliberate save: one version, and never racing the autosave (S4).
   *
   * `flush()` first - it cancels the armed debounce and waits for anything on
   * the wire, so a draft write cannot land AFTER this version snapshot and
   * resurrect a draft the snapshot just cleared. With nothing pending it is a
   * no-op, which is the ordinary case: this stays one request.
   *
   * The toast lives in the host's `onSave` (B3), and so does the error one -
   * which is also why the rejection is swallowed here rather than reported
   * twice.
   */
  const saveNow = useCallback(async () => {
    await flush();
    await onSave(doc);
  }, [flush, doc, onSave]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await saveNow();
    } catch {
      // Already reported by `onSave`.
    } finally {
      setSaving(false);
    }
  }, [saveNow]);

  const handleMarkProofReady = useCallback(async () => {
    setTransitioning(true);
    try {
      // A failed save now ABORTS rather than marking a proof ready off a
      // design the server never received (B3: `onSave` rethrows).
      await saveNow();
      // A STATUS, not an action name: see the note on the detail page.
      await transitionPriceTagRequest(request.id, 'proof_ready');
      toast.success('Design marked as ready');
      router.push(`/dealer-kit/price-tag-requests/${request.id}`);
    } catch (e) {
      // `transitionPriceTagRequest` already extracts the server's own
      // message via `extractApiError` and throws it - a fixed string here
      // swallowed the actual 409 reason (e.g. an invalid transition) and
      // left the salesperson/marketing with no idea why the click failed.
      toast.error(e instanceof Error ? e.message : 'Failed to mark the design ready');
    } finally {
      setTransitioning(false);
    }
  }, [saveNow, request.id, router]);

  const handlePrintSheet = useCallback(
    async (sheetIndex: number) => {
      const sheet = doc.sheets[sheetIndex];
      if (!sheet) return;
      setPrinting(true);
      try {
        // Saved first: the worker prints the latest VERSION, and an unsaved
        // arrangement would print the previous one.
        await saveNow();
        await exportTagSheet(request.id, [sheet.id]);
        toast.success(`Sheet ${sheetIndex + 1} export queued. Check My Downloads.`);
      } catch {
        toast.error('Failed to export the sheet');
      } finally {
        setPrinting(false);
      }
    },
    [doc.sheets, saveNow, request.id],
  );

  const canMarkProofReady =
    request.status === 'designing' || request.status === 'changes_requested';

  /**
   * D15 (AC-S10-3/S10-4): once approved, this bar is where print and
   * hand-over happen - the "go to the design" CTA from the detail page
   * (D14) lands here. Rendered in the exact slot `Mark design ready`
   * occupies, so the bar still holds one primary.
   */
  const isApproved = request.status === 'approved';

  /** Switching a line never LOSES a committed change (AC-S8-3) - flush the
   *  autosave's own pending value before the switch, rather than leaving it
   *  to the ~1s debounce that might not have fired yet. */
  const handleSelectTag = useCallback(
    (tagId: string) => {
      if (tagId !== selectedRequestTagId) void flush();
      setSelectedRequestTagId(tagId);
    },
    [selectedRequestTagId, flush],
  );

  /** r10 S10: the rail sits beside BOTH modes now (it used to live only
   *  inside the design editor), so a click on a row drives whichever
   *  selection that mode already reads - design's own `selectedRequestTagId`
   *  (with the same pre-switch flush), or arrange's `selectedTagId`, which
   *  `ArrangeSheetView` already highlights on the sheet. */
  const handleRailSelect = useCallback(
    (tagId: string) => {
      if (mode === 'arrange') {
        setSelectedTagId(tagId);
      } else {
        handleSelectTag(tagId);
      }
    },
    [mode, handleSelectTag],
  );

  /**
   * The request's tag set changed under us (a split), so re-read it and the
   * per-tag resolver rows with it. The placements in `tags` are untouched: the
   * split tag keeps its id, and its new siblings clone from their template the
   * first time each is opened, exactly as any other untouched tag does.
   */
  /**
   * The request's tag set changed under us, so re-read it AND the per-tag
   * resolver rows.
   *
   * Both, not just the request (tester defect D1): the rail's price, label and
   * open-group pill all come from `resolvedRows`, so a split left 1a showing
   * the figure it had while the basin was still open and 1b showing none at
   * all until the page was reloaded. The two reads go together because they are
   * one answer - what the tags are, and what each of them prints.
   */
  const reloadRequest = useCallback(async () => {
    const [fresh, rows] = await Promise.all([
      getPriceTagRequest(request.id),
      resolveRequestTags(request.id).catch(() => null),
    ]);
    if (fresh) setRequest(fresh);
    if (rows) setResolvedRows(rows);
  }, [request.id]);

  /**
   * r10 S8: in an auto-update status the FIRST poll that reports a change has
   * already applied it server-side (the pin moved, the before-version was
   * written), so the request and the resolved rows are stale the moment the
   * report lands. Re-read both once per report: the tag then carries
   * `data_updated_at` (the dot switches to the Dismiss / Roll back dialog)
   * and the canvas redraws against the new pin. The next poll reports nothing
   * - the diff is empty once the pin matches - so this cannot loop.
   */
  const reportedTagIds = useMemo(
    () => Array.from(changesByTag.keys()).sort().join(','),
    [changesByTag],
  );
  useEffect(() => {
    if (!reportedTagIds || !AUTO_UPDATE_STATUSES.includes(request.status)) return;
    void reloadRequest();
  }, [reportedTagIds, request.status, reloadRequest]);

  /** Restore one version - the History sheet's Restore and S8's Roll back
   *  are the same call. Re-reads the request AND the rows: the pins moved. */
  const restoreVersion = useCallback(
    async (version: number) => {
      await restoreRequestVersion(request.id, version);
      await reloadRequest();
      toast.success(`Restored v${version}`);
    },
    [request.id, reloadRequest],
  );

  const dismissUpdate = useDismissTagDataUpdate(request.id);
  /** r10 S8 Dismiss: clears the indicator server-side, then re-reads the tag
   *  so the dot goes without a page reload. Refused (the route is not there
   *  yet, or the request moved on): the message is shown and the dot stays. */
  const handleDismissUpdate = useCallback(
    async (tagId: string) => {
      try {
        await dismissUpdate.mutateAsync(tagId);
        await reloadRequest();
        toast.success('Update dismissed');
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Could not dismiss the update');
        throw error;
      }
    },
    [dismissUpdate, reloadRequest],
  );

  const updateTag = useUpdateRequestTag(request.id);
  /**
   * r10 S6 Not printed: flips `print_excluded` on the tag and writes the
   * answer into the request held here from the response the PATCH sends
   * back - falling back to the value just sent only if a response somehow
   * carries none. Arrange re-runs off `tagRefs`, so the copies drop out at
   * once.
   */
  const handleTogglePrintExcluded = useCallback(
    async (tag: PriceTagRequestTag) => {
      const next = !tag.print_excluded;
      try {
        const saved = await updateTag.mutateAsync({
          tagId: tag.id,
          data: { print_excluded: next },
        });
        const value = typeof saved?.print_excluded === 'boolean' ? saved.print_excluded : next;
        setRequest((prev) => ({
          ...prev,
          lines: prev.lines.map((line) => ({
            ...line,
            tags: (line.tags ?? []).map((row) =>
              row.id === tag.id ? { ...row, print_excluded: value } : row,
            ),
          })),
        }));
        toast.success(value ? `Tag ${tag.label} will not print` : `Tag ${tag.label} prints again`);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Could not update the tag');
      }
    },
    [updateTag],
  );

  /** D15: the whole-request export, no sheet filter - the same call and
   *  toast the detail page's `handleExport` makes. */
  const handleExportApproved = useCallback(async () => {
    setPrinting(true);
    try {
      const result = await exportTagSheet(request.id);
      toast.success(
        result?.filename
          ? `PDF export queued. Check My Downloads for "${result.filename}".`
          : 'PDF export queued. Check My Downloads.',
      );
    } catch {
      toast.error('Failed to export the sheet');
    } finally {
      setPrinting(false);
    }
  }, [request.id]);

  const handleMarkReadyForCollection = useCallback(async () => {
    setTransitioning(true);
    try {
      await markReadyForCollection(request.id);
      toast.success('Marked ready for collection');
      await reloadRequest();
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : 'Failed to mark ready for collection',
      );
    } finally {
      setTransitioning(false);
    }
  }, [request.id, reloadRequest]);

  /**
   * Removing a tag asks nothing (D7, AC-S3-6): the row parks the removal on the
   * server for its grace window and a toast carries the countdown, the same way
   * every other destructive action on this codebase does. The line's LAST tag is
   * refused twice over - the button is disabled and says why, and the service
   * answers `LAST_TAG` at commit time - so a stale screen cannot get past it.
   */
  const tagDeletion = useDeferredRowAction({
    actionKey: 'price_tag_request_tag.delete',
    entityType: 'price_tag_request_tag',
    verb: 'Removing',
    successMessage: 'Tag removed',
    onCommitted: () => {
      void reloadRequest();
    },
  });

  const handleRemoveTag = useCallback(
    (tag: PriceTagRequestTag) => {
      // The placement goes with the tag, so whatever the canvas is holding has
      // to be on the server before it rewrites the draft document underneath.
      void flush();
      tagDeletion.run({ id: tag.id, subject: `Tag ${tag.label}` });
    },
    [flush, tagDeletion],
  );

  /** Same idea for Design <-> Arrange (AC-S8-3). */
  const handleModeChange = useCallback(
    (value: 'design' | 'arrange') => {
      if (value !== mode) void flush();
      setMode(value);
    },
    [mode, flush],
  );

  // The unmount cleanup flushes too, so this is only about being EARLY: the
  // save leaves before the route change rather than after it.
  const handleBack = useCallback(() => {
    void flush();
    router.push(`/dealer-kit/price-tag-requests/${request.id}`);
  }, [flush, router, request.id]);

  // -- Save as template (S4, D1) -----------------------------------------------

  const selectedEntry = selectedRequestTagId
    ? requestTags.find((entry) => entry.tag.id === selectedRequestTagId) ?? null
    : null;
  const selectedLine = selectedEntry?.line ?? null;
  const selectedLineCode = selectedRequestTagId
    ? resolved.get(selectedRequestTagId)?.code ?? ''
    : '';
  const saveTemplateDefaultName = selectedLineCode ? `${selectedLineCode} tag` : 'New tag';
  const saveTemplateDefaultFamily = (
    selectedLine ? lineFamily(selectedLine, selectedLineCode) : 'ala_carte'
  ) as TagTemplateFamily;

  const handleTemplateCreated = useCallback(
    (created: TagTemplate) => {
      // A real refetch, not a local splice: the picker's source is the
      // backend's own published list (AC-S4-8), and this is what keeps it
      // agreeing with what a reload would show.
      loadTemplates();
      toast.success(`Template "${created.name}" published`, {
        action: {
          label: 'Open',
          onClick: () => router.push(`/dealer-kit/tag-templates/${created.id}`),
        },
      });
    },
    [router, loadTemplates],
  );

  // -- Update template (S6, D6) -------------------------------------------------

  // Only PUBLISHED templates are eligible - `templates` is already
  // `listPublishedTemplates()`'s own result, so a template someone deleted
  // or never published resolves to null and the Template menu offers only
  // "Save as new template" (AC-S6-1).
  const updateEligibleTemplate = selectedTag
    ? templates.find((t) => t.id === selectedTag.template_id) ?? null
    : null;
  const updateSiblingCount =
    selectedTag && updateEligibleTemplate
      ? tagRefs.filter(
          (ref) =>
            ref.id !== selectedRequestTagId &&
            tags[ref.id]?.template_id === updateEligibleTemplate.id,
        ).length
      : 0;
  const updateNextVersionNo = (updateEligibleTemplate?.published_version_no ?? 0) + 1;

  const handleUpdateTemplate = useCallback(
    async (applyToSiblings: boolean) => {
      if (!selectedTag || !selectedRequestTagId || !updateEligibleTemplate) return;
      setUpdatingTemplate(true);
      try {
        // Existing PUT (S1's updateTemplate carries print_size too now) then
        // the existing publish route - no new backend for this slice. Bound
        // layers lose their `text_override` here too - same rule
        // `templateFromTag` applies for "Save as new template": a value
        // typed for THIS line (a price, a name) is not the shared template's
        // to keep, only the slot binding is.
        await updateTagTemplate(updateEligibleTemplate.id, {
          layers: selectedTag.layers.map((layer) => ({
            ...layer,
            text_override: layer.slot_binding ? null : layer.text_override,
          })),
          width_mm: selectedTag.width_mm,
          height_mm: selectedTag.height_mm,
        });
        const published = await publishTemplate(
          updateEligibleTemplate.id,
          `Updated from ${request.doc_number}`,
        );
        if (applyToSiblings && updateSiblingCount > 0) {
          bulkUndoRef.current = tags;
          setTags(
            applyDesignToSiblings(
              tags,
              tagRefs,
              selectedRequestTagId,
              updateEligibleTemplate.id,
              newTagId,
            ),
          );
          toast.success(
            `Updated "${updateEligibleTemplate.name}" (v${published.published_version_no}) and applied to ${updateSiblingCount} other tag${updateSiblingCount === 1 ? '' : 's'}`,
            { action: { label: 'Undo', onClick: undoBulkApply } },
          );
        } else {
          toast.success(
            `Updated "${updateEligibleTemplate.name}" (v${published.published_version_no})`,
          );
        }
        // Refetches the published list (AC-S6-3): the Template menu, the
        // size presets and the "Use template..." picker all read from it.
        loadTemplates();
        setUpdateTemplateOpen(false);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Could not update the template');
      } finally {
        setUpdatingTemplate(false);
      }
    },
    [
      selectedTag,
      selectedRequestTagId,
      updateEligibleTemplate,
      updateSiblingCount,
      tags,
      tagRefs,
      request.doc_number,
      loadTemplates,
      undoBulkApply,
    ],
  );

  // -- Change requests (r9 S2/D6) ---------------------------------------------

  /** Open pins per TAG: the LINES rail badge, and the CTA's own count. */
  const openPinsByTag = useMemo(
    () => openCountByTag(reviewComments),
    [reviewComments],
  );
  const openPinCount = useMemo(
    () => [...openPinsByTag.values()].reduce((sum, count) => sum + count, 0),
    [openPinsByTag],
  );
  /** What the canvas draws: this tag's pins, or nothing while toggled off. */
  const canvasPins = useMemo(
    () =>
      commentsVisible
        ? canvasPinsForTag(reviewComments, selectedRequestTagId)
        : [],
    [commentsVisible, reviewComments, selectedRequestTagId],
  );

  // -- Render ----------------------------------------------------------------

  // The canvas toolbar's own right-end group (S7): Full screen, the
  // Template dropdown (S6) and Save. Only reaches the screen in design
  // mode - it is handed to TagCanvasEditor, which only mounts there;
  // arrange mode keeps its own Full screen + Save in the request bar
  // below, since ArrangeSheetView has no canvas toolbar of its own to
  // move them into (AC-S7-5 holds for free the same way).
  const toolbarTrailing: ToolbarTrailingAction[] = [
    // Only when there is something to show: an empty toggle is a control that
    // does nothing on most requests.
    ...(reviewComments.length > 0
      ? [
          {
            id: 'comments',
            icon: MessageSquare,
            label: commentsVisible
              ? `Hide change requests (${openPinCount} open)`
              : `Show change requests (${openPinCount} open)`,
            onClick: () => setCommentsVisible((visible) => !visible),
            active: commentsVisible,
          } satisfies ToolbarTrailingAction,
        ]
      : []),
    {
      id: 'check-product-data',
      icon: RefreshCw,
      label: 'Check product data',
      onClick: () => void recheckDataChanges(),
    },
    {
      id: 'history',
      icon: History,
      label: 'History',
      onClick: () => setHistoryOpen(true),
    },
    {
      id: 'full-screen',
      icon: focus ? Minimize2 : Maximize2,
      label: focus ? 'Exit full screen' : 'Full screen',
      onClick: () => setFocus(!focus),
      active: focus,
    },
    {
      id: 'template',
      kind: 'menu',
      icon: LayoutTemplate,
      label: 'Template',
      disabled: !selectedTag,
      items: (
        <>
          {updateEligibleTemplate && (
            <DropdownMenuItem onSelect={() => setUpdateTemplateOpen(true)}>
              Update &quot;{updateEligibleTemplate.name}&quot;
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={() => setSaveTemplateOpen(true)}>
            Save as new template
          </DropdownMenuItem>
        </>
      ),
    },
    {
      id: 'save',
      icon: saving ? Loader2 : Save,
      iconClassName: saving ? 'animate-spin' : undefined,
      label: saving ? 'Saving...' : 'Save',
      onClick: save,
      disabled: saving || transitioning,
    },
  ];

  const rail = (
    <>
      <LinesRail
        lines={request.lines}
        resolved={resolved}
        pricesStatus={pricesStatus}
        tags={tags}
        openPinsByTag={openPinsByTag}
        changedTagIds={new Set([...changesByTag.keys(), ...updatedTags.keys()])}
        onReviewTag={setReviewTagId}
        selectedRequestTagId={mode === 'arrange' ? selectedTagId : selectedRequestTagId}
        onSelect={handleRailSelect}
        onUseTemplate={setPickerLineId}
        canApplyToAll={Boolean(selectedTag) && tagRefs.length > 1}
        onApplyToAll={handleApplyDesignToAll}
        onRemoveTag={handleRemoveTag}
        removingTagId={tagDeletion.isPending ? tagDeletion.targetId : null}
        onTogglePrintExcluded={handleTogglePrintExcluded}
        togglingTagId={updateTag.isPending ? (updateTag.variables?.tagId ?? null) : null}
      />
      {selectedTag ? (
        <TagSizeControl
          width_mm={selectedTag.width_mm}
          height_mm={selectedTag.height_mm}
          presets={sizePresets}
          savedSizes={savedSizesQuery.data}
          bounds={tagSizeBoundsForRequest}
          onResize={handleResizeTag}
          onResizeAll={handleResizeAllTags}
          onDeleteSavedSize={(id, name) => deleteSavedSize.run({ id, subject: name })}
          deletingSavedSizeId={deleteSavedSize.isPending ? deleteSavedSize.targetId : null}
          onSaveAsSize={() => setSaveSizeOpen(true)}
          sheetGrid={gridForSize(selectedTag.width_mm, selectedTag.height_mm, selectedTag.template_id)}
          onSheetGridChange={(grid) =>
            handleSheetGridChange(selectedTag.width_mm, selectedTag.height_mm, grid)
          }
        />
      ) : (
        <div className="shrink-0 border-b border-r p-3">
          <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Tag Size
          </span>
          <p className="mt-1 text-2xs text-muted-foreground">
            Select a line to set its tag size.
          </p>
        </div>
      )}
      {selectedTag && (
        <SaveAsSizeDialog
          open={saveSizeOpen}
          onOpenChange={setSaveSizeOpen}
          width_mm={selectedTag.width_mm}
          height_mm={selectedTag.height_mm}
          sheetGrid={customSheetGrids[`${selectedTag.width_mm}x${selectedTag.height_mm}`] ?? null}
        />
      )}
    </>
  );

  return (
    <FocusShell active={focus} onExit={() => setFocus(false)}>
    <div className="flex h-full min-h-0 flex-1 flex-col">
      {/* Request bar: what this is, which half is showing, the Saved
          indicator and - in design mode - the ONE action, Mark design ready
          (S7, AC-S7-1). Full screen, the Template dropdown and Save moved
          into the canvas toolbar's own trailing group below; arrange mode
          keeps its own Full screen + Save here, since ArrangeSheetView has
          no canvas toolbar of its own to move them into.
          `flex-wrap`: at 375px the back button, mode toggle and (arrange
          mode, or a designing request) the remaining actions do not fit one
          row - `min-h-10` rather than a fixed `h-10` so the row can
          actually grow into a second line instead of clipping it. */}
      <div className="flex min-h-10 shrink-0 flex-wrap items-center gap-2 border-b bg-background px-3 py-1.5">
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={handleBack}
        >
          <ChevronLeft className="mr-1 size-3.5" />
          {request.doc_number}
        </Button>

        <div className="ml-2 flex items-center rounded-md border p-0.5">
          {(['design', 'arrange'] as const).map((value) => (
            <button
              key={value}
              type="button"
              className={cn(
                'rounded px-2.5 py-1 text-xs capitalize transition-colors',
                mode === value
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-muted',
              )}
              onClick={() => handleModeChange(value)}
            >
              {value === 'design' ? 'Design' : 'Arrange'}
            </button>
          ))}
        </div>

        <div className="flex-1" />

        <AutosaveIndicator status={status} savedAt={savedAt} onRetry={retry} />

        {mode === 'arrange' && (
          <>
            <FocusToggle
              active={focus}
              onToggle={setFocus}
              label="tags"
              className="h-7 text-xs"
              iconClassName="size-3.5"
            />
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={save}
              disabled={saving || transitioning}
            >
              {saving ? (
                <Loader2 className="mr-1 size-3.5 animate-spin" />
              ) : (
                <Save className="mr-1 size-3.5" />
              )}
              Save
            </Button>
          </>
        )}

        {canMarkProofReady && (
          <Button
            variant="primary"
            size="sm"
            className="h-7 text-xs"
            onClick={handleMarkProofReady}
            disabled={saving || transitioning}
          >
            {transitioning ? (
              <Loader2 className="mr-1 size-3.5 animate-spin" />
            ) : (
              <Eye className="mr-1 size-3.5" />
            )}
            {openPinCount > 0
              ? `Mark design ready (${openPinCount} open)`
              : 'Mark design ready'}
          </Button>
        )}

        {/* D15 (AC-S10-3/S10-4): approved is where print and hand-over
            happen now, in the exact slot Mark design ready occupied. Export
            for either print choice; Mark ready for collection is the
            primary, and only for an office print - self printing ends here. */}
        {isApproved && (
          <>
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={handleExportApproved}
              disabled={printing}
            >
              {printing ? (
                <Loader2 className="mr-1 size-3.5 animate-spin" />
              ) : (
                <Download className="mr-1 size-3.5" />
              )}
              Export PDF
            </Button>
            {request.print_by === 'office' && (
              <Button
                variant="primary"
                size="sm"
                className="h-7 text-xs"
                onClick={handleMarkReadyForCollection}
                disabled={transitioning}
              >
                {transitioning ? (
                  <Loader2 className="mr-1 size-3.5 animate-spin" />
                ) : (
                  <Package className="mr-1 size-3.5" />
                )}
                Mark ready for collection
              </Button>
            )}
          </>
        )}
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* r10 S10 (cause: `<TagCanvasEditor key={selectedTag.id} leftRail=
            {rail}>` put the rail INSIDE the keyed subtree, so selecting a
            tag remounted the rail's own scroll container along with the
            canvas and a row picked at the bottom of a long list jumped out
            of view). The rail now lives here, a sibling of both modes'
            content, so it is never part of what remounts - and Arrange,
            which had no rail of its own before, gets one for free. */}
        <div className="hidden h-full w-64 shrink-0 flex-col overflow-hidden md:flex">
          {rail}
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          {mode === 'design' ? (
            request.lines.length === 0 ? (
              <CanvasMessage text="This request has no lines, so there is nothing to design." />
            ) : templatesStatus === 'loading' ? (
              <CanvasMessage text="Loading templates..." />
            ) : templatesStatus === 'error' ? (
              <CanvasMessage text="Failed to load tag templates.">
                <Button variant="outline" size="sm" onClick={loadTemplates}>
                  <RefreshCw className="mr-1.5 size-3.5" />
                  Retry
                </Button>
              </CanvasMessage>
            ) : pricesStatus === 'loading' ? (
              <CanvasMessage text="Resolving prices..." />
            ) : pricesStatus === 'error' ? (
              <CanvasMessage text="Failed to resolve prices.">
                <Button variant="outline" size="sm" onClick={loadPrices}>
                  <RefreshCw className="mr-1.5 size-3.5" />
                  Retry
                </Button>
              </CanvasMessage>
            ) : selectedTag && selectedDoc ? (
              <TagCanvasEditor
                key={selectedTag.id}
                doc={selectedDoc}
                onChange={() => void save()}
                // D1: a promotion is a LINE fact now, not the request's - the
                // selected tag's own line carries it.
                promotionId={selectedLine?.promotion_id ?? null}
                boundData={boundData}
                onLayersChange={handleLayersChange}
                onUseTemplate={() =>
                  selectedRequestTagId && setPickerLineId(selectedRequestTagId)
                }
                hideSaveBar
                docId={selectedTag.id}
                toolbarTrailing={toolbarTrailing}
                reviewPins={canvasPins}
                onReviewPinResolve={handleReviewPinResolve}
              />
            ) : (
              <CanvasMessage text="Preparing this line..." />
            )
          ) : (
            <ArrangeSheetView
              doc={doc}
              activeSheetIndex={Math.min(activeSheetIndex, doc.sheets.length - 1)}
              onActiveSheetChange={setActiveSheetIndex}
              zoom={arrangeZoom}
              onZoomChange={setArrangeZoom}
              selectedTagId={selectedTagId}
              onSelectTag={setSelectedTagId}
              resolved={resolved}
              assetUrls={library.assetUrls}
              onPrintSheet={handlePrintSheet}
              printing={printing}
              placement={arranged.placement}
              templateNameById={templateNameById}
            />
          )}
        </div>
      </div>

      {/* r10 S8: an UPDATED tag (the pin already moved) reviews what changed
          with Dismiss / Roll back; a tag with a pending diff and no update
          keeps the r9 Keep / Update decision - the flag-only statuses. */}
      <ProductDataReviewDialog
        open={
          reviewTagId !== null && changesByTag.has(reviewTagId) && !updatedTags.has(reviewTagId)
        }
        onOpenChange={(next) => {
          if (!next) setReviewTagId(null);
        }}
        changeSet={reviewTagId ? (changesByTag.get(reviewTagId) ?? null) : null}
        onDecide={(action) =>
          reviewTagId ? decideTagPin(reviewTagId, action) : Promise.resolve()
        }
      />
      {reviewTagId !== null && updatedTags.has(reviewTagId) && (
        <ProductDataUpdatedDialog
          open
          onOpenChange={(next) => {
            if (!next) setReviewTagId(null);
          }}
          code={resolved.get(reviewTagId)?.code ?? ''}
          tagLabel={updatedTags.get(reviewTagId)?.label ?? ''}
          name={resolved.get(reviewTagId)?.name}
          changes={updatedTags.get(reviewTagId)?.data_update_changes ?? []}
          version={updatedTags.get(reviewTagId)?.data_update_version ?? null}
          onDismiss={() => handleDismissUpdate(reviewTagId)}
          onRollBack={restoreVersion}
        />
      )}

      <RequestVersionsSheet
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        docNumber={request.doc_number}
        load={() => listRequestVersions(request.id)}
        onView={(version) => {
          void getRequestVersion(request.id, version)
            .then((versionPayload) =>
              setViewing({ version, payload: versionPayload }),
            )
            .catch(() => toast.error('Could not open that version'));
        }}
        onRestore={restoreVersion}
      />

      {/* A version, read-only, in the same lightbox the detail page uses (and
          the CRM detail's own History View draws it: `RequestDesignSection`).
          A request version IS a whole tag sheet document, so it answers the
          same payload a live design does - drawing today's draft under a
          version's name would tell the reader that v1 looked like something
          it never looked like. */}
      {viewing && (
        <DesignLightbox
          open
          onOpenChange={(next) => {
            if (!next) setViewing(null);
          }}
          title={`${request.doc_number} / version ${viewing.version}`}
          payload={viewing.payload}
        />
      )}

      <TemplatePickDialog
        open={pickerLineId !== null}
        templates={templates}
        currentTemplateId={
          pickerLineId ? tags[pickerLineId]?.template_id ?? null : null
        }
        preferredFamily={
          pickerLineId
            ? lineFamily(
                request.lines.find((l) => l.id === pickerLineId) ?? {
                  line_type: 'product' as const,
                },
                resolved.get(pickerLineId)?.code,
              )
            : null
        }
        onCancel={() => setPickerLineId(null)}
        onConfirm={handleTemplateChosen}
      />

      <SaveAsTemplateDialog
        open={saveTemplateOpen}
        onOpenChange={setSaveTemplateOpen}
        tag={selectedTag}
        defaultName={saveTemplateDefaultName}
        defaultFamily={saveTemplateDefaultFamily}
        onCreated={handleTemplateCreated}
      />

      {updateEligibleTemplate && (
        <UpdateTemplateDialog
          open={updateTemplateOpen}
          onOpenChange={setUpdateTemplateOpen}
          templateName={updateEligibleTemplate.name}
          nextVersionNo={updateNextVersionNo}
          siblingCount={updateSiblingCount}
          saving={updatingTemplate}
          onConfirm={handleUpdateTemplate}
        />
      )}
    </div>
    </FocusShell>
  );
}

// ---------------------------------------------------------------------------
// The canvas's own placeholder states (loading / resolving / error) - the
// design page must always say what it is waiting for rather than sitting on
// a bare, permanent "Preparing this line..." (#476).
// ---------------------------------------------------------------------------

function CanvasMessage({
  text,
  children,
}: {
  text: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <p className="text-sm text-muted-foreground">{text}</p>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The lines rail
// ---------------------------------------------------------------------------

function LinesRail({
  lines,
  resolved,
  pricesStatus,
  tags,
  openPinsByTag,
  changedTagIds,
  onReviewTag,
  selectedRequestTagId,
  onSelect,
  onUseTemplate,
  canApplyToAll,
  onApplyToAll,
  onRemoveTag,
  removingTagId,
  onTogglePrintExcluded,
  togglingTagId,
}: {
  lines: PriceTagRequestLine[];
  /** Resolved rows keyed by TAG id (D3). */
  resolved: Map<string, LineTagData>;
  pricesStatus: 'loading' | 'loaded' | 'error';
  tags: Record<string, PlacedTag>;
  /** Tag id -> open change requests, for the badge (r9 S2/D6). */
  openPinsByTag: Map<string, number>;
  /** Tags whose product data has moved under the pin (r9 S5/D18). */
  changedTagIds: Set<string>;
  onReviewTag: (tagId: string) => void;
  selectedRequestTagId: string | null;
  onSelect: (tagId: string) => void;
  onUseTemplate: (tagId: string) => void;
  /** Something is selected AND there is more than one tag to spread it to (AC-S5-1). */
  canApplyToAll: boolean;
  onApplyToAll: () => void;
  onRemoveTag: (tag: PriceTagRequestTag) => void;
  removingTagId: string | null;
  /** r10 S6: the row's Not printed toggle. */
  onTogglePrintExcluded: (tag: PriceTagRequestTag) => void;
  togglingTagId: string | null;
}) {
  return (
    // D9 (AC-S3-2/S3-3): the 45% cap is gone - LINES fills whatever height
    // TAG SIZE (shrink-0, right below it) does not need, so the two sit
    // foot-to-foot with no dead space between them and no new splitter.
    <div className="flex min-h-0 flex-1 flex-col border-b border-r">
      <div className="flex h-10 shrink-0 items-center justify-between gap-1 border-b px-3">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Lines
        </span>
        <button
          type="button"
          className="flex shrink-0 items-center gap-1 rounded p-1 text-2xs text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
          title="Apply this design to all tags"
          disabled={!canApplyToAll}
          onClick={onApplyToAll}
        >
          <Copy className="size-3" />
          Apply to all
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {lines.length === 0 ? (
          <p className="px-3 py-4 text-center text-xs text-muted-foreground">
            This request has no lines.
          </p>
        ) : (
          <div className="divide-y">
            {lines.map((line) => {
              const lineTags = line.tags ?? [];
              // The line's own identity comes off its FIRST tag's resolved row -
              // every tag on a line prints the same host product, so the code and
              // the name are a line fact even though the rows are per tag.
              const row = lineTags.length > 0 ? resolved.get(lineTags[0].id) : undefined;
              // Prices resolution finished and this line still has no row: its
              // product could not be resolved in this request's company (a
              // cross-company reference, or a genuinely deleted product) - not
              // "still loading". See `457_ptag_line_xco_repair`.
              const notFound = pricesStatus === 'loaded' && !row;
              const code = row?.code ?? '';
              const name = row?.name ?? '';
              // Sorento's product name IS its code (S2): a name that only
              // repeats the code is redundant on the rail as it is on the tag.
              const showName = distinctName(code, name) !== null;
              const family = familyLabel(lineFamily(line, code));
              // D8: a line with exactly ONE tag, NO parts and NO open group
              // renders as ONE selectable block - the line block IS the tag
              // row, with no separate "1a" underneath it. A line with parts,
              // two or more tags, or an open group keeps today's shape.
              const soleTag = lineTags.length === 1 ? lineTags[0] : null;
              const folded =
                soleTag !== null &&
                (line.parts ?? []).length === 0 &&
                (soleTag.open_groups ?? []).length === 0;
              if (folded && soleTag) {
                return (
                  <FoldedLineBlock
                    key={line.id}
                    line={line}
                    tag={soleTag}
                    data={row}
                    code={code}
                    name={name}
                    showName={showName}
                    family={family}
                    notFound={notFound}
                    designed={Boolean(tags[soleTag.id])}
                    selected={selectedRequestTagId === soleTag.id}
                    openPins={openPinsByTag.get(soleTag.id) ?? 0}
                    changed={changedTagIds.has(soleTag.id)}
                    onReview={onReviewTag}
                    onSelect={onSelect}
                    onUseTemplate={onUseTemplate}
                    toggling={togglingTagId === soleTag.id}
                    onTogglePrintExcluded={onTogglePrintExcluded}
                  />
                );
              }
              return (
                <div key={line.id}>
                  <div className="px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      <Badge variant="secondary" className="shrink-0 px-1 py-0 text-2xs">
                        {line.line_type === 'product' ? 'P' : 'Set'}
                      </Badge>
                      <span
                        className="truncate font-mono text-xs text-muted-foreground"
                        title={code}
                      >
                        {code || (notFound ? 'Not found' : 'Resolving...')}
                      </span>
                    </div>
                    {notFound ? (
                      <Badge
                        variant="destructive"
                        appearance="light"
                        className="mt-1 px-1.5 py-0 text-2xs font-normal"
                      >
                        Product not found in this company
                      </Badge>
                    ) : (
                      <>
                        {showName && (
                          <p className="mt-0.5 truncate text-xs" title={name}>
                            {name}
                          </p>
                        )}
                        <p className="mt-0.5 truncate text-2xs text-muted-foreground">
                          Qty {line.quantity} / {family}
                        </p>
                        {line.package_warning && (
                          <Badge
                            variant="warning"
                            appearance="light"
                            className="mt-1 px-1.5 py-0 text-2xs font-normal"
                          >
                            {line.package_warning}
                          </Badge>
                        )}
                        {line.remarks && (
                          <p
                            className="mt-0.5 truncate text-2xs text-muted-foreground"
                            title={line.remarks}
                          >
                            {line.remarks}
                          </p>
                        )}
                      </>
                    )}
                  </div>
                  {/* The tags under the line: one by default, N after a split
                      (D3). Each is what the canvas actually edits, so the r9
                      badges - open change requests, product data moved - hang
                      off the TAG rather than off the line above them. */}
                  <div className="border-t">
                    {lineTags.map((tag) => (
                      <TagRailRow
                        key={tag.id}
                        tag={tag}
                        data={resolved.get(tag.id)}
                        designed={Boolean(tags[tag.id])}
                        selected={selectedRequestTagId === tag.id}
                        canRemove={lineTags.length > 1}
                        removing={removingTagId === tag.id}
                        openPins={openPinsByTag.get(tag.id) ?? 0}
                        changed={changedTagIds.has(tag.id)}
                        onReview={onReviewTag}
                        onSelect={onSelect}
                        onUseTemplate={onUseTemplate}
                        onRemove={onRemoveTag}
                        toggling={togglingTagId === tag.id}
                        onTogglePrintExcluded={onTogglePrintExcluded}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * D8 (AC-S4-1/S4-2): a line with exactly one tag, no parts and no open group
 * is one selectable block on the rail - the line's own identity (type, code,
 * name, warning, remarks) plus that one tag's price, designed check and
 * action group folded into it, with no "1a" underneath. Everything else (a
 * second tag, a part) keeps `TagRailRow`'s separate row instead. An open
 * group never reaches the rail any more (D6) - every choice is a tag by the
 * time a request opens here.
 */
function FoldedLineBlock({
  line,
  tag,
  data,
  code,
  name,
  showName,
  family,
  notFound,
  designed,
  selected,
  openPins,
  changed,
  onReview,
  onSelect,
  onUseTemplate,
  toggling,
  onTogglePrintExcluded,
}: {
  line: PriceTagRequestLine;
  tag: PriceTagRequestTag;
  data: LineTagData | undefined;
  code: string;
  name: string;
  showName: boolean;
  family: string;
  notFound: boolean;
  designed: boolean;
  selected: boolean;
  openPins: number;
  changed: boolean;
  onReview: (tagId: string) => void;
  onSelect: (tagId: string) => void;
  onUseTemplate: (tagId: string) => void;
  toggling: boolean;
  onTogglePrintExcluded: (tag: PriceTagRequestTag) => void;
}) {
  const excluded = Boolean(tag.print_excluded);
  const priceSuffix =
    data && data.show_promo_price && data.sell_price != null
      ? ` / SP ${formatTagPrice(data.sell_price)}`
      : data && data.list_price != null
        ? ` / LP ${formatTagPrice(data.list_price)}`
        : '';
  const overrideSuffix =
    tag.marketing_price_override != null
      ? ` / Override ${formatTagPrice(tag.marketing_price_override)}`
      : '';
  return (
    <div className={cn('relative border-b last:border-b-0', selected && 'bg-accent')}>
      <button
        type="button"
        className={cn(
          'w-full px-3 py-2 pr-24 text-left transition-colors hover:bg-muted/50',
          excluded && 'opacity-60',
        )}
        onClick={() => onSelect(tag.id)}
      >
        <div className="flex items-center gap-1.5">
          <Badge variant="secondary" className="shrink-0 px-1 py-0 text-2xs">
            {line.line_type === 'product' ? 'P' : 'Set'}
          </Badge>
          <span className="truncate font-mono text-xs text-muted-foreground" title={code}>
            {code || (notFound ? 'Not found' : 'Resolving...')}
          </span>
          {designed && <Check className="size-3 shrink-0 text-emerald-600" />}
        </div>
        {notFound ? (
          <Badge
            variant="destructive"
            appearance="light"
            className="mt-1 px-1.5 py-0 text-2xs font-normal"
          >
            Product not found in this company
          </Badge>
        ) : (
          <>
            {showName && (
              <p className="mt-0.5 truncate text-xs" title={name}>
                {name}
              </p>
            )}
            <p className="mt-0.5 truncate text-2xs text-muted-foreground">
              Qty {line.quantity} / {family}
              {priceSuffix}
              {overrideSuffix}
            </p>
            {excluded && <NotPrintedPill />}
            {line.package_warning && (
              <Badge
                variant="warning"
                appearance="light"
                className="mt-1 px-1.5 py-0 text-2xs font-normal"
              >
                {line.package_warning}
              </Badge>
            )}
            {line.remarks && (
              <p className="mt-0.5 truncate text-2xs text-muted-foreground" title={line.remarks}>
                {line.remarks}
              </p>
            )}
          </>
        )}
      </button>
      {/* Same action group `TagRailRow` uses (D12): pins first, then the
          Changed dot, then Not printed, then Use template. No Remove here - a
          folded line's only tag is already un-removable (`canRemove` is false
          when a line has one tag), so the button never showed for it anyway. */}
      <div className="absolute right-1 top-1 flex items-center gap-0.5">
        {openPins > 0 && (
          <span
            className="flex size-4 shrink-0 items-center justify-center rounded-full bg-orange-500 text-2xs font-semibold text-white"
            title={`${openPins} open change request${openPins === 1 ? '' : 's'}`}
          >
            {openPins}
          </span>
        )}
        {changed && (
          <button
            type="button"
            className="size-2.5 rounded-full bg-destructive"
            title="Product data changed - review"
            aria-label={`Review product data changes on ${data?.code || 'this tag'} ${tag.label}`}
            onClick={() => onReview(tag.id)}
          />
        )}
        <NotPrintedToggle
          tag={tag}
          excluded={excluded}
          toggling={toggling}
          onToggle={onTogglePrintExcluded}
        />
        <button
          type="button"
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          title="Use template..."
          aria-label={`Use template for tag ${tag.label}`}
          onClick={() => onUseTemplate(tag.id)}
        >
          <LayoutTemplate className="size-3.5" />
        </button>
      </div>
    </div>
  );
}

/** r10 S6: the pill a row marked Not printed wears (AC-S6-9). */
function NotPrintedPill() {
  return (
    <Badge
      variant="secondary"
      appearance="light"
      className="mt-1 px-1.5 py-0 text-2xs font-normal"
      data-testid="not-printed-pill"
    >
      Not printed
    </Badge>
  );
}

/**
 * r10 S6 (AC-S6-9): the Not printed toggle. `aria-pressed` carries the state,
 * the label stays the same either way so a reader hears one control, not two.
 * A tag marked this way is still openable and editable - only arrange, the
 * PDF and the counts skip it - so the row itself stays live.
 */
function NotPrintedToggle({
  tag,
  excluded,
  toggling,
  onToggle,
}: {
  tag: PriceTagRequestTag;
  excluded: boolean;
  toggling: boolean;
  onToggle: (tag: PriceTagRequestTag) => void;
}) {
  return (
    <button
      type="button"
      className={cn(
        'rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40',
        excluded && 'text-foreground',
      )}
      title={excluded ? 'Not printed - press to print again' : 'Not printed'}
      aria-label={`Not printed ${tag.label}`}
      aria-pressed={excluded}
      disabled={toggling}
      onClick={() => onToggle(tag)}
    >
      <EyeOff className="size-3.5" />
    </button>
  );
}

/**
 * One tag under a line in the rail (D3, AC-S3-3/4).
 *
 * Shown as "1a"/"1b" - the line's position plus a letter - never an id
 * (AC-X-2). D6 (PLAN-price-tag-line-promo-combo-subject.md): a choice group
 * is split into one tag per candidate at submit time, before the designer
 * ever opens - so this row never carries an open group, a Split button or a
 * Pick one select any more.
 */
function TagRailRow({
  tag,
  data,
  designed,
  selected,
  canRemove,
  removing,
  openPins,
  changed,
  onReview,
  onSelect,
  onUseTemplate,
  onRemove,
  toggling,
  onTogglePrintExcluded,
}: {
  tag: PriceTagRequestTag;
  data: LineTagData | undefined;
  designed: boolean;
  selected: boolean;
  /** False on a line's ONLY tag: a line with no tags can never be printed and
   *  never designed, so the server refuses that one (AC-S3-6). */
  canRemove: boolean;
  removing: boolean;
  /** Open change requests pinned on THIS tag, for the badge (r9 S2/D6). */
  openPins: number;
  /** Master data has moved under this tag's pin (r9 S5/D18). */
  changed: boolean;
  onReview: (tagId: string) => void;
  onSelect: (tagId: string) => void;
  onUseTemplate: (tagId: string) => void;
  onRemove: (tag: PriceTagRequestTag) => void;
  toggling: boolean;
  onTogglePrintExcluded: (tag: PriceTagRequestTag) => void;
}) {
  const excluded = Boolean(tag.print_excluded);
  // D6: every choice group is resolved into its own tag at submit now (the
  // tag builder), so `open_groups` is always empty by the time a request
  // reaches the designer - no Open pill, no Split, no Pick one anywhere in
  // this rail (AC-S3-1).
  const chosen = tag.choices_display.map((choice) => choice.code).join(', ');
  return (
    <div className={cn('relative border-b last:border-b-0', selected && 'bg-accent')}>
      <button
        type="button"
        className={cn(
          'w-full py-1.5 pl-6 pr-28 text-left transition-colors hover:bg-muted/50',
          excluded && 'opacity-60',
        )}
        onClick={() => onSelect(tag.id)}
      >
        <div className="flex items-center gap-1.5">
          <span className="shrink-0 font-mono text-2xs text-muted-foreground">
            {tag.label}
          </span>
          {chosen ? (
            <span className="truncate font-mono text-2xs" title={chosen}>
              {chosen}
            </span>
          ) : null}
          {designed && <Check className="size-3 shrink-0 text-emerald-600" />}
        </div>
        <p className="mt-0.5 truncate text-2xs text-muted-foreground">
          Qty {tag.quantity}
          {data && data.show_promo_price && data.sell_price != null
            ? ` / SP ${formatTagPrice(data.sell_price)}`
            : data && data.list_price != null
              ? ` / LP ${formatTagPrice(data.list_price)}`
              : ''}
          {tag.marketing_price_override != null
            ? ` / Override ${formatTagPrice(tag.marketing_price_override)}`
            : ''}
        </p>
        {excluded && <NotPrintedPill />}
      </button>
      <div className="absolute right-1 top-1 flex items-center gap-0.5">
        {/* D12: the open-pins count is a SIBLING of the row button, first in
            this group, so it never overlaps Use template / Remove at any
            count - it used to sit `ml-auto` INSIDE the row button, which
            crowded whatever came after it as the number grew. */}
        {openPins > 0 && (
          <span
            className="flex size-4 shrink-0 items-center justify-center rounded-full bg-orange-500 text-2xs font-semibold text-white"
            title={`${openPins} open change request${openPins === 1 ? '' : 's'}`}
          >
            {openPins}
          </span>
        )}
        {/* Outside the row button, like Use template beside it: a button inside
            a button is invalid HTML, and React says so in the console. */}
        {changed && (
          <button
            type="button"
            className="mr-1 size-2.5 rounded-full bg-destructive"
            title="Product data changed - review"
            aria-label={`Review product data changes on ${data?.code || 'this tag'} ${tag.label}`}
            onClick={() => onReview(tag.id)}
          />
        )}
        <NotPrintedToggle
          tag={tag}
          excluded={excluded}
          toggling={toggling}
          onToggle={onTogglePrintExcluded}
        />
        <button
          type="button"
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          title="Use template..."
          aria-label={`Use template for tag ${tag.label}`}
          onClick={() => onUseTemplate(tag.id)}
        >
          <LayoutTemplate className="size-3.5" />
        </button>
        {/* Disabled with a REASON: a disabled button with no explanation reads
            as broken. The server answers this case with a named 422, and the UI
            says the same thing before the click rather than after it. */}
        <button
          type="button"
          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-destructive disabled:pointer-events-none disabled:opacity-40"
          title={canRemove ? 'Remove tag' : "This is the line's only tag"}
          aria-label={`Remove tag ${tag.label}`}
          disabled={!canRemove || removing}
          onClick={() => onRemove(tag)}
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
    </div>
  );
}

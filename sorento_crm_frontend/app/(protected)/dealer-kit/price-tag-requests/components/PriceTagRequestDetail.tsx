'use client';

/**
 * CRM price tag request detail view.
 *
 * The same page as every other record in this app (D50), and after the Apple
 * alignment S3/S5 merge that means the stock-transfer shape exactly: `PageHeader`
 * (trail derived from the sidebar, title + ONE Back on the toolbar row), then a
 * record card carrying the document number, its status pill and the read-only
 * metadata, with `DetailActions` (page-scoped pager, gear, one primary CTA) on
 * its right. Everything else - the request, its lines and its sales order
 * attachments - lives in tabs (D25), the same shape `StockTransferDetail` and
 * `SPODocumentDetail` use: `Tabs`/`TabsList variant="line"` under the record
 * card, one `Card` per tab.
 *
 * The title is the DOC NUMBER rather than the word "Details", which is why it
 * lives here and not in the route's server component: that one holds the id,
 * and no id reaches a screen.
 *
 * Which action is primary and which are secondary is `priceTagActions`, so the
 * page never has to decide twice. There used to be a second "Open the
 * designer" button living in a standalone Proof card; `priceTagActions`
 * already puts `design`/`view design` first whenever it is legal, so that
 * button was a duplicate of the header's own primary CTA and is gone with the
 * card (D25). The Proof tab itself is gone too (D10): the design lives in the
 * designer, one click away through the same primary CTA.
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import {
  Download,
  Eye,
  FileText,
  HandCoins,
  ListOrdered,
  Loader2,
  Package,
  Paperclip,
  RefreshCw,
  Palette,
  PencilLine,
  UserPlus,
  XCircle,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useHasPermission } from '@/hooks/usePermissions';
import { useIsMobile } from '@/hooks/use-mobile';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import BackToList from '@/components/common/BackToList';
import DetailActions from '@/components/common/DetailActions';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import { PageHeader } from '@/components/common/PageHeader';
import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import { priceTagRequestsPagerQuery } from '../lib/listQuery';
import {
  priceTagStatusLabel,
  priceTagStatusPillClass,
} from '@/lib/price-tag-status';
import {
  formatDate,
  formatDateInMalaysia,
  formatDateTimeInMalaysia,
} from '@/lib/helpers';
import { tagsFromDoc } from '@/lib/dealer-kit/request-tags';
import {
  getPriceTagRequest,
  getTagSheetDoc,
  claimPriceTagRequest,
  transitionPriceTagRequest,
  exportTagSheet,
  markReadyForCollection,
  markCollected,
  updatePriceTagPrintBy,
  lookupLinePricing,
  updatePriceTagLinePrice,
  type PriceTagRequestDetail as PriceTagRequestDetailType,
  type PriceTagRequestLine,
  type LinePricingResult,
} from '../../services/priceTagRequestService';
import {
  AUTO_COLLECT_DAYS_DEFAULT,
  autoCollectOn,
  isTerminalPriceTagStatus,
  printByLabel,
  type PrintBy,
} from '@/lib/dealer-kit/print-collection';
import { PrintBySelect } from '@/components/dealer-kit/PrintBySelect';
import ProductDataReviewDialog from '@/components/dealer-kit/ProductDataReviewDialog';
import {
  recheckTagDataChanges,
  resolveTagPin,
} from '../../services/priceTagDataService';
import { useTagDataChanges, tagDataChangesKey } from '../hooks/useTagDataChanges';
import {
  AUTO_UPDATE_STATUSES,
  type TagDataChangeSet,
} from '@/lib/dealer-kit/product-data-changes';
import { promotionOptionName } from '@/lib/dealer-kit/line-pricing-types';
import RequestDesignSection from './RequestDesignSection';
import {
  openComments,
  type ReviewComment,
} from '@/lib/dealer-kit/review-comments';
import {
  priceTagActions,
  type PriceTagAction,
  type PriceTagActionSpec,
} from './priceTagRequestActions';
import { codeWithName, distinctName } from '@/lib/product-display';

type DetailTab = 'request' | 'lines' | 'attachments';

const STATUS_PILL_BASE =
  'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold';

const ACTION_ICON: Record<PriceTagAction, typeof UserPlus> = {
  claim: UserPlus,
  design: Palette,
  mark_proof_ready: Eye,
  mark_ready_for_collection: Package,
  mark_collected: HandCoins,
  export: Download,
  void: XCircle,
};

interface Props {
  requestId: string;
}

type LinePriceOverride = {
  promotion_id: string | null;
  manual_sell_price: number | null;
  locked: boolean;
};

/** F4 (reviewer B2): every line's SAVED promotion/manual price, as the
 *  override map already shapes it - `locked: true`, since a value the
 *  server persisted is exactly as deliberate as a pick the viewer just
 *  made this session, and must not be overridden by the auto pick. */
function seedLinePriceOverrides(
  lines: PriceTagRequestLine[],
): Record<string, LinePriceOverride> {
  return Object.fromEntries(
    lines
      .filter((line) => line.line_type === 'product' && line.product_id)
      .map((line) => [
        line.id,
        {
          promotion_id: line.promotion_id ?? null,
          manual_sell_price: line.manual_sell_price ?? null,
          locked: line.promotion_id != null || line.manual_sell_price != null,
        },
      ]),
  );
}

export default function PriceTagRequestDetail({ requestId }: Props) {
  const router = useRouter();
  // Owner ruling after #948: Promotion / Selling price are real table
  // columns on desktop, not a `bg-muted/20` sub-row. Below the mobile
  // breakpoint they stack under the line's Name cell instead.
  const isMobile = useIsMobile();
  const [request, setRequest] = useState<PriceTagRequestDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [exportLoading, setExportLoading] = useState(false);
  /** The gear's Edit request modal (r9 D7): today it holds the print choice. */
  const [editOpen, setEditOpen] = useState(false);
  const [reviewTagId, setReviewTagId] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [tab, setTab] = useState<DetailTab>('request');
  // Which lines already have a tag drawn, so the Lines tab can say so per row
  // without a second page of clicking (D25/AC-S10-2). Fetched once, off the
  // same tag-sheet doc the designer itself reads and writes.
  const [designedTagIds, setDesignedTagIds] = useState<Set<string>>(new Set());
  // The salesperson's pinned change requests, held here because the record
  // card's primary CTA counts the open ones (r9 D6) and the Design section is
  // what fetches them.
  const [reviewComments, setReviewComments] = useState<ReviewComment[]>([]);
  // D5/S5: a line's own promotion/manual price. `request.lines` already
  // carries the SAVED value (S6-S9), but this stays a separate map: `locked`
  // marks a deliberate pick or clear (S1's rule, mirrored here) so the auto
  // pick never overwrites it, and it is what the select/input actually bind
  // to while the viewer is choosing. Seeded from `request.lines` on load
  // (`seedLinePriceOverrides`, F4) so a saved pick opens on itself, not on
  // whatever `lookupLinePricing` auto-picks this session.
  const [linePriceOverrides, setLinePriceOverrides] = useState<
    Record<string, LinePriceOverride>
  >({});
  const [savingLinePrice, setSavingLinePrice] = useState<string | null>(null);
  const canProcessPrice = useHasPermission('dealer_kit.price_tag_requests.process');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getPriceTagRequest(requestId)
      .then((data) => {
        if (cancelled) return;
        setRequest(data);
        // F4 (reviewer B2): without this, `linePriceOverrides` starts empty
        // and `effectivePromotionId` falls back to the AUTO pick until the
        // viewer makes a fresh choice this session - a line saved with a
        // real promotion opened on whatever `lookupLinePricing` auto-picked
        // instead of what was actually saved.
        setLinePriceOverrides(seedLinePriceOverrides(data?.lines ?? []));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [requestId]);

  useEffect(() => {
    let cancelled = false;
    getTagSheetDoc(requestId)
      .then((doc) => {
        if (cancelled) return;
        // Keyed by REQUEST TAG id since S3 (D3): `tagsFromDoc` reads the
        // document's own key, which is what a "Designed" cell asks about.
        setDesignedTagIds(new Set(tagsFromDoc(doc).keys()));
      })
      .catch(() => {
        // No design yet, or the fetch failed - every line reads "No tag",
        // which is the correct empty state either way.
      });
    return () => {
      cancelled = true;
    };
  }, [requestId]);

  // What master data has changed under the pinned tags (r9 S5/D18), polled
  // every 30s (AC-C1/AC-C3) instead of loaded once on mount - a product
  // edited in another tab reaches this pill with no reload.
  const queryClient = useQueryClient();
  const { data: dataChanges = [] } = useTagDataChanges(requestId, {
    enabled: !!request && !isTerminalPriceTagStatus(request.status, request.print_by),
  });

  const decideTagPin = useCallback(
    async (tagId: string, action: 'update' | 'keep') => {
      try {
        await resolveTagPin(requestId, tagId, action);
        await queryClient.invalidateQueries({ queryKey: tagDataChangesKey(requestId) });
        toast.success(action === 'update' ? 'Tag updated' : 'Kept the current tag');
      } catch {
        toast.error('Could not apply that decision');
      }
    },
    [requestId, queryClient],
  );

  /**
   * "Check product data" (owner round finding 3): a Keep silences ONE drift
   * by recording its hash, and there was no way to ask again, so a red dot
   * silenced once stayed silent forever - even for a later, unrelated edit
   * that would have tripped the gate on its own. This re-arms every line.
   */
  const recheckDataChanges = useCallback(async () => {
    try {
      const rows = await recheckTagDataChanges(requestId);
      queryClient.setQueryData(tagDataChangesKey(requestId), rows);
      const changed = rows.filter((set) => set.changes.length > 0).length;
      toast.success(
        changed > 0
          ? `${changed} tag${changed === 1 ? '' : 's'} changed`
          : 'Product data is up to date',
      );
    } catch {
      toast.error('Could not check product data');
    }
  }, [requestId, queryClient]);

  const handleClaim = useCallback(async () => {
    setActionLoading(true);
    try {
      await claimPriceTagRequest(requestId);
      toast.success('Request claimed');
      const data = await getPriceTagRequest(requestId);
      setRequest(data);
    } catch {
      toast.error('Failed to claim request');
    } finally {
      setActionLoading(false);
    }
  }, [requestId]);

  const handleMarkProofReady = useCallback(async () => {
    setActionLoading(true);
    try {
      // The route takes the STATUS to move to, not an action name. It was sent
      // 'mark_proof_ready' since this page was written, which is not a status,
      // so every click came back 409 INVALID_TRANSITION. Measured on the lane.
      await transitionPriceTagRequest(requestId, 'proof_ready');
      toast.success('Design marked as ready');
      const data = await getPriceTagRequest(requestId);
      setRequest(data);
    } catch {
      toast.error('Failed to mark the design ready');
    } finally {
      setActionLoading(false);
    }
  }, [requestId]);

  const handleExport = useCallback(async () => {
    setExportLoading(true);
    try {
      const result = await exportTagSheet(requestId);
      toast.success(
        `PDF export queued. Check My Downloads for "${result.filename}".`,
      );
      // Refetch to reflect the status change (approved -> ready on first export).
      const data = await getPriceTagRequest(requestId);
      setRequest(data);
    } catch {
      toast.error('Failed to export PDF');
    } finally {
      setExportLoading(false);
    }
  }, [requestId]);

  const handleMarkReadyForCollection = useCallback(async () => {
    setActionLoading(true);
    try {
      await markReadyForCollection(requestId);
      toast.success('Marked ready for collection');
      const data = await getPriceTagRequest(requestId);
      setRequest(data);
    } catch {
      toast.error('Failed to mark the request ready for collection');
    } finally {
      setActionLoading(false);
    }
  }, [requestId]);

  const handleMarkCollected = useCallback(async () => {
    setActionLoading(true);
    try {
      await markCollected(requestId);
      toast.success('Marked collected');
      const data = await getPriceTagRequest(requestId);
      setRequest(data);
    } catch {
      toast.error('Failed to mark the request collected');
    } finally {
      setActionLoading(false);
    }
  }, [requestId]);

  const handleSavePrintBy = useCallback(
    async (next: PrintBy | null) => {
      setActionLoading(true);
      try {
        await updatePriceTagPrintBy(requestId, next);
        const data = await getPriceTagRequest(requestId);
        setRequest(data);
        setEditOpen(false);
        toast.success('Request updated');
      } catch {
        toast.error('Failed to update the request');
      } finally {
        setActionLoading(false);
      }
    },
    [requestId],
  );

  /**
   * Void is a server-deferred pending action (D7, S6): no dialog asks first,
   * the button becomes a countdown with a Cancel, and the server commits when
   * the window lapses even if this tab is closed.
   */
  const voiding = useDeferredAction({
    actionKey: 'price_tag_request.void',
    entityType: 'price_tag_request',
    entityId: requestId,
    verb: 'Voiding',
    subject: request?.doc_number ?? '',
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Request voided',
    onCommitted: () => router.push('/dealer-kit/price-tag-requests'),
  });

  const openDesigner = useCallback(() => {
    router.push(`/dealer-kit/price-tag-requests/${requestId}/design`);
  }, [requestId, router]);

  // A tag row's own Design action (AC-S10-2, AC-S3-2): the same designer,
  // opened with THAT tag pre-selected rather than whichever one the designer
  // defaults to. `?tag=` since S3; the designer still honours `?line=` for a
  // link written before it.
  const openDesignerForTag = useCallback(
    (tagId: string) => {
      router.push(
        `/dealer-kit/price-tag-requests/${requestId}/design?tag=${encodeURIComponent(tagId)}`,
      );
    },
    [requestId, router],
  );

  const runAction = useCallback(
    (action: PriceTagAction) => {
      if (action === 'claim') return void handleClaim();
      if (action === 'design') return openDesigner();
      if (action === 'mark_proof_ready') return void handleMarkProofReady();
      if (action === 'mark_ready_for_collection') {
        return void handleMarkReadyForCollection();
      }
      if (action === 'mark_collected') return void handleMarkCollected();
      if (action === 'export') return void handleExport();
      voiding.start();
    },
    [
      handleClaim,
      openDesigner,
      handleMarkProofReady,
      handleMarkReadyForCollection,
      handleMarkCollected,
      handleExport,
      voiding,
    ],
  );

  /**
   * Tag id -> what to call it, so a pin's rail entry never shows an id.
   *
   * The line's code, plus the tag's own label when the line prints more than
   * one ("SRT-1234 1b"): with a single tag the label would only repeat the
   * line's position back at a reader who can already see it.
   */
  const tagLabels = useMemo(() => {
    const labels = new Map<string, string>();
    for (const line of request?.lines ?? []) {
      const code = line.code || line.name || 'Tag';
      const tags = line.tags ?? [];
      for (const tag of tags) {
        labels.set(tag.id, tags.length > 1 ? `${code} ${tag.label}` : code);
      }
    }
    return labels;
  }, [request?.lines]);

  const openChangeRequests = useMemo(
    () => openComments(reviewComments).length,
    [reviewComments],
  );

  const actions: PriceTagActionSpec[] = useMemo(
    () =>
      request
        ? priceTagActions(
            request.status,
            request.assigned_to_id,
            openChangeRequests,
            request.print_by ?? null,
          )
        : [],
    [request, openChangeRequests],
  );
  const primary = actions[0] ?? null;
  const secondary = actions.slice(1);
  // The SAME predicate the header CTA and gear use to decide whether Design
  // (or its claimed-so-far "View design" phase) is legal right now - a row's
  // own Design action must never offer something the header itself would
  // refuse (review: row action was ungated, so an `approved`/`ready`/`void`
  // request still let a row jump into the designer).
  const canDesign = actions.some((spec) => spec.action === 'design');

  // D5/S5: this request's lines are terminal (collected/cancelled) once the
  // request itself is - the SAME predicate the price tag list and the
  // portal read view use, so the two never disagree about when a request
  // stops accepting changes (AC-S5-3).
  const isTerminal = request
    ? isTerminalPriceTagStatus(request.status, request.print_by ?? null)
    : true;
  const canEditLinePrice = canProcessPrice && !isTerminal;

  // D5/S5 (built, S7/S12): recomputed through the real `lookupLinePricing`
  // whenever the request or a line's own override changes. `locked` means
  // the salesperson/marketing has already decided this line's promotion
  // (picked or cleared, or SAVED - seeded on load, F4); until then the
  // effective promotion is the auto pick, same rule as the portal form.
  const [linePricing, setLinePricing] = useState<Record<string, LinePricingResult>>({});
  useEffect(() => {
    const lines = request?.lines ?? [];
    if (lines.length === 0) {
      setLinePricing({});
      return;
    }
    let cancelled = false;
    lookupLinePricing(
      request?.price_mode ?? 'list',
      lines
        .filter((l) => l.line_type === 'product' && l.product_id)
        .map((l) => {
          const override = linePriceOverrides[l.id];
          return {
            key: l.id,
            product_id: l.product_id,
            part_product_ids: (l.parts ?? [])
              .filter((p) => p.product_id)
              .map((p) => p.product_id as string),
            candidate_product_ids: [],
            promotion_id: override?.locked ? override.promotion_id : undefined,
          };
        }),
    ).then((results) => {
      if (!cancelled) setLinePricing(Object.fromEntries(results.map((r) => [r.key, r])));
    });
    return () => {
      cancelled = true;
    };
  }, [request, linePriceOverrides]);

  /** The line's effective promotion: the deliberate pick/clear if there is
   *  one, else the auto pick (S1's rule, mirrored on the CRM side). */
  const effectivePromotionId = useCallback(
    (lineId: string): string | null => {
      const override = linePriceOverrides[lineId];
      if (override?.locked) return override.promotion_id;
      return linePricing[lineId]?.auto_promotion_id ?? null;
    },
    [linePriceOverrides, linePricing],
  );

  const saveLinePrice = useCallback(
    async (
      lineId: string,
      patch: { promotion_id?: string | null; manual_sell_price?: number | null },
    ) => {
      if (!request) return;
      setSavingLinePrice(lineId);
      try {
        await updatePriceTagLinePrice(request.id, lineId, patch);
        // The route clears the line's tags' pins server-side (S11), so the
        // data-change banner picks up the price change alongside the
        // refreshed request - the same two calls every other mutation here
        // makes after a write.
        const data = await getPriceTagRequest(request.id);
        setRequest(data);
        await queryClient.invalidateQueries({ queryKey: tagDataChangesKey(request.id) });
        toast.success('Price basis updated');
      } catch (e) {
        toast.error(e instanceof Error ? e.message : 'Failed to update the price');
      } finally {
        setSavingLinePrice(null);
      }
    },
    [request, queryClient],
  );

  const choosePromotion = useCallback(
    (lineId: string, promotionId: string) => {
      const nextPromotionId = promotionId || null;
      setLinePriceOverrides((prev) => ({
        ...prev,
        [lineId]: { promotion_id: nextPromotionId, manual_sell_price: null, locked: true },
      }));
      // D2: picking a promotion clears any manual figure - including one
      // still sitting, uncommitted, in the draft the manual input reads.
      setManualPriceDraft((prev) => ({ ...prev, [lineId]: '' }));
      void saveLinePrice(lineId, { promotion_id: nextPromotionId, manual_sell_price: null });
    },
    [saveLinePrice],
  );

  // F7 (reviewer S7): the raw typed text, per line - updates on every
  // keystroke for the input's own display, but does NOT touch
  // `linePriceOverrides`. That map is a dependency of the `lookupLinePricing`
  // recompute effect above, so writing it per keystroke fired a network call
  // per keystroke too; nothing about typing a manual figure changes which
  // promotions cover the line, so there was nothing for that recompute to
  // answer differently anyway. Committed into `linePriceOverrides` (and
  // saved) only on blur, in `commitManualLinePrice`.
  const [manualPriceDraft, setManualPriceDraft] = useState<Record<string, string>>({});

  // A typed price that is not usable is not saved and not rewritten: the box keeps it and says why.
  const [manualPriceError, setManualPriceError] = useState<Record<string, string | undefined>>({});

  const manualPriceFollowRef = useRef<Record<string, boolean>>({});

  const setManualLinePrice = useCallback((lineId: string, value: string) => {
    setManualPriceDraft((prev) => ({ ...prev, [lineId]: value }));
    // Once a sentence has been shown it follows the typing in both directions.
    if (manualPriceFollowRef.current[lineId]) {
      setManualPriceError((prev) => ({
        ...prev,
        [lineId]: numberFieldError(value, { min: 0.01, optional: true }),
      }));
    }
  }, []);

  const commitManualLinePrice = useCallback(
    (lineId: string) => {
      const draft = manualPriceDraft[lineId];
      // Blank clears the override (no manual price); anything else must be a price.
      const message = numberFieldError(draft, { min: 0.01, optional: true });
      if (message) {
        manualPriceFollowRef.current[lineId] = true;
        setManualPriceError((prev) => ({ ...prev, [lineId]: message }));
        return;
      }
      const manual = parseNumberDraft(draft);
      setLinePriceOverrides((prev) => ({
        ...prev,
        [lineId]: { promotion_id: null, manual_sell_price: manual, locked: true },
      }));
      void saveLinePrice(lineId, { promotion_id: null, manual_sell_price: manual });
    },
    [manualPriceDraft, saveLinePrice],
  );

  // Sales Order attachments: standard preview/download, read-only - upload
  // stays portal-only (D4). Backed by the generic attachment download route,
  // same as every other CRM attachment list.
  const attachments = request?.attachments ?? [];
  const attachmentPreviewItems: AttachmentPreviewItem[] = useMemo(
    () =>
      (request?.attachments ?? []).map((att) => ({
        id: att.link_id,
        name: att.filename || 'Attachment',
        url: att.url ?? '',
        downloadUrl: `/api/v1/resource-management/attachments/${att.attachment_id}/download`,
      })),
    [request?.attachments],
  );

  const busy = actionLoading || exportLoading;

  /**
   * How long an untouched hand-over waits before it closes itself (D10/D11).
   *
   * Read off the narrow app-config projection every authenticated user may
   * read, not the settings blob - marketing works these requests without
   * `user_management.settings.view`.
   *
   * 0 is a real answer (the sweep is off), so the fallback is on the TYPE.
   */
  const { data: appConfig } = useQuery({
    queryKey: ['system-app-config'],
    queryFn: async () => {
      const response = await apiFetch('/api/user-management/settings/app-config');
      if (!response.ok) throw new Error('Failed to load settings');
      return response.json() as Promise<Record<string, unknown>>;
    },
    staleTime: 60_000,
  });
  const autoCollectDays =
    typeof appConfig?.price_tag_auto_collect_days === 'number'
      ? appConfig.price_tag_auto_collect_days
      : AUTO_COLLECT_DAYS_DEFAULT;

  /**
   * r10 S8: the pill counts a tag once whether master data moved under a pin
   * that is waiting on Keep / Update (the poll) or was already applied by an
   * auto-update and not yet dismissed (`data_updated_at` on the tag) - the
   * same union the designer's rail dots show.
   */
  const changedCount = useMemo(() => {
    const ids = new Set<string>();
    for (const set of dataChanges) {
      if (set.changes.length > 0) ids.add(set.tag_id);
    }
    for (const line of request?.lines ?? []) {
      for (const tag of line.tags ?? []) {
        if (tag.data_updated_at) ids.add(tag.id);
      }
    }
    return ids.size;
  }, [dataChanges, request?.lines]);
  const changesByTag = useMemo(() => {
    const map = new Map<string, TagDataChangeSet>();
    for (const set of dataChanges) {
      if (set.changes.length > 0) map.set(set.tag_id, set);
    }
    return map;
  }, [dataChanges]);
  const reviewSet = reviewTagId ? (changesByTag.get(reviewTagId) ?? null) : null;

  /** The office may fix the print choice until the request is finished (D7). */
  const canEditRequest =
    !!request && !isTerminalPriceTagStatus(request.status, request.print_by);

  /** "since 14/09/2026 / auto-collects 21/09/2026" under the record header. */
  const collectionSubline = (() => {
    if (!request || request.status !== 'ready_for_collection') return null;
    // Malaysia, from a UTC instant (S7): the backend's naive timestamps are
    // UTC, and `new Date(...)` + `formatDate` read both ends locally - so
    // 16:30 UTC printed as the 14th here and the 15th for the office.
    const since = request.ready_for_collection_at
      ? formatDateInMalaysia(request.ready_for_collection_at)
      : null;
    const auto = autoCollectOn(request.ready_for_collection_at, autoCollectDays);
    if (!since) return null;
    return auto
      ? `Ready since ${since} \u00b7 auto-collects ${formatDateInMalaysia(auto)}`
      : `Ready since ${since}`;
  })();

  // Back carries the list query the row click wrote, so the reader returns to the
  // page, sort, search and status filter they left (S3-01).
  const backLink = (
    <BackToList
      listPath="/dealer-kit/price-tag-requests"
      label="Back to price tag requests"
    />
  );

  /** The title is the DOC NUMBER, never the id: no UUID reaches a screen. */
  const header = (title: string) => (
    <PageHeader title={title} actions={backLink} />
  );

  if (loading) {
    return (
      <div className="space-y-4">
        {header('Price Tag Request')}
        <Skeleton className="h-32 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  if (!request) {
    return (
      <div className="space-y-4">
        {header('Price Tag Request')}
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <div className="text-sm font-semibold">Price tag request not found</div>
          <p className="max-w-md text-sm text-muted-foreground">
            This request doesn&apos;t exist, or it was removed after this link was
            made. Head back to the list to pick another.
          </p>
        </Card>
      </div>
    );
  }

  const PrimaryIcon = primary ? ACTION_ICON[primary.action] : null;

  return (
    <div className="space-y-4">
      {header(request.doc_number)}

      {/* The record header - what the request IS, and what can be done to it. */}
      <Card>
        <CardHeader className="block py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0 space-y-1">
              <div className="flex min-w-0 flex-wrap items-center gap-3">
                <CardTitle className="text-lg">{request.doc_number}</CardTitle>
                {request.status && (
                  <span
                    className={`${STATUS_PILL_BASE} ${priceTagStatusPillClass(request.status)}`}
                  >
                    {priceTagStatusLabel(request.status)}
                  </span>
                )}
                {/* The product data gate (r9 D18): the tags are drawn from what
                    was pinned, and this says how many of them master data has
                    moved under since. */}
                {changedCount > 0 && (
                  <span
                    className={`${STATUS_PILL_BASE} bg-amber-100 text-amber-800`}
                    data-testid="product-data-changed-pill"
                  >
                    {request.status && AUTO_UPDATE_STATUSES.includes(request.status)
                      ? `Product data updated · ${changedCount}`
                      : `Product data changed · ${changedCount}`}
                  </span>
                )}
              </div>
              {/* Read-only metadata lives in the header, never in a card body. */}
              <p className="text-sm text-muted-foreground">
                Created: {formatDateTimeInMalaysia(request.created_at)}
                {' · '}
                Need by:{' '}
                {request.needed_by_date
                  ? formatDate(new Date(request.needed_by_date))
                  : '-'}
                {' · '}
                Assigned to: {request.assigned_to_name ?? 'Unclaimed'}
                {' · '}
                Printing: {printByLabel(request.print_by)}
              </p>
              {collectionSubline && (
                <p className="text-sm text-muted-foreground">
                  {collectionSubline}
                </p>
              )}
              {request.status === 'collected' && request.collected_at && (
                <p className="text-sm text-muted-foreground">
                  Collected {formatDateInMalaysia(request.collected_at)}
                  {request.collected_auto
                    ? ' automatically'
                    : request.collected_by_name
                      ? ` by ${request.collected_by_name}`
                      : ''}
                </p>
              )}
            </div>
            {/* The workflow gear, not a record action set: which verbs exist depends
                on the status and on who claimed it, which `priceTagActions` decides. */}
            <DetailActions
              pager={{
                ...priceTagRequestsPagerQuery,
                detailPath: '/dealer-kit/price-tag-requests',
                currentId: requestId,
                ariaLabel: 'price tag request',
              }}
              gearLabel="Price tag request actions"
              gear={
                secondary.length > 0 || canEditRequest ? (
                  <DetailActionsMenu ariaLabel="Price tag request actions">
                    {canEditRequest && (
                      <DropdownMenuItem
                        disabled={busy}
                        onSelect={(event) => {
                          event.preventDefault();
                          setEditOpen(true);
                        }}
                      >
                        <PencilLine className="size-4" />
                        Edit request
                      </DropdownMenuItem>
                    )}
                    {canEditRequest && (
                      <DropdownMenuItem
                        disabled={busy}
                        onSelect={(event) => {
                          event.preventDefault();
                          void recheckDataChanges();
                        }}
                      >
                        <RefreshCw className="size-4" />
                        Check product data
                      </DropdownMenuItem>
                    )}
                    {secondary.map((spec) => {
                      const Icon = ACTION_ICON[spec.action];
                      return (
                        <DropdownMenuItem
                          key={spec.action}
                          disabled={busy}
                          variant={spec.destructive ? 'destructive' : undefined}
                          onSelect={(event) => {
                            event.preventDefault();
                            runAction(spec.action);
                          }}
                        >
                          <Icon className="size-4" />
                          {spec.label}
                        </DropdownMenuItem>
                      );
                    })}
                  </DetailActionsMenu>
                ) : null
              }
              pendingAction={voiding.countdown}
              primary={
                primary ? (
                  <Button
                    variant="primary"
                    size="sm"
                    className="gap-1.5"
                    disabled={busy}
                    onClick={() => runAction(primary.action)}
                    data-testid="price-tag-primary-cta"
                  >
                    {busy ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      PrimaryIcon && <PrimaryIcon className="size-4" />
                    )}
                    {primary.label}
                  </Button>
                ) : null
              }
            />
          </div>
        </CardHeader>
      </Card>

      {/* Request / Lines / Sales Order (D25): the same record, three tabs
          instead of stacked cards - view and edit are the same layout, and
          there is no edit here beyond what the header's own actions already
          do. */}
      <Tabs value={tab} onValueChange={(v) => setTab(v as DetailTab)} className="w-full">
        <TabsList variant="line" className="mb-4 w-full justify-start">
          <TabsTrigger value="request">
            <FileText />
            <span>Request</span>
          </TabsTrigger>
          <TabsTrigger value="lines">
            <ListOrdered />
            <span>Lines</span>
          </TabsTrigger>
          <TabsTrigger value="attachments">
            <Paperclip />
            <span>Sales Order</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="request" className="mt-0 space-y-4 focus-visible:outline-none">
          {/* The design first (r9 D3): the question this page is opened to
              answer is what the tags look like, and the request's own fields
              are the reference beneath it. */}
          <RequestDesignSection
            requestId={requestId}
            docNumber={request.doc_number}
            tagLabels={tagLabels}
            onCommentsChange={setReviewComments}
            currentRound={request.review_round}
          />

          <Card>
            <CardContent className="px-4 py-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 text-sm">
                <div>
                  <span className="text-muted-foreground block">Customer</span>
                  {/* A portal draft may carry neither (D48a). */}
                  <p className="font-medium">{request.debtor_name ?? '-'}</p>
                  {request.debtor_code && (
                    <p className="text-xs text-muted-foreground">
                      {request.debtor_code}
                    </p>
                  )}
                </div>
                <div>
                  <span className="text-muted-foreground block">Salesperson</span>
                  <p className="font-medium">{request.contact_name ?? '-'}</p>
                </div>
                {/* D1: a promotion is per LINE now (see the Lines tab) -
                    there is no single request-level promotion to name here
                    any more. */}
                <div>
                  <span className="text-muted-foreground block">Price</span>
                  <p className="font-medium">
                    {(request.price_mode ?? 'list') === 'selling'
                      ? 'Selling price'
                      : 'List price'}
                  </p>
                </div>
                <div>
                  <span className="text-muted-foreground block">Printing</span>
                  {/* A request from before the choice existed reads "Not set",
                      and the collection CTA stays hidden until it is (D7). */}
                  <p className="font-medium">{printByLabel(request.print_by)}</p>
                </div>
              </div>
              <div className="mt-4">
                <span className="text-sm text-muted-foreground block">Notes</span>
                <p className="text-sm mt-1">
                  {request.notes ? (
                    request.notes
                  ) : (
                    <span className="text-muted-foreground">
                      The salesperson left no notes.
                    </span>
                  )}
                </p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="lines" className="mt-0 space-y-4 focus-visible:outline-none">
          <Card>
            <CardHeader className="py-3 px-4">
              <CardTitle className="text-base">
                Lines ({request.lines.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              {request.lines.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No lines in this request.
                </p>
              ) : (() => {
                // Owner ruling after #948: Promotion / Selling price are real
                // columns (desktop) instead of a `bg-muted/20` sub-row -
                // `sellingMode` gates both the two extra headers and each
                // line row's two extra cells, and below the mobile
                // breakpoint they move into the stack under Name instead
                // (the two never render together, so nothing duplicates).
                const sellingMode = (request.price_mode ?? 'list') === 'selling';
                const showPriceColumns = sellingMode && !isMobile;
                return (
                <div className="overflow-x-auto">
                  {/* Line, then the parts it asked for, then the tags that get
                      printed for it (D3). One line is one row; a split line has
                      several tag rows under it, labelled 1a / 1b. */}
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="py-2 pr-3 font-medium">Type</th>
                        <th className="py-2 pr-3 font-medium">Code</th>
                        <th className="py-2 pr-3 font-medium">Name</th>
                        <th className="py-2 pr-3 font-medium text-right">Qty</th>
                        <th className="py-2 pr-3 font-medium text-right">
                          List Price
                        </th>
                        <th className="py-2 pr-3 font-medium text-right">
                          Sell Price
                        </th>
                        {showPriceColumns && (
                          <>
                            <th className="py-2 pr-3 font-medium">Promotion</th>
                            <th className="py-2 pr-3 font-medium text-right">
                              Selling price
                            </th>
                          </>
                        )}
                        <th className="py-2 pr-3 font-medium">Remarks</th>
                        <th className="py-2 pr-3 font-medium">Tag</th>
                        {(canDesign || changesByTag.size > 0) && (
                          <th className="py-2 font-medium text-right">Actions</th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {request.lines.map((line) => {
                        const showActions = canDesign || changesByTag.size > 0;
                        const columns = (showActions ? 9 : 8) + (showPriceColumns ? 2 : 0);
                        const tags = line.tags ?? [];
                        const parts = line.parts ?? [];
                        const lineChanged = tags.some((tag) =>
                          changesByTag.has(tag.id),
                        );
                        // D7: a line with exactly ONE tag and NO parts folds
                        // that tag's price / status / actions into the line
                        // row - no separate "1a" row underneath it. A line
                        // with parts, or two or more tags, keeps today's shape.
                        const foldedTag =
                          tags.length === 1 && parts.length === 0
                            ? tags[0]
                            : null;
                        // D5/S5: a line's own price basis, Selling mode only -
                        // List price alone needs no promotion pick (AC-S5-1).
                        // Editable only while the request is not terminal AND
                        // the viewer holds `dealer_kit.price_tag_requests.process`
                        // (AC-S5-3/S5-4); otherwise the same cells render read
                        // only. Shared between the desktop `<td>` columns and
                        // the mobile stack under Name - never both at once
                        // (`showPriceColumns`/`isMobile` pick exactly one).
                        const hasLinePricing =
                          line.line_type === 'product' && !!line.product_id;
                        // Owner polish after #948: the desktop `<td>` sits
                        // under a "Promotion"/"Selling price" header, so its
                        // cell holds only the value/control - the label
                        // stays only on the mobile stack, which has no
                        // header to read it off.
                        const renderPromotionCell = (withLabel: boolean) => (
                          <div className="min-w-[200px] flex-1 sm:max-w-[280px]">
                            <Label
                              htmlFor={`promotion-${line.id}`}
                              className={
                                withLabel
                                  ? 'text-2xs uppercase tracking-wide text-muted-foreground'
                                  : 'sr-only'
                              }
                            >
                              Promotion
                            </Label>
                            {canEditLinePrice ? (
                              <SearchableSelect
                                id={`promotion-${line.id}`}
                                clearable
                                truncateTriggerLabel
                                size="sm"
                                value={effectivePromotionId(line.id) ?? ''}
                                onChange={(value) => choosePromotion(line.id, value)}
                                options={(linePricing[line.id]?.promotion_options ?? []).map(
                                  (option) => ({
                                    value: option.id,
                                    label: `${promotionOptionName(option)} - RM ${option.sell_price.toLocaleString('en-MY')}`,
                                  }),
                                )}
                                placeholder="No covering promotion"
                                emptyMessage="No promotion covers this line."
                                disabled={savingLinePrice === line.id}
                              />
                            ) : (
                              <p className="text-sm font-medium">
                                {(() => {
                                  const promoId = effectivePromotionId(line.id);
                                  const option = linePricing[line.id]?.promotion_options.find(
                                    (o) => o.id === promoId,
                                  );
                                  return option ? promotionOptionName(option) : '-';
                                })()}
                              </p>
                            )}
                          </div>
                        );
                        const renderSellingPriceCell = (withLabel: boolean) => (
                          <div className="min-w-[140px]">
                            {withLabel && (
                              <Label className="text-2xs uppercase tracking-wide text-muted-foreground">
                                Selling price
                              </Label>
                            )}
                            {effectivePromotionId(line.id) ? (
                              <p className="text-sm font-medium whitespace-nowrap">
                                RM{' '}
                                {(
                                  linePricing[line.id]?.sell_price ?? 0
                                ).toLocaleString('en-MY')}
                              </p>
                            ) : canEditLinePrice ? (
                              <NumberInput
                                inputMode="decimal"
                                // `ManualSellPrice` is `gt=0` with 2 decimal
                                // places, so 0 is a refusal, not a bound.
                                min={0.01}
                                step={0.01}
                                variant="sm"
                                className="w-28"
                                value={
                                  manualPriceDraft[line.id] ??
                                  linePriceOverrides[line.id]?.manual_sell_price ??
                                  ''
                                }
                                onChange={(e) => setManualLinePrice(line.id, e.target.value)}
                                onBlur={() => commitManualLinePrice(line.id)}
                                placeholder="Type a price"
                                aria-label={`Selling price for ${line.code || line.name}`}
                                disabled={savingLinePrice === line.id}
                                error={manualPriceError[line.id]}
                              />
                            ) : (
                              <p className="text-sm font-medium whitespace-nowrap">
                                {linePriceOverrides[line.id]?.manual_sell_price != null
                                  ? `RM ${linePriceOverrides[line.id]!.manual_sell_price!.toLocaleString('en-MY')}`
                                  : '-'}
                              </p>
                            )}
                          </div>
                        );
                        return (
                          <Fragment key={line.id}>
                            <tr className="border-b last:border-b-0">
                              <td className="py-2 pr-3">
                                <Badge variant="secondary" className="text-xs">
                                  {line.line_type === 'product' ? 'Product' : 'Set'}
                                </Badge>
                              </td>
                              <td className="py-2 pr-3 font-mono text-xs">
                                {line.code}
                              </td>
                              <td className="py-2 pr-3">
                                {distinctName(line.code, line.name) ? (
                                  <span
                                    className="truncate block max-w-[200px]"
                                    title={line.name}
                                  >
                                    {line.name}
                                  </span>
                                ) : null}
                                {line.package_warning && (
                                  <span className="mt-1 flex flex-wrap items-center gap-1.5">
                                    <Badge
                                      variant="warning"
                                      appearance="light"
                                      size="sm"
                                    >
                                      Package warning
                                    </Badge>
                                    <span className="text-xs text-muted-foreground">
                                      {line.package_warning}
                                    </span>
                                  </span>
                                )}
                                {/* Rolled up from the tags below (r9 D18): the
                                    line says THAT something moved, the tag row
                                    says which one and offers the decision.
                                    Redundant on a folded row (D7) - that one
                                    tag's own Changed pill is on this same row. */}
                                {!foldedTag && lineChanged && (
                                  <span className="mt-1 flex">
                                    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-2xs font-semibold text-amber-800">
                                      Changed
                                    </span>
                                  </span>
                                )}
                                {/* AC-S1-11-equivalent: below the app's 992px
                                    mobile breakpoint (useIsMobile), the same
                                    Promotion/Selling price fields stack here
                                    instead of living in their own columns. */}
                                {isMobile && sellingMode && hasLinePricing && (
                                  <div
                                    data-testid="line-pricing-stack"
                                    className="mt-2 flex flex-wrap items-center gap-4 border-l-2 border-border pl-3"
                                  >
                                    {renderPromotionCell(true)}
                                    {renderSellingPriceCell(true)}
                                  </div>
                                )}
                              </td>
                              <td className="py-2 pr-3 text-right">{line.quantity}</td>
                              {foldedTag ? (
                                <>
                                  <td className="py-2 pr-3 text-right">
                                    {foldedTag.list_price != null
                                      ? `RM ${foldedTag.list_price.toFixed(2)}`
                                      : '-'}
                                  </td>
                                  <td className="py-2 pr-3 text-right">
                                    {line.show_promo_price &&
                                    foldedTag.sell_price != null ? (
                                      <span className="text-green-700 font-medium">
                                        RM {foldedTag.sell_price.toFixed(2)}
                                      </span>
                                    ) : (
                                      '-'
                                    )}
                                    {foldedTag.marketing_price_override != null && (
                                      <span className="block text-xs text-amber-600">
                                        Override: RM{' '}
                                        {foldedTag.marketing_price_override.toFixed(2)}
                                      </span>
                                    )}
                                  </td>
                                </>
                              ) : (
                                <>
                                  {/* Price is a TAG fact since D4 - the host plus
                                      that tag's own resolved parts - so an
                                      un-folded line leaves both money columns
                                      empty rather than repeating one tag's
                                      figure as if it were the line's. */}
                                  <td className="py-2 pr-3" />
                                  <td className="py-2 pr-3" />
                                </>
                              )}
                              {showPriceColumns && (
                                <>
                                  <td className="py-2 pr-3 align-top">
                                    {hasLinePricing ? renderPromotionCell(false) : null}
                                  </td>
                                  <td className="py-2 pr-3 text-right align-top">
                                    {hasLinePricing ? renderSellingPriceCell(false) : null}
                                  </td>
                                </>
                              )}
                              <td
                                className="py-2 pr-3 text-muted-foreground text-xs truncate max-w-[160px]"
                                title={line.remarks ?? undefined}
                              >
                                {line.remarks || '-'}
                              </td>
                              <td className="py-2 pr-3">
                                {foldedTag && (
                                  <div className="flex flex-wrap items-center gap-1.5">
                                    {designedTagIds.has(foldedTag.id) ? (
                                      <span className="text-xs text-emerald-700 font-medium">
                                        Designed
                                      </span>
                                    ) : (
                                      <span className="text-xs text-muted-foreground">
                                        No tag
                                      </span>
                                    )}
                                    {changesByTag.has(foldedTag.id) && (
                                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-2xs font-semibold text-amber-800">
                                        Changed
                                      </span>
                                    )}
                                  </div>
                                )}
                              </td>
                              {showActions && (
                                <td className="py-2 text-right">
                                  {foldedTag && (
                                    <div className="flex items-center justify-end gap-1">
                                      {changesByTag.has(foldedTag.id) && (
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          className="gap-1.5"
                                          onClick={() => setReviewTagId(foldedTag.id)}
                                          aria-label={`Review changes on ${line.code || line.name} ${foldedTag.label}`}
                                        >
                                          <RefreshCw className="size-3.5" />
                                          Review
                                        </Button>
                                      )}
                                      {canDesign && (
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          className="gap-1.5"
                                          onClick={() => openDesignerForTag(foldedTag.id)}
                                          aria-label={`Design tag ${foldedTag.label}`}
                                        >
                                          <Palette className="size-3.5" />
                                          Design
                                        </Button>
                                      )}
                                    </div>
                                  )}
                                </td>
                              )}
                            </tr>
                            {foldedTag ? null : (
                              <>
                            {/* What the salesperson asked to come with it (S2). */}
                            {parts.map((part) => (
                              <tr key={part.id} className="border-b last:border-b-0">
                                <td className="py-1.5 pr-3" />
                                <td
                                  className="py-1.5 pr-3 pl-4 font-mono text-xs text-muted-foreground"
                                  colSpan={columns - 1}
                                >
                                  {part.product_id
                                    ? `${codeWithName(part.code ?? '', part.name)}${part.role ? ` (${part.role})` : ''}`
                                    : `${part.role ?? 'Open'}: ${part.candidates
                                        .map((candidate) => candidate.code)
                                        .join(' / ')}`}
                                </td>
                              </tr>
                            ))}
                            {/* What actually prints (D3). */}
                            {tags.map((tag) => (
                              <tr key={tag.id} className="border-b last:border-b-0">
                                <td className="py-1.5 pr-3" />
                                <td className="py-1.5 pr-3 pl-4 font-mono text-xs">
                                  {tag.label}
                                </td>
                                <td className="py-1.5 pr-3 text-xs text-muted-foreground">
                                  {tag.open_groups.length > 0
                                    ? tag.open_groups
                                        .map(
                                          (group) =>
                                            `Open: ${group.role} (${group.candidates.length})`,
                                        )
                                        .join(', ')
                                    : tag.choices_display
                                        .map((choice) => choice.code)
                                        .join(', ') || '-'}
                                </td>
                                <td className="py-1.5 pr-3 text-right">{tag.quantity}</td>
                                <td className="py-1.5 pr-3 text-right">
                                  {tag.list_price != null
                                    ? `RM ${tag.list_price.toFixed(2)}`
                                    : '-'}
                                </td>
                                <td className="py-1.5 pr-3 text-right">
                                  {line.show_promo_price && tag.sell_price != null ? (
                                    <span className="text-green-700 font-medium">
                                      RM {tag.sell_price.toFixed(2)}
                                    </span>
                                  ) : (
                                    '-'
                                  )}
                                  {tag.marketing_price_override != null && (
                                    <span className="block text-xs text-amber-600">
                                      Override: RM{' '}
                                      {tag.marketing_price_override.toFixed(2)}
                                    </span>
                                  )}
                                </td>
                                {/* The line's own Promotion/Selling price
                                    columns sit on the LINE row above, not
                                    here - a tag row keeps them empty so it
                                    stays aligned under the same headers. */}
                                {showPriceColumns && (
                                  <>
                                    <td className="py-1.5 pr-3" />
                                    <td className="py-1.5 pr-3" />
                                  </>
                                )}
                                <td className="py-1.5 pr-3" />
                                <td className="py-1.5 pr-3">
                                  <div className="flex flex-wrap items-center gap-1.5">
                                    {designedTagIds.has(tag.id) ? (
                                      <span className="text-xs text-emerald-700 font-medium">
                                        Designed
                                      </span>
                                    ) : (
                                      <span className="text-xs text-muted-foreground">
                                        No tag
                                      </span>
                                    )}
                                    {changesByTag.has(tag.id) && (
                                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-2xs font-semibold text-amber-800">
                                        Changed
                                      </span>
                                    )}
                                  </div>
                                </td>
                                {showActions && (
                                  <td className="py-1.5 text-right">
                                    <div className="flex items-center justify-end gap-1">
                                      {changesByTag.has(tag.id) && (
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          className="gap-1.5"
                                          onClick={() => setReviewTagId(tag.id)}
                                          aria-label={`Review changes on ${line.code || line.name} ${tag.label}`}
                                        >
                                          <RefreshCw className="size-3.5" />
                                          Review
                                        </Button>
                                      )}
                                      {canDesign && (
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          className="gap-1.5"
                                          onClick={() => openDesignerForTag(tag.id)}
                                          aria-label={`Design tag ${tag.label}`}
                                        >
                                          <Palette className="size-3.5" />
                                          Design
                                        </Button>
                                      )}
                                    </div>
                                  </td>
                                )}
                              </tr>
                            ))}
                              </>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                );
              })()}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="attachments" className="mt-0 space-y-4 focus-visible:outline-none">
          {/* Sales order attachments - read-only: marketing views/downloads
              what the salesperson attached, upload stays portal-only (D4). */}
          <Card>
            <CardHeader className="py-3 px-4">
              <CardTitle className="text-base">Sales Order</CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-4">
              {attachments.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No sales order files attached.
                </p>
              ) : (
                <div className="space-y-1">
                  {attachments.map((att, idx) => (
                    <button
                      key={att.link_id}
                      type="button"
                      onClick={() => {
                        setPreviewIndex(idx);
                        setPreviewOpen(true);
                      }}
                      className="flex w-full items-center justify-between text-sm px-2 py-1.5 bg-muted rounded hover:bg-muted/70 transition-colors text-left"
                    >
                      <div className="flex items-center min-w-0">
                        <FileText className="size-4 mr-2 text-muted-foreground shrink-0" />
                        <span className="truncate" title={att.filename ?? undefined}>
                          {att.filename || 'Attachment'}
                        </span>
                      </div>
                      <span className="text-xs text-muted-foreground ml-2 shrink-0">
                        {att.uploaded_at ? formatDateTimeInMalaysia(att.uploaded_at) : '-'}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <AttachmentPreviewModal
        open={previewOpen}
        onOpenChange={setPreviewOpen}
        items={attachmentPreviewItems}
        startIndex={previewIndex}
      />

      <ProductDataReviewDialog
        open={reviewSet !== null}
        onOpenChange={(next) => {
          if (!next) setReviewTagId(null);
        }}
        changeSet={reviewSet}
        onDecide={(action) =>
          reviewTagId
            ? decideTagPin(reviewTagId, action)
            : Promise.resolve()
        }
      />

      {/* Edit request (r9 D7): the office fixing the print choice, in a modal
          the way every other edit in this app is. One field today; the rest of
          a request is the salesperson's to change through a revision. */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Edit request</DialogTitle>
            <DialogDescription>{request.doc_number}</DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label id="edit-print-by-label">Printing</Label>
            <PrintBySelect
              aria-labelledby="edit-print-by-label"
              value={(request.print_by as PrintBy | null) ?? null}
              disabled={busy}
              onChange={(next) => void handleSavePrintBy(next)}
              onClear={() => void handleSavePrintBy(null)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

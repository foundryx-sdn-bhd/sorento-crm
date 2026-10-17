'use client';

/**
 * Portal landing content - shared by the stable slug tree
 * (`/portal/c/{slug}`) and the legacy `/portal` tree (impersonation + old
 * links). All navigation goes through lib/portal-paths so links stay inside
 * the active tree.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  Copy,
  FileText,
  LogOut,
  MessageCircle,
  Plus,
  Star,
} from 'lucide-react';
import type { ColumnDef } from '@tanstack/react-table';
import { getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import type { ListBoardViewMode } from '@/hooks/useListBoardViewPreference';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/lib/toast';
import { formatCurrency } from '@/lib/helpers';
import {
  LANDING_KINDS,
  LANDING_LABELS,
  PortalContact,
  PortalLandingKind,
  PortalSubmissionSummary,
  PortalUnauthorizedError,
  clearPortalToken,
  fetchMeWithGrace,
  fetchSubmissions,
  isLandingKind,
  isSubmissionKind,
  portalLogout,
  readPortalToken,
  statusLabel,
} from '../lib/portal-client';
import { listRequestsAsSummaries } from '../lib/price-tag-request-service';
import { listSampleRequestsAsSummaries } from '../lib/sample-request-service';
import { listNewProductApplicationsAsSummaries } from '../lib/new-product-application-service';
import { listOpportunitiesAsSummaries } from '../lib/sales-opportunity-landing';
import { MyTargetPanel } from './MyTargetPanel';
import {
  complaintStatusLabel,
  complaintStatusPillClass,
} from '@/lib/complaint-status';
import { revisionBadgeLabel } from '@/lib/document-number';
import {
  portalDetailPath,
  portalDuplicatePath,
  portalNewPath,
  portalRevisePath,
  portalVerifyPath,
  waMeUrl,
} from '../lib/portal-paths';
import { useRevisionPolicy } from '../hooks/useRevisions';
import { ReviseAction } from './ReviseAction';
import { LandingCardShell } from './LandingCardShell';
import { LandingToolbar } from './LandingToolbar';
import { CustomerAsksList } from './CustomerAsksList';
import { listCustomerAsks } from '../lib/customer-asks-service';
import { ConversationList } from './ConversationList';
import { listConversations } from '../lib/conversations-service';
import {
  DEFAULT_LANDING_SORT,
  activeLandingFilterCount,
  applyLandingFilters,
  landingFieldValue,
  landingFieldsFor,
  sortLandingItems,
  submissionStatusLabel,
  type LandingField,
  type LandingFilters,
  type LandingSort,
} from '../lib/landing-fields';

// Shared across every kind, so the choice survives a type switch (AC-L7).
const PORTAL_VIEW_KEY = 'sorento.portalView';

// AC-L3: a contact whose overrides hide every kind gets a WhatsApp escape
// hatch rather than a blank landing.
const WA_NO_FORMS_TEXT = 'Hi, I would like to submit a form.';

/**
 * The dropdown's option list for THIS contact: every landing kind the
 * server's `visible_form_types` grants, in `LANDING_KINDS` order
 * (PLAN-portal-forms-market-segment D2/AC-L1). All five kinds are gated the
 * same way now - none is offered or fetched unconditionally. The server
 * enforces the same rule on every route regardless, so this only decides
 * what is offered, never what is allowed.
 */
function landingKindsFor(contact: PortalContact | null): PortalLandingKind[] {
  const visible = contact?.visible_form_types ?? [];
  // Stock Inquiry stays first when visible - the dealers' main form, and the
  // one deliberate exception to LANDING_KINDS' own order (a pre-existing
  // choice this lane keeps, not a new one). Everything else follows in
  // LANDING_KINDS order.
  const rest = LANDING_KINDS.filter((k) => k !== 'stock_inquiry' && visible.includes(k));
  return visible.includes('stock_inquiry') ? ['stock_inquiry', ...rest] : rest;
}

const EMPTY_LISTS: Record<PortalLandingKind, PortalSubmissionSummary[]> = {
  complaint: [],
  stock_inquiry: [],
  purchase_request: [],
  sponsorship_form: [],
  price_tag_request: [],
  sample_request: [],
  new_product_application: [],
  sales_opportunity: [],
  customer_asks: [],
  conversation: [],
};

type BadgeVariant =
  | 'primary'
  | 'secondary'
  | 'destructive'
  | 'success'
  | 'warning'
  | 'info';

// Status → badge colour. draft is intentionally neutral (no colour) so it
// reads as "not yet meaningful". The other states map to a unique colour so
// users can scan a list without reading labels.
function statusVariant(row: PortalSubmissionSummary): BadgeVariant {
  if (row.is_draft) return 'secondary';
  const s = (row.status || '').toLowerCase();
  if (s === 'rejected' || s === 'cancelled' || s === 'lost') return 'destructive';
  if (
    s === 'won' ||
    s === 'approved' ||
    s === 'completed' ||
    s === 'fulfilled' ||
    s === 'settled_on_site' ||
    s === 'closed'
  ) {
    return 'success';
  }
  if (s === 'responded' || s === 'replied') return 'info';
  return 'primary';
}

// Tailwind classes that tint the card background + border by status. Kept
// subtle so text stays legible.
function statusCardClass(row: PortalSubmissionSummary): string {
  if (row.is_draft) {
    return 'bg-card border-border';
  }
  const s = (row.status || '').toLowerCase();
  if (s === 'rejected' || s === 'cancelled' || s === 'lost') {
    return 'bg-destructive/5 border-destructive/40';
  }
  if (
    s === 'won' ||
    s === 'approved' ||
    s === 'completed' ||
    s === 'fulfilled' ||
    s === 'settled_on_site' ||
    s === 'closed'
  ) {
    return 'bg-success/5 border-success/40';
  }
  if (s === 'responded' || s === 'replied') {
    return 'bg-violet-50 border-violet-200 dark:bg-violet-950/30 dark:border-violet-900';
  }
  return 'bg-primary/5 border-primary/30';
}

// Per-kind primary/secondary metadata picked for the compact card layout.
function pickCardMeta(row: PortalSubmissionSummary): {
  product?: string;
  project?: string;
  customer?: string;
} {
  if (row.kind === 'complaint') {
    return {
      product: row.product_code ?? undefined,
      project: row.project_title ?? undefined,
      customer: row.customer_name ?? undefined,
    };
  }
  if (row.kind === 'stock_inquiry') {
    return {
      product: row.product_code ?? undefined,
      project: row.project_name ?? undefined,
      customer: row.project_customer ?? undefined,
    };
  }
  if (
    row.kind === 'price_tag_request' ||
    row.kind === 'sales_opportunity' ||
    row.kind === 'sample_request' ||
    row.kind === 'new_product_application'
  ) {
    return { customer: row.customer_name ?? undefined };
  }
  return {
    project: row.project_title ?? row.sponsor_subject ?? undefined,
    customer: row.customer_name ?? undefined,
  };
}

export function PortalLanding({ slug }: { slug?: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [contact, setContact] = useState<PortalContact | null>(null);
  const [submissions, setSubmissions] =
    useState<Record<PortalLandingKind, PortalSubmissionSummary[]>>(EMPTY_LISTS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openAskCount, setOpenAskCount] = useState(0);
  const [conversationCount, setConversationCount] = useState(0);
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debouncedSearch,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const initialTabFromUrl = (() => {
    const t = searchParams?.get('type');
    return isLandingKind(t) ? t : null;
  })();
  // N1: null, not a hardcoded kind - a kind picked before `visible_form_types`
  // is known could be one this contact cannot see, and the fallback effect
  // below corrects it the moment `landingKinds` resolves. Seeding a value
  // absent from `options` for that one frame is worse than seeding none.
  const [activeTab, setActiveTab] =
    useState<PortalLandingKind | null>(initialTabFromUrl);
  // Filter + sort are component state that resets whenever the type changes
  // (D-L4) - the field set differs per kind, so a status or field value
  // picked for one kind has no business surviving a tab switch. The view
  // choice (cards vs list) is the one thing that persists, per device,
  // across both a reload and a type switch (AC-L7).
  const [filters, setFilters] = useState<LandingFilters>({});
  const [sort, setSort] = useState<LandingSort>(DEFAULT_LANDING_SORT);
  useEffect(() => {
    setFilters({});
    setSort(DEFAULT_LANDING_SORT);
  }, [activeTab]);
  // Cards is the default at EVERY width (R3-2, AC-R9) - the old viewport-based
  // default (list on `md:` and up) is gone. A stored choice still wins.
  const [view, setViewState] = useState<ListBoardViewMode>('board');
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const stored = window.localStorage.getItem(PORTAL_VIEW_KEY);
    if (stored === 'list' || stored === 'board') {
      setViewState(stored);
    }
  }, []);
  const setView = useCallback((mode: ListBoardViewMode) => {
    setViewState(mode);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(PORTAL_VIEW_KEY, mode);
    }
  }, []);
  const userPickedTabRef = useRef<boolean>(Boolean(searchParams?.get('type')));
  // Mirror current URL `?type=` so loadAll's expired-token redirect can read it
  // without depending on `searchParams` (which would re-create loadAll and
  // break the debounced search).
  const typeQueryRef = useRef<string | null>(searchParams?.get('type') ?? null);
  useEffect(() => {
    typeQueryRef.current = searchParams?.get('type') ?? null;
  }, [searchParams]);

  const landingKinds = useMemo(() => landingKindsFor(contact), [contact]);

  // A `?type=` deep link, or a starred default, can name a kind this contact
  // does not hold - a link forwarded by a colleague, or a grant since
  // withdrawn. Fall back to the first visible kind rather than showing an
  // option whose list the server would refuse (AC-L2). An empty visible set
  // leaves nothing to fall back to - the empty state renders instead.
  useEffect(() => {
    if (!contact) return;
    if (landingKinds.length === 0) return;
    if (activeTab && landingKinds.includes(activeTab)) return;
    setActiveTab(landingKinds[0]);
  }, [contact, landingKinds, activeTab]);

  // Once the contact is known (and the URL has no `?type=` deep-link, and the
  // user hasn't manually switched tabs in this session), apply any default
  // tab the contact previously starred.
  useEffect(() => {
    if (!contact?.contact_id) return;
    if (userPickedTabRef.current) return;
    if (searchParams?.get('type')) return;
    if (typeof window === 'undefined') return;
    const stored = window.localStorage.getItem(
      `sorento.portalDefaultTab.${contact.contact_id}`,
    );
    if (isLandingKind(stored) && landingKinds.includes(stored)) {
      setActiveTab(stored);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contact?.contact_id, landingKinds]);

  const handleTabChange = useCallback((next: PortalLandingKind) => {
    userPickedTabRef.current = true;
    setActiveTab(next);
  }, []);

  const defaultTabKey = useMemo(() => {
    if (!contact?.contact_id) return null;
    return `sorento.portalDefaultTab.${contact.contact_id}`;
  }, [contact?.contact_id]);

  const [savedDefaultTab, setSavedDefaultTab] =
    useState<PortalLandingKind | null>(null);
  useEffect(() => {
    if (!defaultTabKey || typeof window === 'undefined') {
      setSavedDefaultTab(null);
      return;
    }
    const stored = window.localStorage.getItem(defaultTabKey);
    setSavedDefaultTab(isLandingKind(stored) ? stored : null);
  }, [defaultTabKey]);

  const handleSetDefaultTab = useCallback(() => {
    if (!activeTab || !defaultTabKey || typeof window === 'undefined') return;
    window.localStorage.setItem(defaultTabKey, activeTab);
    setSavedDefaultTab(activeTab);
    toast.success(`${LANDING_LABELS[activeTab]} is now your default tab.`);
  }, [activeTab, defaultTabKey]);

  // Track whether the very first load has finished. Subsequent search
  // refetches must NOT flip `loading` back to true, otherwise the skeleton
  // remounts and the search Input loses focus on every keystroke.
  const initialLoadDone = useRef(false);

  // Keep tab in sync if the user navigates back with a different ?type=.
  useEffect(() => {
    const t = searchParams?.get('type');
    // `landingKinds.includes` too (the same guard the default-tab effect above uses):
    // `isLandingKind` only proves `t` is a valid tab kind at all (reviewer should-fix 9,
    // Phase 3: it does NOT match `sales_opportunity`, which has no tab here to begin
    // with) - `landingKinds` is the narrower, per-contact set this contact was actually
    // granted, which a `?type=` query param can name without ever having been offered.
    if (isLandingKind(t) && landingKinds.includes(t) && t !== activeTab) setActiveTab(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const loadAll = useCallback(
    async (q?: string) => {
      const existing = readPortalToken();
      if (!existing) {
        router.replace(portalVerifyPath({ slug: slug ?? null }));
        return;
      }
      const isInitial = !initialLoadDone.current;
      if (isInitial) setLoading(true);
      try {
        // fetchMeWithGrace absorbs the post-verify commit-visibility race
        // (fresh token transiently 401s) before bouncing back to verify.
        const me = await fetchMeWithGrace();
        setContact(me);
        // Every kind is gated the same way now (AC-L1): only ask for a list
        // the resolved `visible_form_types` actually grants, in canonical
        // order. Each kind answers its own endpoint in its own shape, so a
        // gated form beyond the four legacy ones brings its own adapter.
        const kinds = landingKindsFor(me);
        // allSettled, not all: the legs are independent lists and one of them
        // answering 403 or 500 used to reject the whole load, so the landing
        // showed its error screen and the rest of the kinds that answered
        // perfectly well were unreachable. A leg that fails is that kind empty.
        // Customer asks (fix round 5) is not a list of submissions: its body pages its own
        // rows (`CustomerAsksList`), so this leg only reads the open count for the badge.
        const legs = await Promise.allSettled(
          kinds.map((k) =>
            k === 'price_tag_request'
              ? listRequestsAsSummaries(q)
              : k === 'sample_request'
                ? listSampleRequestsAsSummaries(q)
                : k === 'new_product_application'
                  ? listNewProductApplicationsAsSummaries(q)
                  : k === 'sales_opportunity'
                ? listOpportunitiesAsSummaries(q)
                : k === 'customer_asks'
                  ? listCustomerAsks({ page: 1, limit: 1, state: 'open', q }).then((page) => {
                      setOpenAskCount(page.pagination?.total ?? 0);
                      return [] as PortalSubmissionSummary[];
                    })
                  : k === 'conversation'
                    // Same shape as Customer asks: the body (`ConversationList`) reads its own
                    // rows, this leg only reads the count for the picker badge.
                    ? listConversations({ page: 1, limit: 1, q }).then((page) => {
                        setConversationCount(page.pagination?.total ?? 0);
                        return [] as PortalSubmissionSummary[];
                      })
                    : fetchSubmissions(k, q),
          ),
        );

        // An expired token is not one kind failing - every leg would fail and
        // the answer is to re-verify - so it is rethrown to the handler below.
        const dead = legs.find(
          (leg) =>
            leg.status === 'rejected' &&
            leg.reason instanceof PortalUnauthorizedError,
        );
        if (dead && dead.status === 'rejected') throw dead.reason;

        // N4: derived from the canonical list rather than spreading the
        // module-level EMPTY_LISTS - a kind LANDING_KINDS gains cannot be
        // forgotten here the way a manually-enumerated copy could be.
        const next = Object.fromEntries(
          LANDING_KINDS.map((k) => [k, [] as PortalSubmissionSummary[]]),
        ) as Record<PortalLandingKind, PortalSubmissionSummary[]>;
        kinds.forEach((k, index) => {
          const leg = legs[index];
          if (leg.status === 'fulfilled') {
            next[k] = leg.value;
          } else {
            console.warn('Portal landing: one list failed to load', leg.reason);
          }
        });
        setSubmissions(next);
        setError(null);
        // Token validated - clear the freshness stamp so subsequent transient
        // 401s (e.g. real expiry) bounce back immediately without retry.
        if (typeof window !== 'undefined') {
          window.sessionStorage.removeItem('sorento.portalTokenWrittenAt');
        }
      } catch (e) {
        if (e instanceof PortalUnauthorizedError) {
          // Token expired/revoked. The slug tree recovers identity from the
          // slug itself; the legacy tree forwards the dead token so
          // /portal/verify can look up contact/space via /token-info.
          router.replace(
            portalVerifyPath({
              slug: slug ?? null,
              reason: 'expired',
              token: existing,
              type: typeQueryRef.current,
            }),
          );
          return;
        }
        setError(e instanceof Error ? e.message : 'Failed to load portal.');
      } finally {
        if (isInitial) {
          setLoading(false);
          initialLoadDone.current = true;
        }
      }
    },
    [router, slug],
  );

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // Debounced refetch when the user types in the search box (M6-06:
  // useDebouncedSearch, the shared 200ms standard); backend now does the
  // field-spanning search so the result reflects every column (product,
  // metadata, doc number, etc.).
  useEffect(() => {
    void loadAll(debouncedSearch);
  }, [debouncedSearch, loadAll]);

  const totals = useMemo(() => {
    const out: Record<PortalLandingKind, number> = {
      complaint: 0,
      stock_inquiry: 0,
      purchase_request: 0,
      sponsorship_form: 0,
      price_tag_request: 0,
      sample_request: 0,
      new_product_application: 0,
      sales_opportunity: 0,
      customer_asks: 0,
      conversation: 0,
    };
    for (const t of landingKinds) out[t] = submissions[t]?.length ?? 0;
    // Its badge is the open count, not a row count (asks are paged server-side).
    out.customer_asks = openAskCount;
    out.conversation = conversationCount;
    return out;
  }, [submissions, landingKinds, openAskCount, conversationCount]);

  const handleLogout = useCallback(async () => {
    const t = readPortalToken();
    // Revoke server-side first (best-effort) - clearing storage alone would
    // leave a copied token valid until expiry.
    try {
      await portalLogout();
    } catch {
      // best-effort
    }
    clearPortalToken();
    const qs = !slug && t ? t : null;
    router.replace(
      portalVerifyPath({ slug: slug ?? null, reason: 'logout', token: qs }),
    );
  }, [router, slug]);

  if (loading) {
    return (
      <div className="w-full max-w-3xl mx-auto px-3 pt-4 pb-4 space-y-3">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full max-w-3xl mx-auto px-3 pt-4 pb-4 space-y-3">
        <Alert variant="destructive">
          <AlertIcon>
            <AlertCircle />
          </AlertIcon>
          <AlertTitle>{error}</AlertTitle>
        </Alert>
        <Button
          variant="outline"
          onClick={() =>
            router.replace(portalVerifyPath({ slug: slug ?? null }))
          }
        >
          Verify with OTP
        </Button>
      </div>
    );
  }

  // The tab actually rendered below: `activeTab` can be null for the one
  // frame before the fallback effect above corrects it (N1); `landingKinds[0]`
  // is always defined once this render reaches the non-empty branch, so the
  // final `?? 'complaint'` is a type satisfier only, never actually reached.
  const currentTab: PortalLandingKind = activeTab ?? landingKinds[0] ?? 'complaint';

  return (
    <div className="w-full max-w-3xl mx-auto px-3 pt-3 pb-4 space-y-3">
      {/* Header - Welcome centered, Log out anchored to top-right. */}
      <div className="relative flex items-center justify-center min-h-[2.75rem]">
        <h1 className="text-lg font-semibold text-center break-words px-12">
          Welcome{contact?.name ? `, ${contact.name}` : ''}
        </h1>
        <Button
          variant="outline"
          size="sm"
          onClick={handleLogout}
          className="absolute right-0 top-1/2 -translate-y-1/2 h-9 px-2.5"
          aria-label="Log out"
          title="Log out"
        >
          <LogOut className="h-4 w-4" />
        </Button>
      </div>

      {landingKinds.length === 0 ? (
        // AC-L3/AC-L5: no picker, toolbar, list or search box - just the one
        // next step this contact has left. Same shape as the per-kind empty
        // card below (icon, py-8).
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground space-y-2">
            <MessageCircle className="h-8 w-8 mx-auto" />
            <p>No forms are available for your account.</p>
            {contact?.whatsapp_number ? (
              <Button asChild>
                <a
                  href={waMeUrl(contact.whatsapp_number, WA_NO_FORMS_TEXT)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <MessageCircle className="h-4 w-4 mr-2" />
                  Chat with us on WhatsApp
                </a>
              </Button>
            ) : (
              // No WhatsApp number on file - Log out (already the header's
              // own action) is the only next step left to offer, rather than
              // nothing.
              <Button variant="outline" onClick={handleLogout}>
                <LogOut className="h-4 w-4 mr-2" />
                Log out
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Search input only (D-L3) - the status filter now lives in the
              toolbar's Filter popover, alongside every other filterable field. */}
          <ListSearchInput
            value={search}
            onChange={setSearch}
            isSettling={searchSettling}
            placeholder="Search..."
            aria-label="Search submissions"
            className="w-full"
            inputClassName="h-12 text-base"
          />

          <div className="flex items-stretch gap-2">
            <SearchableSelect
              value={currentTab}
              onChange={(v) => handleTabChange(v as PortalLandingKind)}
              options={landingKinds.map((t) => ({
                value: t,
                label: LANDING_LABELS[t],
              }))}
              size="lg"
              triggerClassName="flex-1 min-h-12 text-base"
              renderTriggerLabel={(opt) => {
                const t = opt.value as PortalLandingKind;
                return (
                  <span className="flex items-center gap-2">
                    {LANDING_LABELS[t]}
                    <Badge variant="secondary" className="px-1.5 py-0 text-xs">
                      {totals[t]}
                    </Badge>
                    {savedDefaultTab === t && (
                      <Star
                        className="h-3.5 w-3.5 fill-yellow-400 text-yellow-500 shrink-0"
                        aria-label="default"
                      />
                    )}
                  </span>
                );
              }}
              renderOption={(opt) => {
                const t = opt.value as PortalLandingKind;
                return (
                  <span className="flex items-center gap-2">
                    {LANDING_LABELS[t]}
                    <Badge variant="secondary" className="px-1.5 py-0 text-xs">
                      {totals[t]}
                    </Badge>
                    {savedDefaultTab === t && (
                      <Star
                        className="h-3.5 w-3.5 fill-yellow-400 text-yellow-500 shrink-0"
                        aria-label="default"
                      />
                    )}
                  </span>
                );
              }}
            />
            <Button
              type="button"
              variant="outline"
              onClick={handleSetDefaultTab}
              disabled={!contact || savedDefaultTab === currentTab}
              aria-label={
                savedDefaultTab === currentTab
                  ? `${LANDING_LABELS[currentTab]} is your default tab`
                  : `Set ${LANDING_LABELS[currentTab]} as default tab`
              }
              title={
                savedDefaultTab === currentTab
                  ? 'Default tab'
                  : 'Set as default for this contact'
              }
              className="h-12 px-3"
            >
              <Star
                className={`h-4 w-4 ${
                  savedDefaultTab === currentTab
                    ? 'fill-yellow-400 text-yellow-500'
                    : ''
                }`}
              />
            </Button>
          </div>

          {/* F2: the logging agent's own target progress, at the top of the kind. */}
          {currentTab === 'sales_opportunity' ? <MyTargetPanel slug={slug} /> : null}

          {currentTab === 'customer_asks' ? (
            <CustomerAsksList
              search={debouncedSearch}
              contactId={contact?.contact_id}
              view={view}
              onViewChange={setView}
            />
          ) : currentTab === 'conversation' ? (
            <ConversationList
              search={debouncedSearch}
              contactId={contact?.contact_id}
              view={view}
              onViewChange={setView}
            />
          ) : (
          <SubmissionList
            kind={currentTab}
            items={submissions[currentTab] ?? []}
            filters={filters}
            onFiltersChange={setFilters}
            sort={sort}
            onSortChange={setSort}
            view={view}
            onViewChange={setView}
            slug={slug}
            search={search}
            onClearSearch={() => setSearch('')}
          />
          )}
        </>
      )}
    </div>
  );
}

function SubmissionList({
  kind,
  items,
  filters,
  onFiltersChange,
  sort,
  onSortChange,
  view,
  onViewChange,
  slug,
  search,
  onClearSearch,
}: {
  kind: PortalLandingKind;
  items: PortalSubmissionSummary[];
  filters: LandingFilters;
  onFiltersChange: (next: LandingFilters) => void;
  sort: LandingSort;
  onSortChange: (next: LandingSort) => void;
  view: ListBoardViewMode;
  onViewChange: (mode: ListBoardViewMode) => void;
  slug?: string;
  /** A search term is answered server-side (`fetchSubmissions(kind, q)`),
   *  so a search-only zero result already arrives as `items.length === 0`
   *  with no client-side `filters` set at all (review round 2, AC-L8) - the
   *  empty state has to know about it too, not just `filters`. */
  search?: string;
  onClearSearch?: () => void;
}) {
  const fields = useMemo(() => landingFieldsFor(kind), [kind]);
  const filtered = useMemo(
    () => sortLandingItems(applyLandingFilters(items, fields, filters), fields, sort),
    [items, fields, filters, sort],
  );

  const [previewRow, setPreviewRow] = useState<PortalSubmissionSummary | null>(
    null,
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <LandingToolbar
          fields={fields}
          items={items}
          filters={filters}
          onFiltersChange={onFiltersChange}
          sort={sort}
          onSortChange={onSortChange}
          view={view}
          onViewChange={onViewChange}
        />
        <Button
          asChild
          size="sm"
          className="shrink-0"
          title={`New ${LANDING_LABELS[kind]}`}
          aria-label={`New ${LANDING_LABELS[kind]}`}
        >
          <Link href={portalNewPath(kind, slug)}>
            <Plus className="shrink-0" />
            {/* Review round 2/3: "New Price Tag Request" (the longest label)
                pushed the toolbar onto two rows at 375px - below `sm` the
                clip hides everything past "New" (no ellipsis, so it never
                shows a ragged fragment of the kind name), the full label at
                `sm` and up. One text node, not two - a wrapping element
                around a bare "New" here reads as a second, stray "New" next
                to a submission card's own status badge of the same word
                (`PortalLanding.priceTag.test.tsx`). */}
            <span className="block max-w-[31px] overflow-hidden whitespace-nowrap sm:max-w-none sm:inline">
              New {LANDING_LABELS[kind]}
            </span>
          </Link>
        </Button>
      </div>
      {filtered.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground space-y-2">
            <FileText className="h-8 w-8 mx-auto" />
            {items.length === 0 &&
            !(search ?? '').trim() &&
            activeLandingFilterCount(filters) === 0 ? (
              <p>No {LANDING_LABELS[kind].toLowerCase()} submissions yet.</p>
            ) : (
              <>
                <p>No submissions match your filters.</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    onFiltersChange({});
                    onClearSearch?.();
                  }}
                >
                  Clear filters
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      ) : view === 'list' ? (
        <SubmissionTable
          kind={kind}
          items={filtered}
          fields={fields}
          sort={sort}
          onSortChange={onSortChange}
          slug={slug}
        />
      ) : (
        <ul className="space-y-2.5">
          {filtered.map((row) => (
            <li key={row.id}>
              <SubmissionCard
                row={row}
                kind={kind}
                slug={slug}
                onLongPress={() => setPreviewRow(row)}
              />
            </li>
          ))}
        </ul>
      )}

      <SubmissionPreviewDialog
        row={previewRow}
        kind={kind}
        slug={slug}
        onOpenChange={(open) => !open && setPreviewRow(null)}
      />
    </div>
  );
}

/**
 * Click / long-press / keyboard wiring shared by SubmissionCard and
 * SubmissionRow (D-L5) - both open the detail page on a tap/click/Enter and
 * the preview dialog on a long-press, right-click or context-menu key.
 */
function useSubmissionPress({
  kind,
  id,
  slug,
  onLongPress,
}: {
  kind: PortalLandingKind;
  id: string;
  slug?: string;
  onLongPress: () => void;
}) {
  const router = useRouter();
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const longPressFired = useRef(false);

  const startPress = () => {
    longPressFired.current = false;
    longPressTimer.current = setTimeout(() => {
      longPressFired.current = true;
      onLongPress();
    }, 450);
  };
  const clearPress = () => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  };

  return {
    role: 'link' as const,
    tabIndex: 0,
    onClick: () => {
      if (longPressFired.current) {
        longPressFired.current = false;
        return;
      }
      router.push(portalDetailPath(kind, id, slug));
    },
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        router.push(portalDetailPath(kind, id, slug));
      }
    },
    onContextMenu: (e: React.MouseEvent) => {
      e.preventDefault();
      onLongPress();
    },
    onTouchStart: startPress,
    onTouchEnd: clearPress,
    onTouchMove: clearPress,
    onTouchCancel: clearPress,
    onMouseDown: startPress,
    onMouseUp: clearPress,
    onMouseLeave: clearPress,
  };
}

/**
 * List view (R3-2, AC-R9/AC-R10): the repo DataGrid, one column per field of
 * the current kind, rather than the old `<ul>` of `SubmissionRow`. Header
 * click drives the SAME sort state the toolbar's Sort dropdown uses (R3-3's
 * rule: the active field flips direction, a different field picks its type's
 * natural default) - `SortableHeader` duplicates that small rule rather than
 * sharing it with `LandingToolbar`, since the two live in different DOM
 * shapes (menu item vs. column header).
 */
function SortableHeader({
  field,
  sort,
  onSortChange,
}: {
  field: LandingField;
  sort: LandingSort;
  onSortChange: (next: LandingSort) => void;
}) {
  const isActive = sort.key === field.key;
  return (
    <button
      type="button"
      className="flex items-center gap-1 text-left font-medium"
      onClick={(e) => {
        e.stopPropagation();
        onSortChange({
          key: field.key,
          dir: isActive
            ? sort.dir === 'asc'
              ? 'desc'
              : 'asc'
            : field.type === 'date'
              ? 'desc'
              : 'asc',
        });
      }}
    >
      <span>{field.label}</span>
      {isActive &&
        (sort.dir === 'asc' ? (
          <ArrowUp className="size-3.5 text-muted-foreground" />
        ) : (
          <ArrowDown className="size-3.5 text-muted-foreground" />
        ))}
    </button>
  );
}

function submissionTableColumns(
  fields: LandingField[],
  sort: LandingSort,
  onSortChange: (next: LandingSort) => void,
): ColumnDef<PortalSubmissionSummary>[] {
  // Named, not an anonymous arrow returned from `header()` (react/display-name):
  // a ColumnDef.header renders as its own component, and an unnamed one
  // trips the lint rule even though it never gets its own devtools entry.
  const header = (field: LandingField) => {
    function SortableColumnHeader() {
      return (
        <SortableHeader field={field} sort={sort} onSortChange={onSortChange} />
      );
    }
    return SortableColumnHeader;
  };
  const documentField = fields.find((f) => f.key === 'document_number') ?? {
    key: 'document_number',
    label: 'Form Number',
    type: 'text' as const,
  };
  const statusField = fields.find((f) => f.key === 'status') ?? {
    key: 'status',
    label: 'Status',
    type: 'status' as const,
  };
  const createdField = fields.find((f) => f.key === 'created_at') ?? {
    key: 'created_at',
    label: 'Created',
    type: 'date' as const,
  };
  // Every kind's card carries these three as COMMON_FIELDS; everything else
  // in `fields` is the kind's own set. `needed_by_date` (price_tag_request
  // only) is a date, so it never lands here - it gets its own "Need by"
  // column below, same as the card's dedicated "Need by" row.
  const textKindFields = fields.filter(
    (f) =>
      f.type === 'text' &&
      f.key !== 'document_number' &&
      f.key !== 'status',
  );
  // Review round 3: the column only exists for a kind that actually carries
  // the field (today, price_tag_request only) - a synthetic descriptor for
  // every other kind gave every list a permanently blank "Need by" column.
  const needByField = fields.find((f) => f.key === 'needed_by_date');

  const dateCell = (value: string | null | undefined) =>
    value ? (
      new Date(value).toLocaleDateString()
    ) : (
      <span className="text-muted-foreground">-</span>
    );

  const columns: ColumnDef<PortalSubmissionSummary>[] = [
    {
      accessorKey: 'document_number',
      header: header(documentField),
      cell: ({ row }) => {
        const value = row.original.document_number ?? row.original.title ?? '-';
        return (
          <span className="block truncate" title={value}>
            {value}
          </span>
        );
      },
      size: 170,
      minSize: 100,
    },
    {
      accessorKey: 'status',
      header: header(statusField),
      cell: ({ row }) => {
        const text = submissionStatusLabel(row.original);
        return (
          <Badge
            variant={statusVariant(row.original)}
            className="truncate"
            title={text}
          >
            {text}
          </Badge>
        );
      },
      size: 120,
      minSize: 90,
    },
    ...textKindFields.map(
      (field): ColumnDef<PortalSubmissionSummary> => ({
        accessorKey: field.key,
        header: header(field),
        cell: ({ row }) => {
          const value = landingFieldValue(row.original, field) ?? '-';
          return (
            <span className="block truncate" title={value}>
              {value}
            </span>
          );
        },
        size: 150,
        minSize: 100,
      }),
    ),
    ...(needByField
      ? [
          {
            accessorKey: 'needed_by_date',
            header: header(needByField),
            cell: ({ row }) => dateCell(row.original.needed_by_date),
            size: 110,
            minSize: 90,
          } satisfies ColumnDef<PortalSubmissionSummary>,
        ]
      : []),
    {
      accessorKey: 'created_at',
      header: header(createdField),
      cell: ({ row }) => dateCell(row.original.created_at),
      size: 110,
      minSize: 90,
    },
  ];
  return columns;
}

function SubmissionTable({
  kind,
  items,
  fields,
  sort,
  onSortChange,
  slug,
}: {
  kind: PortalLandingKind;
  items: PortalSubmissionSummary[];
  fields: LandingField[];
  sort: LandingSort;
  onSortChange: (next: LandingSort) => void;
  slug?: string;
}) {
  const columns = useMemo(
    () => submissionTableColumns(fields, sort, onSortChange),
    [fields, sort, onSortChange],
  );
  // `items` arrives already filtered + sorted (`sortLandingItems`, driven by
  // the same `sort` state the column headers write to) - the table just
  // displays it in that order rather than re-sorting client-side itself.
  const table = useReactTable({
    columns,
    data: items,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  return (
    <DataGrid
      table={table}
      recordCount={items.length}
      rowHref={(row) => portalDetailPath(kind, row.id, slug)}
      // Columns are DATA (they change with the kind), never a fixed set to
      // remember per user - same rationale `McpToolsList` documents for its
      // own `listingKey={null}`.
      listingKey={null}
      tableLayout={{
        width: 'fixed',
        columnsResizable: true,
        columnsDraggable: false,
        columnsMovable: false,
        columnsVisibility: false,
      }}
    >
      <DataGridTable />
    </DataGrid>
  );
}

function SubmissionCard({
  row,
  kind,
  slug,
  onLongPress,
}: {
  row: PortalSubmissionSummary;
  kind: PortalLandingKind;
  slug?: string;
  onLongPress: () => void;
}) {
  const press = useSubmissionPress({ kind, id: row.id, slug, onLongPress });
  const meta = pickCardMeta(row);
  const primary = row.document_number ?? row.title ?? '-';
  const tintClass = statusCardClass(row);

  // Complaints use the shared status map so portal labels + colours tally with
  // the internal system view; other kinds keep the generic badge variant.
  const isComplaint = row.kind === 'complaint';
  const statusText = submissionStatusLabel(row);

  return (
    <LandingCardShell {...press} tintClass={tintClass}>
      {/* Status badge anchored top-right; allows multi-word status to wrap
          onto two lines without colliding with the primary text. */}
      {isComplaint && !row.is_draft ? (
        <span
          className={`absolute top-2 right-2 max-w-[45%] inline-flex items-center justify-end rounded-md px-2 py-0.5 text-xs font-semibold whitespace-normal text-right leading-tight ${complaintStatusPillClass(row.status)}`}
        >
          {statusText}
        </span>
      ) : (
        <Badge
          variant={statusVariant(row)}
          className="absolute top-2 right-2 max-w-[45%] whitespace-normal text-right leading-tight justify-end"
        >
          {statusText}
        </Badge>
      )}
      <div className="space-y-1 pr-[45%]">
        <div className="flex flex-wrap items-center gap-1.5">
          <p className="text-base font-semibold break-words" title={primary}>
            {primary}
          </p>
          {revisionBadgeLabel(row.revision_no) && (
            <Badge variant="secondary">
              {revisionBadgeLabel(row.revision_no)}
            </Badge>
          )}
          {row.has_revision_draft && (
            <Badge variant="warning" data-testid="revising-chip">
              Revising
            </Badge>
          )}
        </div>
      </div>
      <div className="space-y-1 mt-1">
        {meta.product && (
          <p
            className="text-sm text-foreground/80 break-words"
            title={meta.product}
          >
            <span className="text-muted-foreground">Product: </span>
            {meta.product}
          </p>
        )}
        {meta.project && (
          <p
            className="text-sm text-foreground/80 break-words"
            title={meta.project}
          >
            <span className="text-muted-foreground">Project: </span>
            {meta.project}
          </p>
        )}
        {meta.customer && (
          <p
            className="text-sm text-foreground/80 break-words"
            title={meta.customer}
          >
            <span className="text-muted-foreground">Customer: </span>
            {meta.customer}
          </p>
        )}
        {row.expected_amount != null && (
          <p className="text-sm text-foreground/80">
            <span className="text-muted-foreground">Amount: </span>
            {formatCurrency(row.expected_amount)}
          </p>
        )}
        {row.expected_close_date && (
          <p className="text-sm text-foreground/80">
            <span className="text-muted-foreground">Expected close: </span>
            {new Date(`${row.expected_close_date.slice(0, 10)}T00:00:00`).toLocaleDateString('en-GB', {
              dateStyle: 'medium',
            })}
          </p>
        )}
        {row.needed_by_date && (
          <p className="text-sm text-foreground/80">
            <span className="text-muted-foreground">Need by: </span>
            {new Date(row.needed_by_date).toLocaleDateString(undefined, {
              dateStyle: 'medium',
            })}
          </p>
        )}
        {row.last_revised_at ? (
          <p className="text-xs text-muted-foreground">
            Revised{' '}
            {new Date(row.last_revised_at).toLocaleDateString(undefined, {
              dateStyle: 'medium',
            })}
          </p>
        ) : (
          row.created_at && (
            <p className="text-xs text-muted-foreground">
              {new Date(row.created_at).toLocaleDateString(undefined, {
                dateStyle: 'medium',
              })}
            </p>
          )
        )}
        {row.rejection_reason && (
          <p className="text-xs text-destructive line-clamp-2">
            {row.rejection_reason}
          </p>
        )}
      </div>
    </LandingCardShell>
  );
}

function SubmissionPreviewDialog({
  row,
  kind,
  slug,
  onOpenChange,
}: {
  row: PortalSubmissionSummary | null;
  kind: PortalLandingKind;
  slug?: string;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  // The list carries no policy block, so the card reads the same `revision`
  // block the detail page renders from. One GET, opened on long press only.
  // Revisions are a submission-kind mechanism: a gated kind has no such
  // endpoint, so it is asked for nothing rather than 404ing on every preview.
  const { policy } = useRevisionPolicy(
    isSubmissionKind(kind) ? kind : 'stock_inquiry',
    isSubmissionKind(kind) ? (row?.id ?? null) : null,
  );
  const meta = row
    ? pickCardMeta(row)
    : { product: undefined, project: undefined, customer: undefined };
  const entries: { label: string; value: string }[] = [];
  if (row) {
    if (row.document_number)
      entries.push({ label: 'Document number', value: row.document_number });
    if (row.title) entries.push({ label: 'Title', value: row.title });
    if (meta.product) entries.push({ label: 'Product', value: meta.product });
    if (meta.project) entries.push({ label: 'Project', value: meta.project });
    if (meta.customer)
      entries.push({ label: 'Customer', value: meta.customer });
    if (row.delivery_order_number)
      entries.push({ label: 'DO number', value: row.delivery_order_number });
    if (row.item_description)
      entries.push({ label: 'Item description', value: row.item_description });
    if (row.purpose) entries.push({ label: 'Purpose', value: row.purpose });
    if (row.reference && row.reference !== row.document_number) {
      entries.push({ label: 'Reference', value: row.reference });
    }
    if (row.created_at) {
      entries.push({
        label: 'Created',
        value: new Date(row.created_at).toLocaleString(undefined, {
          dateStyle: 'medium',
          timeStyle: 'short',
        }),
      });
    }
    if (row.rejection_reason)
      entries.push({ label: 'Rejection reason', value: row.rejection_reason });
  }

  return (
    <Dialog open={Boolean(row)} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="text-base flex items-center gap-2 flex-wrap">
            <span className="break-words">
              {row?.document_number ?? row?.title ?? 'Submission'}
            </span>
            {row &&
              (row.kind === 'complaint' && !row.is_draft ? (
                <span
                  className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ${complaintStatusPillClass(row.status)}`}
                >
                  {complaintStatusLabel(row.status)}
                </span>
              ) : (
                <Badge variant={statusVariant(row)}>
                  {submissionStatusLabel(row)}
                </Badge>
              ))}
          </DialogTitle>
        </DialogHeader>
        <div className="flex-1 min-h-0 overflow-y-auto space-y-3 -mx-1 px-1">
          {entries.map((e) => (
            <div key={e.label} className="space-y-0.5">
              <p className="text-xs uppercase tracking-wide text-muted-foreground">
                {e.label}
              </p>
              <p className="text-sm break-words whitespace-pre-wrap">
                {e.value}
              </p>
            </div>
          ))}
        </div>
        <ReviseAction
          policy={policy}
          onRevise={() => {
            if (row) router.push(portalRevisePath(kind, row.id, slug));
            onOpenChange(false);
          }}
          className="border-t pt-3"
        />
        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            className="h-10"
          >
            Close
          </Button>
          {/* An opportunity's New form takes no source to copy from. */}
          {kind !== 'sales_opportunity' ? (
            <Button
              variant="outline"
              onClick={() => {
                if (row) router.push(portalDuplicatePath(kind, row.id, slug));
                onOpenChange(false);
              }}
              className="h-10"
            >
              <Copy className="h-4 w-4 mr-2" />
              Duplicate
            </Button>
          ) : null}
          <Button
            onClick={() => {
              if (row) router.push(portalDetailPath(kind, row.id, slug));
              onOpenChange(false);
            }}
            className="h-10"
          >
            Open
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

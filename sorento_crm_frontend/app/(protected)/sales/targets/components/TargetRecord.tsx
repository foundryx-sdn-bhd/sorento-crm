'use client';

import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  Building2,
  CalendarRange,
  CircleDollarSign,
  Info,
  LoaderCircleIcon,
  Plus,
  SquarePen,
  Target as TargetIcon,
  Trash2,
  UsersRound,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardHeader } from '@/components/ui/card';
import { DateRangePicker } from '@/components/ui/date-range-picker';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import DetailActions from '@/components/common/DetailActions';
import RecordNavigation from '@/components/common/RecordNavigation';
import type { RecordAction } from '@/components/common/recordActions';
import {
  SearchableMultiSelect,
  type SearchableMultiSelectOption,
} from '@/components/common/SearchableMultiSelect';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDateInMalaysia, todayMalaysiaYyyyMmDd } from '@/lib/helpers';
import {
  useCreateSalesTarget,
  usePatchSalesTarget,
  useSalesTarget,
  useSalesTargetOptions,
  useSalesTargets,
  useTargetCustomerSearch,
  useTargetProductSearch,
} from '../hooks/useSalesTargets';
import { useSalesTargetActions } from '../actions';
import {
  METRIC_LABEL,
  SCOPE_LABEL,
  endOfMonth,
  formatFigure,
  formatPct,
  scopeSummary,
  shortDate,
  splitSummary,
  unitOf,
} from '../lib/format';
import { generatePeriods } from '../lib/periods';
import type {
  CommissionMethod,
  CommissionTier,
  Rounding,
  TargetAccount,
  ThresholdUnit,
  SalesTargetCreatePayload,
  SalesTargetDetail,
  SalesTargetOptions,
  SalesTargetUpdatePayload,
  TargetBasis,
  TargetMetric,
  TargetProductScope,
  TargetSplitUnit,
  TargetSubjectKind,
} from '../types/salesTarget.types';

const KIND_OPTIONS = [
  { value: 'agent', label: 'Agent' },
  { value: 'team', label: 'Team' },
  { value: 'dealer', label: 'Dealer' },
];
const KIND_LABEL: Record<TargetSubjectKind, string> = { agent: 'Agent', team: 'Team', dealer: 'Dealer' };
const THRESHOLD_LABEL: Record<ThresholdUnit, string> = {
  pct: '% of target',
  amount: 'RM',
  points: 'Points',
};
const THRESHOLD_OPTIONS = (Object.keys(THRESHOLD_LABEL) as ThresholdUnit[]).map((value) => ({
  value,
  label: THRESHOLD_LABEL[value],
}));
const ROUNDING_LABEL: Record<Rounding, string> = { sen: 'Sen (0.01)', ringgit: 'Ringgit (1)' };
const ROUNDING_OPTIONS = (Object.keys(ROUNDING_LABEL) as Rounding[]).map((value) => ({
  value,
  label: ROUNDING_LABEL[value],
}));
const TIER_TYPE_OPTIONS = [
  { value: 'main', label: 'Tier' },
  { value: 'additional', label: '+ Additional' },
];
const METRIC_OPTIONS = [
  { value: 'amount', label: 'Amount (RM)' },
  { value: 'quantity', label: 'Quantity (units)' },
];
const COUNTS_OPTIONS = [
  { value: 'ordered', label: 'Ordered' },
  { value: 'delivered', label: 'Delivered' },
];
const APPLIES_OPTIONS: { value: TargetProductScope; label: string }[] = [
  { value: 'all', label: SCOPE_LABEL.all },
  { value: 'categories', label: SCOPE_LABEL.categories },
  { value: 'products', label: SCOPE_LABEL.products },
  { value: 'brands', label: SCOPE_LABEL.brands },
];
const METHOD_LABEL: Record<CommissionMethod, string> = {
  none: 'None',
  marginal: 'Higher rate above each threshold only',
  retroactive: 'Highest rate on everything',
};
const METHOD_OPTIONS = (Object.keys(METHOD_LABEL) as CommissionMethod[]).map((value) => ({
  value,
  label: METHOD_LABEL[value],
}));
const UNIT_OPTIONS = [
  { value: 'day', label: 'Days' },
  { value: 'week', label: 'Weeks' },
  { value: 'month', label: 'Months' },
];

/** The owner's question on the hand test ("what does split mean ya"), answered on the form. */
export const SPLIT_HELP =
  'Split breaks the dates into periods of N days, weeks or months, each with its own figure. Off: one figure for the whole range.';
/** Both dates are required (owner ruling N6); a half-empty range never reaches Save. */
export const DATES_REQUIRED = 'Pick both a start date and an end date.';

type RecordTab = 'details' | 'periods' | 'agents' | 'accounts' | 'team' | 'commission';

/** One commission tier as typed (plan 3.3; one engine, lane SALES-TARGET-DEALER); `key` keeps a
 * row's inputs stable on remove. `from` is the threshold in the target's unit (%, RM, points). */
interface TierDraft {
  key: number;
  from: string;
  rate: string;
  bonus: string;
  additional: boolean;
  /** Tiers per period: the period's start. */
  period: string;
  /** Dealer targets: the rate typed for each counted account; empty = earns nothing. */
  accountRates: Record<string, string>;
}

let nextTierKey = 0;
function tierDraft(tier?: CommissionTier, from = '', accounts: string[] = []): TierDraft {
  nextTierKey += 1;
  const threshold = tier ? (tier.from_pct ?? tier.threshold) : null;
  const accountRates: Record<string, string> = {};
  if (tier?.account_rates) {
    for (const r of tier.account_rates) accountRates[r.customer_id] = String(r.rate);
  } else if (tier && tier.rate !== null && tier.rate !== undefined) {
    for (const id of accounts) accountRates[id] = String(tier.rate);
  }
  return {
    key: nextTierKey,
    from: threshold === null || threshold === undefined ? from : String(threshold),
    rate: tier && tier.rate !== null && tier.rate !== undefined ? String(tier.rate) : '',
    bonus: tier?.bonus_amount === null || tier?.bonus_amount === undefined ? '' : String(tier.bonus_amount),
    additional: !!tier?.is_additional,
    period: tier?.period_start ?? '',
    accountRates,
  };
}

interface Draft {
  kind: TargetSubjectKind | '';
  subjectId: string;
  name: string;
  metric: TargetMetric;
  basis: TargetBasis;
  scope: TargetProductScope;
  categoryIds: string[];
  productIds: string[];
  brandIds: string[];
  start: string;
  end: string;
  split: boolean;
  every: string;
  unit: TargetSplitUnit | '';
  /** An agent target's figure, create only. */
  figure: string;
  /** A team target's figure per agent, create only. */
  agentFigures: Record<string, string>;
  /** Edit: each period's figure by period id; on a team target, its agents' periods (F1). */
  figures: Record<string, string>;
  /** Edit, a team target: a member with no figure yet, by agent then period start (F1). */
  newAgentFigures: Record<string, Record<string, string>>;
  method: CommissionMethod;
  tiers: TierDraft[];
  /** Dealer targets: the counted accounts and the paid one (customer ids). */
  accountIds: string[];
  payoutId: string;
  thresholdUnit: ThresholdUnit;
  rounding: Rounding;
  perPeriod: boolean;
}

function emptyDraft(preset: TargetRecordPreset | undefined): Draft {
  const today = todayMalaysiaYyyyMmDd();
  return {
    kind: preset?.kind ?? '',
    subjectId: preset?.subjectId ?? '',
    name: '',
    metric: 'amount',
    basis: 'ordered',
    scope: 'all',
    categoryIds: [],
    productIds: [],
    brandIds: [],
    start: today,
    end: endOfMonth(today),
    split: false,
    every: '1',
    unit: 'month',
    figure: '',
    agentFigures: {},
    figures: {},
    newAgentFigures: {},
    method: 'none',
    tiers: [],
    accountIds: [],
    payoutId: '',
    // A dealer scheme is fixed RM tiers per period, highest tier paying (owner, 16 Oct).
    thresholdUnit: preset?.kind === 'dealer' ? 'amount' : 'pct',
    rounding: 'sen',
    perPeriod: preset?.kind === 'dealer',
  };
}

function idsOf(target: SalesTargetDetail, kind: 'category' | 'product' | 'brand'): string[] {
  return target.scope.filter((s) => s.kind === kind).map((s) => s.id);
}

function draftOf(target: SalesTargetDetail): Draft {
  const accountIds = (target.accounts ?? []).map((a) => a.customer_id);
  return {
    kind: target.subject_kind,
    subjectId: (target.subject_kind === 'team' ? target.sales_team_id : target.sales_agent_id) ?? '',
    name: target.name,
    metric: target.metric,
    basis: target.basis,
    scope: target.product_scope,
    categoryIds: idsOf(target, 'category'),
    productIds: idsOf(target, 'product'),
    brandIds: idsOf(target, 'brand'),
    start: target.start_date,
    end: target.end_date,
    split: !!target.split_every,
    every: String(target.split_every ?? 1),
    unit: target.split_unit ?? 'month',
    figure: '',
    agentFigures: {},
    figures: savedFigures(target),
    newAgentFigures: {},
    method: target.commission_method ?? 'none',
    tiers: (target.tiers ?? []).map((t) => tierDraft(t, '', accountIds)),
    accountIds,
    payoutId: target.payout_account?.customer_id ?? '',
    thresholdUnit: target.threshold_unit ?? 'pct',
    rounding: target.rounding ?? 'sen',
    perPeriod: !!target.tiers_per_period,
  };
}

/** The figures Edit mode edits, by period id: an agent target's own periods, or a team
 * target's agents' periods (a team period is their sum, T3). */
function savedFigures(target: SalesTargetDetail): Record<string, string> {
  const out: Record<string, string> = {};
  if (target.subject_kind === 'team') {
    for (const child of target.children) {
      for (const p of child.periods) if (p.id) out[p.id] = String(p.target_value);
    }
  } else {
    for (const p of target.periods) out[p.id] = String(p.target_value);
  }
  return out;
}

/** A dealer tier's rates: one `rate` when every counted account has the same one, else the
 * accounts given a rate (only they earn on the tier). */
function dealerRates(t: TierDraft, accountIds: string[]): Pick<CommissionTier, 'rate' | 'account_rates'> {
  const typed = accountIds.map((id) => (t.accountRates[id] ?? '').trim());
  if (typed.length && typed.every((v) => v !== '' && Number(v) === Number(typed[0]))) {
    return { rate: Number(typed[0]), account_rates: null };
  }
  return {
    rate: 0,
    account_rates: accountIds
      .filter((id) => (t.accountRates[id] ?? '').trim() !== '')
      .map((id) => ({ customer_id: id, rate: Number(t.accountRates[id]) })),
  };
}

function tiersOf(draft: Draft): CommissionTier[] {
  const dealer = draft.kind === 'dealer';
  return draft.tiers.map((t) => ({
    ...(draft.thresholdUnit === 'pct' ? { from_pct: Number(t.from) } : { threshold: Number(t.from) }),
    ...(dealer ? dealerRates(t, draft.accountIds) : { rate: Number(t.rate) }),
    bonus_amount: t.bonus.trim() === '' ? null : Number(t.bonus),
    ...(t.additional ? { is_additional: true } : {}),
    ...(draft.perPeriod ? { period_start: t.period || null } : {}),
  }));
}

/** The tiers as the server returns them, in the same shape `tiersOf` builds, to spot a change. */
function savedTiers(target: SalesTargetDetail): CommissionTier[] {
  const base = draftOf(target);
  return tiersOf(base);
}

/** Why the tiers cannot be saved yet, or `''`. */
function tierProblem(draft: Draft): string {
  const bad = (text: string, required: boolean) =>
    text.trim() === '' ? required : !Number.isFinite(Number(text)) || Number(text) < 0;
  const dealer = draft.kind === 'dealer';
  const unit = THRESHOLD_LABEL[draft.thresholdUnit];
  if (draft.tiers.some((t) => bad(t.from, true) || bad(t.bonus, false))) {
    return draft.thresholdUnit === 'pct' ? 'Type a from % for every tier.' : `Type the threshold (${unit}) of every tier.`;
  }
  if (dealer) {
    if (draft.tiers.some((t) => Object.values(t.accountRates).some((v) => bad(v, false)))) return 'A rate is not a number.';
    if (draft.tiers.some((t) => !draft.accountIds.some((id) => (t.accountRates[id] ?? '').trim() !== ''))) {
      return 'Give every tier a rate for at least one account.';
    }
  } else if (draft.tiers.some((t) => bad(t.rate, true))) {
    return 'Type a rate for every tier.';
  }
  if (draft.perPeriod && draft.tiers.some((t) => !t.period)) return 'Pick the period of every tier.';
  const keys = draft.tiers.map((t) => `${draft.perPeriod ? t.period : ''}|${t.additional}|${Number(t.from)}`);
  if (new Set(keys).size !== keys.length) return 'Two tiers start at the same threshold in one period.';
  if (draft.thresholdUnit === 'points' && draft.metric !== 'amount') return 'Points thresholds need an amount target.';
  if (draft.method === 'none' && draft.tiers.length) return 'Pick how the tiers pay, or remove the tiers.';
  return '';
}

function splitOf(draft: Draft): { every: number; unit: TargetSplitUnit } | null {
  const every = Number(draft.every);
  if (!draft.split || !draft.unit || !Number.isInteger(every) || every < 1 || every > 99) return null;
  return { every, unit: draft.unit };
}

function scopeLists(draft: Draft) {
  return {
    category_ids: draft.scope === 'categories' ? draft.categoryIds : [],
    product_ids: draft.scope === 'products' ? draft.productIds : [],
    brand_ids: draft.scope === 'brands' ? draft.brandIds : [],
  };
}

/** Only what changed, so a rename of a child never touches what it follows. */
function changes(target: SalesTargetDetail, draft: Draft): SalesTargetUpdatePayload {
  const out: SalesTargetUpdatePayload = {};
  if (draft.name.trim() !== target.name) out.name = draft.name.trim();
  // Every changed figure rides in this one request (F1).
  const saved = savedFigures(target);
  const figures = Object.keys(draft.figures)
    .filter((periodId) => periodId in saved && toNumber(draft.figures[periodId]) !== Number(saved[periodId]))
    .map((periodId) => ({ period_id: periodId, target_value: toNumber(draft.figures[periodId]) }));
  if (figures.length) out.figures = figures;
  const newAgents = target.members_without_figure.flatMap((m) => {
    const typed = draft.newAgentFigures[m.sales_agent_id] ?? {};
    const entries = target.periods
      .filter((p) => (typed[p.period_start] ?? '').trim() !== '')
      .map((p) => ({ period_start: p.period_start, target_value: toNumber(typed[p.period_start]) }));
    return entries.length ? [{ sales_agent_id: m.sales_agent_id, figures: entries }] : [];
  });
  if (newAgents.length) out.new_agents = newAgents;
  const tiers = tiersOf(draft);
  if (
    draft.method !== (target.commission_method ?? 'none') ||
    draft.thresholdUnit !== (target.threshold_unit ?? 'pct') ||
    draft.rounding !== (target.rounding ?? 'sen') ||
    draft.perPeriod !== !!target.tiers_per_period ||
    JSON.stringify(tiers) !== JSON.stringify(savedTiers(target))
  ) {
    out.commission_method = draft.method;
    if (draft.thresholdUnit !== (target.threshold_unit ?? 'pct')) out.threshold_unit = draft.thresholdUnit;
    if (draft.rounding !== (target.rounding ?? 'sen')) out.rounding = draft.rounding;
    if (draft.perPeriod !== !!target.tiers_per_period) out.tiers_per_period = draft.perPeriod;
    out.tiers = tiers;
  }
  if (target.subject_kind === 'dealer') {
    const before = (target.accounts ?? []).map((a) => a.customer_id);
    if (draft.accountIds.join() !== before.join()) out.customer_ids = draft.accountIds;
    if (draft.payoutId !== (target.payout_account?.customer_id ?? '')) out.payout_customer_id = draft.payoutId || null;
  }
  // A child's What counts and Dates follow its team target (T3); its figures and tiers are its own.
  if (target.parent) return out;
  if (draft.metric !== target.metric) out.metric = draft.metric;
  if (draft.basis !== target.basis) out.basis = draft.basis;
  const before = scopeLists(draftOf(target));
  const after = scopeLists(draft);
  const scopeChanged =
    draft.scope !== target.product_scope ||
    (Object.keys(after) as (keyof typeof after)[]).some((k) => after[k].join() !== before[k].join());
  if (scopeChanged) Object.assign(out, { product_scope: draft.scope, ...after });
  if (draft.start !== target.start_date) out.start_date = draft.start;
  if (draft.end !== target.end_date) out.end_date = draft.end;
  const split = splitOf(draft);
  if ((split?.every ?? null) !== target.split_every || (split?.unit ?? null) !== target.split_unit) {
    out.split_every = split?.every ?? null;
    out.split_unit = split?.unit ?? null;
  }
  return out;
}

/** Members of the team on any day of the range, one line per agent (S1-27). */
function membersInRange(
  team: SalesTargetOptions['teams'][number] | undefined,
  start: string,
  end: string,
): { sales_agent_id: string; label: string }[] {
  if (!team || !start || !end) return [];
  const seen = new Map<string, string>();
  for (const m of team.members) {
    const inRange = (!m.valid_from || m.valid_from <= end) && (!m.valid_to || m.valid_to >= start);
    if (inRange && !seen.has(m.sales_agent_id)) seen.set(m.sales_agent_id, m.label);
  }
  return Array.from(seen, ([sales_agent_id, label]) => ({ sales_agent_id, label })).sort((a, b) =>
    a.label.localeCompare(b.label),
  );
}

function toNumber(value: string): number {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      {htmlFor ? (
        <Label htmlFor={htmlFor} id={`${htmlFor}-label`} className="text-xs text-muted-foreground">
          {label}
        </Label>
      ) : (
        <span className="text-xs text-muted-foreground">{label}</span>
      )}
      <div className="min-w-0 text-sm">{children}</div>
    </div>
  );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Card>
      <section aria-label={label} className="flex flex-col gap-3 p-5">
        <h3 className="text-sm font-semibold">{label}</h3>
        {children}
      </section>
    </Card>
  );
}

export interface TargetRecordPreset {
  kind?: TargetSubjectKind;
  subjectId?: string;
}

/**
 * The target record (UAC S1-18, S1-23 to S1-28; J14; the S1 hand test of 27 Sep, F4): ONE
 * view for a new target (`/sales/targets/new`, no `id`) and an existing one
 * (`/sales/targets/{id}`), in place of the retired Set target modal.
 *
 * The standard record: a header card (the TGT number, the name, and in the meta strip who it
 * is for, its team target, the agent count, Created and Updated; prev/next and the gear on the
 * right), then line tabs in the order **Details** (Target, What counts, Dates, Figure),
 * **Periods**, **Agents** (a team target only) and **Commission**. Edit is an item in the gear
 * with Duplicate and Delete; the edit view is the same tabs with each value swapped for its
 * input in place, and Cancel and Save in the header card. A new target opens in that edit view,
 * empty, with the defaults Amount, Ordered, All products, today to month end, split off.
 *
 * Every single pick is `SearchableSelect`, every multi pick `SearchableMultiSelect`, and a
 * picked category, product or brand always shows its "CODE - Name" (F5): the record's own scope
 * labels seed the pickers, and every product the search returns is remembered by name.
 *
 * Figures are edited in Edit mode only (the owner's retest of 27 Sep, F1): Periods takes a
 * figure per period, and a team target's Agents tab takes each agent's figure per period, with
 * the team total per period recomputed as it is typed. Save sends the header, every changed
 * figure and the commission tiers in one PATCH; Cancel discards. The Commission tab (F2) edits
 * the tiers in Edit mode and shows them as rows otherwise.
 *
 * An agent target of a team target (F3) is headed "Agent target, part of <team target>" and
 * has a Team target tab showing its parent. It follows its parent (T3): What counts and Dates
 * stay read-only with one "Set on the team target" line; its name, its figures (which re-sum
 * the team target) and its tiers are its own.
 */
export default function TargetRecord({ id, preset }: { id?: string; preset?: TargetRecordPreset }) {
  const router = useRouter();
  const isCreate = !id;
  const canEdit = useHasPermission('sales.targets.edit');
  const canOpenAgent = useHasPermission('master_data.sales_agents.view');
  const today = todayMalaysiaYyyyMmDd();

  const loaded = useSalesTarget(id ?? null);
  const { isLoading, isError } = loaded;
  const target = isCreate ? undefined : loaded.data;
  const [draft, setDraft] = useState<Draft | null>(() => (isCreate ? emptyDraft(preset) : null));
  const editing = draft !== null;
  const { data: options } = useSalesTargetOptions(editing);
  const create = useCreateSalesTarget();
  const patchHeader = usePatchSalesTarget();
  const { actions: targetActions, pending } = useSalesTargetActions(target, {
    onDeleted: () => router.push('/sales/targets'),
  });
  const { data: siblings } = useSalesTargets(
    { all: true, subject: target?.subject_kind ?? 'agent' },
    !!target,
  );
  const [tab, setTab] = useState<RecordTab>('details');

  // Every name a picked category, product or brand is known by (F5).
  const labels = useRef(new Map<string, string>());
  for (const item of target?.scope ?? []) labels.current.set(item.id, item.label);
  for (const c of options?.categories ?? []) labels.current.set(c.id, c.label);
  for (const b of options?.brands ?? []) labels.current.set(b.id, b.label);
  for (const a of target?.accounts ?? []) labels.current.set(a.customer_id, `${a.code} - ${a.name}`);
  if (target?.payout_account) {
    labels.current.set(target.payout_account.customer_id, `${target.payout_account.code} - ${target.payout_account.name}`);
  }
  const searchCustomers = useTargetCustomerSearch();
  const fetchCustomers = useCallback(
    async (query: string) => {
      const rows = await searchCustomers(query);
      for (const row of rows) labels.current.set(row.value, row.label);
      return rows;
    },
    [searchCustomers],
  );
  const searchProducts = useTargetProductSearch();
  const fetchProducts = useCallback(
    async (query: string) => {
      const rows = await searchProducts(query);
      for (const row of rows) labels.current.set(row.value, row.label);
      return rows;
    },
    [searchProducts],
  );

  const ids = useMemo(() => {
    const seen: string[] = [];
    for (const row of siblings?.rows ?? []) {
      if (row.target_id && !seen.includes(row.target_id)) seen.push(row.target_id);
    }
    return seen;
  }, [siblings]);

  if (!isCreate && isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full rounded-xl" />
        <Skeleton className="h-48 w-full rounded-xl" />
      </div>
    );
  }
  if (!isCreate && (isError || !target)) {
    return (
      <Card className="flex flex-col items-center gap-3 p-10 text-center">
        <div className="text-sm font-semibold">Target not found</div>
      </Card>
    );
  }

  const set = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));
  const kind: TargetSubjectKind | '' = isCreate ? (draft?.kind ?? '') : (target as SalesTargetDetail).subject_kind;
  const isTeam = kind === 'team';
  const isDealer = kind === 'dealer';
  const isChild = !!target?.parent;
  const thresholdUnit = draft?.thresholdUnit ?? target?.threshold_unit ?? 'pct';
  // On RM / points thresholds each period's figure is its lowest tier: nothing to type.
  const figureFromTiers = thresholdUnit !== 'pct';
  const metric = draft?.metric ?? target?.metric ?? 'amount';
  const unit = unitOf(metric);

  // Periods the dates make, for the hint and the new target's Periods tab.
  const split = draft ? splitOf(draft) : null;
  const datesMissing = !!draft && (!draft.start || !draft.end);
  let periods: { start: string; end: string }[] = [];
  let periodError = '';
  if (draft && !datesMissing) {
    try {
      periods = generatePeriods(draft.start, draft.end, split?.every ?? null, split?.unit ?? null);
    } catch (error) {
      periodError = error instanceof Error ? error.message : 'These dates do not make periods.';
    }
  }

  const team = isTeam ? options?.teams.find((t) => t.id === draft?.subjectId) : undefined;
  const members = draft && isCreate && isTeam ? membersInRange(team, draft.start, draft.end) : [];
  const teamSum = members.reduce((sum, m) => sum + toNumber(draft?.agentFigures[m.sales_agent_id] ?? ''), 0);

  const scopeReady =
    !draft ||
    draft.scope === 'all' ||
    (draft.scope === 'categories' && draft.categoryIds.length > 0) ||
    (draft.scope === 'products' && draft.productIds.length > 0) ||
    (draft.scope === 'brands' && draft.brandIds.length > 0);
  const saving = create.isPending || patchHeader.isPending;
  const commissionProblem = draft ? tierProblem(draft) : '';
  const canSave =
    !!draft &&
    !commissionProblem &&
    draft.name.trim().length > 0 &&
    !datesMissing &&
    !periodError &&
    scopeReady &&
    (!isDealer || draft.accountIds.length > 0) &&
    (!isCreate ||
      (!!draft.kind &&
        (isDealer
          ? draft.thresholdUnit !== 'pct' || draft.figure.trim() !== ''
          : !!draft.subjectId &&
            (isTeam ? members.length > 0 : figureFromTiers || draft.figure.trim() !== '')))) &&
    !saving;

  const save = async () => {
    if (!draft || !canSave) return;
    if (!isCreate && target) {
      const pendingChanges = changes(target, draft);
      if (Object.keys(pendingChanges).length === 0) {
        setDraft(null);
        return;
      }
      try {
        await patchHeader.mutateAsync({ targetId: target.id, ...pendingChanges });
        setDraft(null);
      } catch {
        // The hook toasted the reason; the edit stays open so nothing typed is lost.
      }
      return;
    }
    const lists = scopeLists(draft);
    const common = {
      name: draft.name.trim(),
      metric: draft.metric,
      basis: draft.basis,
      product_scope: draft.scope,
      ...(draft.scope === 'categories' ? { category_ids: lists.category_ids } : {}),
      ...(draft.scope === 'products' ? { product_ids: lists.product_ids } : {}),
      ...(draft.scope === 'brands' ? { brand_ids: lists.brand_ids } : {}),
      start_date: draft.start,
      end_date: draft.end,
      ...(split ? { split_every: split.every, split_unit: split.unit } : {}),
      // Sent only when not the default, so a plain agent or team target's payload is unchanged.
      ...(draft.thresholdUnit !== 'pct' ? { threshold_unit: draft.thresholdUnit } : {}),
      ...(draft.rounding !== 'sen' ? { rounding: draft.rounding } : {}),
      ...(draft.perPeriod ? { tiers_per_period: true } : {}),
      ...(draft.tiers.length ? { commission_method: draft.method, tiers: tiersOf(draft) } : {}),
    };
    const figure = figureFromTiers ? {} : { target_value: toNumber(draft.figure) };
    const payload: SalesTargetCreatePayload =
      draft.kind === 'dealer'
        ? {
            subject_kind: 'dealer',
            customer_ids: draft.accountIds,
            ...(draft.payoutId ? { payout_customer_id: draft.payoutId } : {}),
            ...figure,
            ...common,
          }
        : draft.kind === 'agent'
        ? { subject_kind: 'agent', sales_agent_id: draft.subjectId, ...figure, ...common }
        : {
            subject_kind: 'team',
            sales_team_id: draft.subjectId,
            agent_figures: members.map((m) => ({
              sales_agent_id: m.sales_agent_id,
              target_value: toNumber(draft.agentFigures[m.sales_agent_id] ?? ''),
            })),
            ...common,
          };
    try {
      const created = await create.mutateAsync(payload);
      router.push(`/sales/targets/${created.id}`);
    } catch {
      // The hook toasted the reason; the record stays open with what was typed.
    }
  };
  const cancel = () => (isCreate ? router.push('/sales/targets') : setDraft(null));

  const tabs: { value: RecordTab; label: string; icon: typeof Info }[] = [
    { value: 'details', label: 'Details', icon: Info },
    { value: 'periods', label: 'Periods', icon: CalendarRange },
    ...(isTeam ? [{ value: 'agents' as const, label: 'Agents', icon: UsersRound }] : []),
    ...(isDealer ? [{ value: 'accounts' as const, label: 'Accounts', icon: Building2 }] : []),
    ...(isChild ? [{ value: 'team' as const, label: 'Team target', icon: TargetIcon }] : []),
    { value: 'commission', label: 'Commission', icon: CircleDollarSign },
  ];

  const actions: RecordAction[] =
    target && canEdit
      ? [{ key: 'sales_target.edit', label: 'Edit', icon: SquarePen, run: () => setDraft(draftOf(target)) }, ...targetActions]
      : targetActions;
  const index = target ? ids.indexOf(target.id) : -1;

  // A team period in Edit mode: the live sum of what is typed for its agents (T3, F1).
  const teamTotal = (periodStart: string): number => {
    if (!target || !draft) return 0;
    let sum = 0;
    for (const child of target.children) {
      const cp = child.periods.find((x) => x.period_start === periodStart);
      if (cp?.id) sum += toNumber(draft.figures[cp.id] ?? '');
    }
    for (const typed of Object.values(draft.newAgentFigures)) sum += toNumber(typed[periodStart] ?? '');
    return sum;
  };
  // Add tier on the read-only Commission tab starts Edit mode with a first tier row (F2).
  const startAddingTier = () => {
    if (!target) return;
    const base = draftOf(target);
    setDraft({
      ...base,
      method: base.method === 'none' ? (base.kind === 'dealer' ? 'retroactive' : 'marginal') : base.method,
      tiers: [...base.tiers, tierDraft(undefined, base.tiers.length || base.thresholdUnit !== 'pct' ? '' : '0')],
    });
    setTab('commission');
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="block py-4" data-testid="record-header">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <RecordIdentity target={target} canOpenAgent={canOpenAgent} />
            {editing ? (
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <Button variant="outline" size="sm" onClick={cancel} disabled={saving}>
                  Cancel
                </Button>
                <Button size="sm" onClick={save} disabled={!canSave}>
                  {saving ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
                  Save
                </Button>
              </div>
            ) : (
              <DetailActions
                pagerNode={
                  <RecordNavigation
                    index={index >= 0 ? index + 1 : null}
                    total={ids.length}
                    hasPrevious={index > 0}
                    hasNext={index >= 0 && index < ids.length - 1}
                    onPrevious={() => router.push(`/sales/targets/${ids[index - 1]}`)}
                    onNext={() => router.push(`/sales/targets/${ids[index + 1]}`)}
                    ariaLabel="target"
                  />
                }
                actions={actions}
                pendingAction={pending}
                gearLabel="Target options"
              />
            )}
          </div>
        </CardHeader>
      </Card>

      <Tabs value={tab} onValueChange={(v) => setTab(v as RecordTab)}>
        <TabsList variant="line" className="mb-5">
          {tabs.map((t) => (
            <TabsTrigger key={t.value} value={t.value} onClick={() => setTab(t.value)}>
              <t.icon className="size-4" />
              <span>{t.label}</span>
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="details" className="space-y-5">
          <Section label="Target">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Field label="Target for" htmlFor={isCreate ? 'target-for' : undefined}>
                {isCreate && draft ? (
                  <SearchableSelect
                    id="target-for"
                    value={draft.kind}
                    onChange={(v) =>
                      set({
                        kind: (v as TargetSubjectKind) || '',
                        subjectId: '',
                        agentFigures: {},
                        accountIds: [],
                        payoutId: '',
                        // A dealer scheme is fixed RM tiers, highest tier paying (owner, 16 Oct).
                        ...(v === 'dealer' ? { thresholdUnit: 'amount' as const, perPeriod: true } : {}),
                      })
                    }
                    options={KIND_OPTIONS}
                    placeholder="Pick one"
                    disabled={!!preset?.subjectId}
                  />
                ) : (
                  KIND_LABEL[kind || 'agent']
                )}
              </Field>
              <Field label={isDealer ? 'Accounts' : 'Who'} htmlFor={(isCreate || (isDealer && editing)) ? 'target-who' : undefined}>
                {isDealer && draft ? (
                  <SearchableMultiSelect
                    id="target-who"
                    value={draft.accountIds}
                    onChange={(ids) =>
                      set({
                        accountIds: ids,
                        payoutId: draft.payoutId || ids[0] || '',
                      })
                    }
                    fetchOptions={fetchCustomers}
                    selectedOptions={draft.accountIds.map((id) => ({ value: id, label: labels.current.get(id) ?? id }))}
                    placeholder="Search customers by code or name"
                    clearable
                    wrapOptions
                  />
                ) : isCreate && draft ? (
                  <SearchableSelect
                    id="target-who"
                    value={draft.subjectId}
                    onChange={(v) => set({ subjectId: v, agentFigures: {} })}
                    options={
                      draft.kind === 'team'
                        ? (options?.teams ?? []).map((t) => ({ value: t.id, label: t.name }))
                        : draft.kind === 'agent'
                          ? (options?.agents ?? []).map((a) => ({ value: a.id, label: a.label }))
                          : []
                    }
                    placeholder={draft.kind === 'team' ? 'Pick a team' : 'Pick an agent'}
                    emptyMessage={draft.kind ? 'Nothing active to pick.' : 'Pick Target for first.'}
                    disabled={!draft.kind || !!preset?.subjectId}
                    wrapOptions
                  />
                ) : (
                  <span className="block truncate" title={target?.subject_label}>
                    {target?.subject_label}
                  </span>
                )}
              </Field>
              <Field label="Name" htmlFor={editing ? 'target-name' : undefined}>
                {editing ? (
                  <Input
                    id="target-name"
                    value={draft.name}
                    maxLength={120}
                    onChange={(e) => set({ name: e.target.value })}
                    className="h-8"
                  />
                ) : (
                  <span className="block truncate" title={target?.name}>
                    {target?.name}
                  </span>
                )}
              </Field>
            </div>
          </Section>

          <Section label="What counts">
            {isChild ? <p className="text-xs text-muted-foreground">Set on the team target</p> : null}
            {editing && !isChild ? (
              <WhatCountsEditor draft={draft} set={set} options={options} labels={labels.current} fetchProducts={fetchProducts} />
            ) : target ? (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <Field label="Measure">{`${METRIC_LABEL[target.metric]} (${unitOf(target.metric)})`}</Field>
                <Field label="Counts">{target.counts_label}</Field>
                <Field label="Applies to">
                  {target.product_scope === 'all'
                    ? SCOPE_LABEL.all
                    : `${SCOPE_LABEL[target.product_scope]}: ${scopeSummary(target.product_scope, target.scope.length)}`}
                </Field>
                {target.scope.length ? (
                  <ul className="flex flex-col gap-1 sm:col-span-3">
                    {target.scope.map((item) => (
                      <li key={item.id} className="truncate text-sm" title={item.label}>
                        {item.label}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}
          </Section>

          <Section label="Dates">
            {editing && !isChild ? (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                  <div className="sm:col-span-2">
                    <Field label="Start and end date" htmlFor="target-dates">
                      <DateRangePicker
                        id="target-dates"
                        aria-label="Start and end date"
                        from={draft.start || null}
                        to={draft.end || null}
                        onChange={({ from, to }) => set({ start: from ?? '', end: to ?? '' })}
                        className="sm:w-72"
                      />
                    </Field>
                  </div>
                  <Field label="Split">
                    <div className="flex flex-wrap items-center gap-2">
                      <Switch aria-label="Split" checked={draft.split} onCheckedChange={(on) => set({ split: on })} />
                      {draft.split ? (
                        <>
                          <Label htmlFor="target-split-every" className="text-xs">
                            Every
                          </Label>
                          <Input
                            id="target-split-every"
                            type="number"
                            min={1}
                            max={99}
                            value={draft.every}
                            onChange={(e) => set({ every: e.target.value })}
                            className="h-8 w-16"
                          />
                          <Label htmlFor="target-split-unit" className="sr-only">
                            Split unit
                          </Label>
                          <SearchableSelect
                            id="target-split-unit"
                            value={draft.unit}
                            // Clearing the unit turns the split off (S1-24).
                            onChange={(v) => (v ? set({ unit: v as TargetSplitUnit }) : set({ split: false, unit: 'month' }))}
                            options={UNIT_OPTIONS}
                            clearable
                            className="w-28"
                          />
                        </>
                      ) : null}
                    </div>
                  </Field>
                </div>
                <p className="text-xs text-muted-foreground">{SPLIT_HELP}</p>
                {datesMissing ? (
                  <p className="text-sm text-destructive">{DATES_REQUIRED}</p>
                ) : periodError ? (
                  <p className="text-sm text-destructive">{periodError}</p>
                ) : periods.length ? (
                  <p className="text-xs text-muted-foreground">
                    {periods.length === 1
                      ? `1 period, ${shortDate(periods[0].start)} to ${shortDate(periods[0].end)}`
                      : `${periods.length} periods, the last ${shortDate(periods[periods.length - 1].start)} to ${shortDate(periods[periods.length - 1].end)}`}
                  </p>
                ) : null}
              </div>
            ) : target ? (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <div className="sm:col-span-2">
                  <Field label="Start and end date">{`${shortDate(target.start_date)} to ${shortDate(target.end_date)}`}</Field>
                </div>
                <Field label="Split">{splitSummary(target.split_every, target.split_unit)}</Field>
              </div>
            ) : null}
          </Section>

          <Section label="Figure">
            {figureFromTiers && !isTeam ? (
              <Field label={`Target figure (${thresholdUnit === 'points' ? 'points' : unit})`}>
                <span className="tabular-nums">{target ? figureSummary(target) : '-'}</span>
              </Field>
            ) : isCreate && draft && !isTeam ? (
              <Field label={`Target figure (${unit}) ${split ? 'per period' : 'for the whole range'}`} htmlFor="target-figure">
                <Input
                  id="target-figure"
                  type="number"
                  min={0}
                  step="any"
                  value={draft.figure}
                  onChange={(e) => set({ figure: e.target.value })}
                  className="h-8 sm:w-60"
                />
              </Field>
            ) : isCreate ? (
              <Field label={`Team target (${unit})`}>
                <span className="tabular-nums">{formatFigure(teamSum)}</span>
              </Field>
            ) : target ? (
              <Field label={`${isTeam ? 'Team target' : 'Target figure'} (${unit})`}>
                <span className="tabular-nums">{figureSummary(target)}</span>
              </Field>
            ) : null}
          </Section>
        </TabsContent>

        <TabsContent value="periods">
          <Card>
            <section aria-label="Periods" className="flex flex-col gap-3 p-5">
              {isCreate ? (
                periods.length ? (
                  <PeriodsTable unit={unit}>
                    {periods.map((p) => (
                      <tr key={p.start}>
                        <td className="whitespace-nowrap px-3 py-2">{`${shortDate(p.start)} to ${shortDate(p.end)}`}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                          {formatFigure(isTeam ? teamSum : toNumber(draft?.figure ?? ''))}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">-</td>
                        <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">-</td>
                      </tr>
                    ))}
                  </PeriodsTable>
                ) : (
                  <div className="rounded-lg border border-dashed py-8 text-center text-sm font-medium">No periods yet</div>
                )
              ) : target ? (
                <PeriodsTable
                  unit={unit}
                  commission={target.commission_method !== 'none'}
                  ratio={target.threshold_unit === 'points'}
                >
                  {target.periods.map((p) => {
                    const future = p.period_start > today;
                    const periodLabel = `${shortDate(p.period_start)} to ${shortDate(p.period_end)}`;
                    return (
                      <tr key={p.id} className={p.is_current ? 'bg-primary/5' : undefined}>
                        <td className="whitespace-nowrap px-3 py-2">
                          <span className="inline-flex items-center gap-2">
                            {`${shortDate(p.period_start)} to ${shortDate(p.period_end)}`}
                            {p.is_current ? (
                              <Badge variant="primary" appearance="light" size="sm">
                                Current
                              </Badge>
                            ) : null}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                          {editing && draft && !isTeam && !figureFromTiers ? (
                            <FigureInput
                              label={`Figure, ${periodLabel}`}
                              value={draft.figures[p.id] ?? ''}
                              onChange={(value) => set({ figures: { ...draft.figures, [p.id]: value } })}
                            />
                          ) : editing && isTeam ? (
                            formatFigure(teamTotal(p.period_start))
                          ) : (
                            formatFigure(p.target_value)
                          )}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                          {future ? '-' : formatFigure(p.achieved_value)}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                          {future ? '-' : formatPct(p.achieved_pct)}
                        </td>
                        {target.threshold_unit === 'points' ? (
                          <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                            {p.ratio_missing ? 'No ratio' : formatFigure(p.point_ratio ?? null)}
                          </td>
                        ) : null}
                        {target.commission_method !== 'none' ? (
                          <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                            {future || p.commission_earned === null || p.commission_earned === undefined
                              ? '-'
                              : formatFigure(p.commission_earned + (p.bonus_earned ?? 0))}
                          </td>
                        ) : null}
                      </tr>
                    );
                  })}
                </PeriodsTable>
              ) : null}
            </section>
          </Card>
        </TabsContent>

        {isTeam ? (
          <TabsContent value="agents">
            <Card>
              <section aria-label="Agents" className="flex flex-col gap-3 p-5">
                {isCreate && draft ? (
                  <NewTeamFigures
                    members={members}
                    hasTeam={!!draft.subjectId}
                    figures={draft.agentFigures}
                    unit={unit}
                    perWhat={split ? 'per period' : 'for the whole range'}
                    sum={teamSum}
                    onChange={(agentId, value) => set({ agentFigures: { ...draft.agentFigures, [agentId]: value } })}
                  />
                ) : target ? (
                  <TeamFigures
                    target={target}
                    draft={draft}
                    unit={unit}
                    total={(p) => (draft ? teamTotal(p.period_start) : p.target_value)}
                    onFigure={(periodId, value) => set({ figures: { ...(draft?.figures ?? {}), [periodId]: value } })}
                    onNewFigure={(agentId, periodStart, value) =>
                      set({
                        newAgentFigures: {
                          ...(draft?.newAgentFigures ?? {}),
                          [agentId]: { ...(draft?.newAgentFigures[agentId] ?? {}), [periodStart]: value },
                        },
                      })
                    }
                  />
                ) : null}
              </section>
            </Card>
          </TabsContent>
        ) : null}

        {isDealer ? (
          <TabsContent value="accounts">
            <Card>
              <section aria-label="Accounts" className="flex flex-col gap-3 p-5">
                <DealerAccounts
                  target={target}
                  draft={draft}
                  labels={labels.current}
                  fetchCustomers={fetchCustomers}
                  set={set}
                />
              </section>
            </Card>
          </TabsContent>
        ) : null}

        {target?.parent ? (
          <TabsContent value="team">
            <ParentTarget parentId={target.parent.id} today={today} />
          </TabsContent>
        ) : null}

        <TabsContent value="commission">
          <Card>
            <section aria-label="Commission" className="flex flex-col gap-3 p-5">
              <CommissionTiers
                target={target}
                draft={draft}
                metric={metric}
                problem={commissionProblem}
                canEdit={canEdit}
                set={set}
                onStartAdding={startAddingTier}
                periodStarts={
                  isCreate ? periods.map((p) => ({ start: p.start, end: p.end })) : (target?.periods ?? []).map((p) => ({ start: p.period_start, end: p.period_end }))
                }
                accounts={accountColumns(target, draft, labels.current)}
              />
            </section>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

/** The header card's left side: the number, the name, and the read-only meta strip. */
function RecordIdentity({
  target,
  canOpenAgent,
}: {
  target: SalesTargetDetail | undefined;
  canOpenAgent: boolean;
}) {
  if (!target) {
    return (
      <div className="flex min-w-0 flex-col gap-1.5">
        <h2 className="truncate text-lg font-semibold">New target</h2>
      </div>
    );
  }
  const isTeam = target.subject_kind === 'team';
  const subjectHref = target.subject_kind === 'dealer'
    ? null
    : isTeam
    ? `/sales/teams/${target.sales_team_id}`
    : canOpenAgent
      ? `/master-data-management/sales-agents/${target.sales_agent_id}`
      : null;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{target.target_no}</span>
      <h2 className="truncate text-lg font-semibold" title={target.name}>
        {target.name}
      </h2>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="min-w-0 truncate">
          {'For '}
          {subjectHref ? (
            <Link href={subjectHref} className="text-primary hover:underline">
              {target.subject_label}
            </Link>
          ) : (
            target.subject_label
          )}
        </span>
        {target.parent ? (
          <span className="min-w-0 truncate">
            {'Agent target, part of '}
            <Link href={`/sales/targets/${target.parent.id}`} className="text-primary hover:underline">
              {target.parent.name}
            </Link>
          </span>
        ) : null}
        {isTeam ? (
          <span>{`${target.child_count} agent target${target.child_count === 1 ? '' : 's'}`}</span>
        ) : null}
        {target.created_at ? <span>Created {formatDateInMalaysia(target.created_at)}</span> : null}
        {target.updated_at ? <span>Updated {formatDateInMalaysia(target.updated_at)}</span> : null}
      </div>
    </div>
  );
}

/** One figure for every period, or "Varies by period". */
function figureSummary(target: SalesTargetDetail): string {
  const values = new Set(target.periods.map((p) => p.target_value));
  if (values.size !== 1) return 'Varies by period';
  const [only] = Array.from(values);
  return `${formatFigure(only)} ${target.periods.length === 1 ? 'for the whole range' : 'per period'}`;
}

function PeriodsTable({
  unit,
  commission = false,
  ratio = false,
  children,
}: {
  unit: string;
  commission?: boolean;
  ratio?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full min-w-[32rem] text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr>
            <th className="px-3 py-2 text-start font-medium">Period</th>
            <th className="px-3 py-2 text-end font-medium">{`Target (${unit})`}</th>
            <th className="px-3 py-2 text-end font-medium">Achieved</th>
            <th className="px-3 py-2 text-end font-medium">%</th>
            {ratio ? <th className="px-3 py-2 text-end font-medium">RM per point</th> : null}
            {commission ? <th className="px-3 py-2 text-end font-medium">Commission (RM)</th> : null}
          </tr>
        </thead>
        <tbody className="divide-y">{children}</tbody>
      </table>
    </div>
  );
}

/** What counts, in edit: Measure, Counts, Applies to, and the picker Applies to asks for. */
function WhatCountsEditor({
  draft,
  set,
  options,
  labels,
  fetchProducts,
}: {
  draft: Draft;
  set: (patch: Partial<Draft>) => void;
  options: SalesTargetOptions | undefined;
  labels: Map<string, string>;
  fetchProducts: (query: string) => Promise<SearchableMultiSelectOption[]>;
}) {
  // A picked value missing from the options (an inactive category, say) keeps its name.
  const withPicked = (base: { id: string; label: string }[], picked: string[]): SearchableMultiSelectOption[] => {
    const out = base.map((o) => ({ value: o.id, label: o.label }));
    for (const value of picked) {
      if (!out.some((o) => o.value === value) && labels.has(value)) out.push({ value, label: labels.get(value) as string });
    }
    return out;
  };
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <Field label="Measure" htmlFor="target-measure">
        <SearchableSelect
          id="target-measure"
          value={draft.metric}
          onChange={(v) => v && set({ metric: v as TargetMetric })}
          options={METRIC_OPTIONS}
        />
      </Field>
      <Field label="Counts" htmlFor="target-counts">
        <SearchableSelect
          id="target-counts"
          value={draft.basis}
          onChange={(v) => v && set({ basis: v as TargetBasis })}
          options={COUNTS_OPTIONS}
        />
      </Field>
      <Field label="Applies to" htmlFor="target-applies">
        <SearchableSelect
          id="target-applies"
          value={draft.scope}
          onChange={(v) => v && set({ scope: v as TargetProductScope })}
          options={APPLIES_OPTIONS}
        />
      </Field>
      {draft.scope === 'categories' ? (
        <div className="sm:col-span-3">
          <Field label="Categories" htmlFor="target-categories">
            <SearchableMultiSelect
              id="target-categories"
              value={draft.categoryIds}
              onChange={(ids) => set({ categoryIds: ids })}
              options={withPicked(options?.categories ?? [], draft.categoryIds)}
              placeholder="Pick categories"
              emptyMessage="No categories."
              wrapOptions
            />
          </Field>
        </div>
      ) : null}
      {draft.scope === 'products' ? (
        <div className="sm:col-span-3">
          <Field label="Products" htmlFor="target-products">
            <SearchableMultiSelect
              id="target-products"
              value={draft.productIds}
              onChange={(ids) => set({ productIds: ids })}
              fetchOptions={fetchProducts}
              selectedOptions={draft.productIds.flatMap((value) =>
                labels.has(value) ? [{ value, label: labels.get(value) as string }] : [],
              )}
              placeholder="Search products"
              emptyMessage="No products match."
              wrapOptions
            />
          </Field>
        </div>
      ) : null}
      {draft.scope === 'brands' ? (
        <div className="sm:col-span-3">
          <Field label="Brands" htmlFor="target-brands">
            <SearchableMultiSelect
              id="target-brands"
              value={draft.brandIds}
              onChange={(ids) => set({ brandIds: ids })}
              options={withPicked(options?.brands ?? [], draft.brandIds)}
              placeholder="Pick brands"
              emptyMessage="No brands."
              wrapOptions
            />
          </Field>
        </div>
      ) : null}
    </div>
  );
}

/** A new team target: one figure per agent in the team during the dates, and their sum (T3). */
function NewTeamFigures({
  members,
  hasTeam,
  figures,
  unit,
  perWhat,
  sum,
  onChange,
}: {
  members: { sales_agent_id: string; label: string }[];
  hasTeam: boolean;
  figures: Record<string, string>;
  unit: string;
  perWhat: string;
  sum: number;
  onChange: (agentId: string, value: string) => void;
}) {
  if (!members.length) {
    return (
      <div className="rounded-lg border border-dashed py-8 text-center text-sm font-medium">
        {hasTeam ? 'No agents in this team during these dates' : 'Pick a team first'}
      </div>
    );
  }
  return (
    <>
      <div className="overflow-x-auto rounded-lg border">
        <table aria-label="Agent figures" className="w-full text-sm">
          <thead className="bg-muted/40 text-xs text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-start font-medium">Agent</th>
              <th className="w-44 px-3 py-2 text-end font-medium">{`Figure (${unit}) ${perWhat}`}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {members.map((m) => (
              <tr key={m.sales_agent_id}>
                <td className="max-w-0 truncate px-3 py-1.5" title={m.label}>
                  {m.label}
                </td>
                <td className="px-3 py-1.5">
                  <Input
                    type="number"
                    min={0}
                    step="any"
                    aria-label={`${m.label} figure`}
                    value={figures[m.sales_agent_id] ?? ''}
                    onChange={(e) => onChange(m.sales_agent_id, e.target.value)}
                    className="h-8 text-end"
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between rounded-lg bg-muted/40 px-3 py-2 text-sm">
        <span className="font-medium">Team target</span>
        <span className="tabular-nums">{formatFigure(sum)}</span>
      </div>
    </>
  );
}

/** A figure typed in Edit mode (F1): the metric's unit is named by the column. */
function FigureInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <Input
      type="number"
      min={0}
      step="any"
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="ms-auto h-8 w-28 text-end"
    />
  );
}

/**
 * A saved team target's agents (F1): one row per agent, one column per period, and the team
 * total per period in the footer. Read mode shows the figures; Edit mode swaps each for an
 * input in place (a member with no figure yet included), and the totals follow what is typed.
 */
function TeamFigures({
  target,
  draft,
  unit,
  total,
  onFigure,
  onNewFigure,
}: {
  target: SalesTargetDetail;
  draft: Draft | null;
  unit: string;
  total: (period: SalesTargetDetail['periods'][number]) => number;
  onFigure: (periodId: string, value: string) => void;
  onNewFigure: (agentId: string, periodStart: string, value: string) => void;
}) {
  if (target.children.length === 0 && target.members_without_figure.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed py-8 text-center">
        <span className="text-sm font-medium">No agents in this team during these dates</span>
        <Link href={`/sales/teams/${target.sales_team_id}`} className="text-sm text-primary hover:underline">
          Open the team
        </Link>
      </div>
    );
  }
  const single = target.periods.length === 1;
  const heading = (p: SalesTargetDetail['periods'][number]) => (single ? `Target (${unit})` : shortDate(p.period_start));
  const cell = 'whitespace-nowrap px-3 py-1.5 text-end tabular-nums';
  const agentCell = 'sticky start-0 z-10 max-w-48 truncate bg-background px-3 py-1.5 text-start';
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table aria-label="Agent figures" className="w-full text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr>
            <th className="sticky start-0 z-10 bg-muted px-3 py-2 text-start font-medium">Agent</th>
            {target.periods.map((p) => (
              <th key={p.id} className="whitespace-nowrap px-3 py-2 text-end font-medium">
                {heading(p)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {target.children.map((child) => (
            <tr key={child.target_id}>
              <td className={agentCell} title={child.label}>
                <Link href={`/sales/targets/${child.target_id}`} className="text-primary hover:underline">
                  {child.label}
                </Link>
              </td>
              {target.periods.map((p) => {
                const cp = child.periods.find((x) => x.period_start === p.period_start);
                return (
                  <td key={p.id} className={cell}>
                    {draft && cp?.id ? (
                      <FigureInput
                        label={`${child.label}, ${shortDate(p.period_start)}`}
                        value={draft.figures[cp.id] ?? ''}
                        onChange={(value) => onFigure(cp.id as string, value)}
                      />
                    ) : (
                      formatFigure(cp?.target_value ?? null)
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
          {target.members_without_figure.map((m) => (
            <tr key={m.sales_agent_id}>
              <td className={agentCell} title={m.label}>
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate">{m.label}</span>
                  {draft ? null : <span className="shrink-0 text-xs text-muted-foreground">No figure yet</span>}
                </span>
              </td>
              {target.periods.map((p) => (
                <td key={p.id} className={cell}>
                  {draft ? (
                    <FigureInput
                      label={`${m.label}, ${shortDate(p.period_start)}`}
                      value={draft.newAgentFigures[m.sales_agent_id]?.[p.period_start] ?? ''}
                      onChange={(value) => onNewFigure(m.sales_agent_id, p.period_start, value)}
                    />
                  ) : (
                    '-'
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        <tfoot data-testid="team-totals" className="border-t bg-muted/40 font-medium">
          <tr>
            <th scope="row" className="sticky start-0 z-10 bg-muted px-3 py-2 text-start font-medium">
              Team target
            </th>
            {target.periods.map((p) => (
              <td key={p.id} className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                {formatFigure(total(p))}
              </td>
            ))}
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

/**
 * An agent target's Team target tab (F3): the team target it is one line of, read-only, with
 * what counts, its dates and split, its periods with their figures, and a way to open it.
 */
function ParentTarget({ parentId, today }: { parentId: string; today: string }) {
  const { data: parent, isLoading } = useSalesTarget(parentId);
  return (
    <Card>
      <section aria-label="Team target" className="flex flex-col gap-4 p-5">
        {isLoading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : !parent ? (
          <div className="rounded-lg border border-dashed py-8 text-center text-sm font-medium">
            Team target not found
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex min-w-0 flex-col gap-1">
                <span className="text-xs font-medium text-muted-foreground">{parent.target_no}</span>
                <h3 className="truncate text-base font-semibold" title={parent.name}>
                  {parent.name}
                </h3>
                <span className="truncate text-xs text-muted-foreground">{`For ${parent.subject_label}`}</span>
              </div>
              <Button asChild variant="outline" size="sm" className="shrink-0">
                <Link href={`/sales/targets/${parent.id}`}>Open team target</Link>
              </Button>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Field label="Measure">{`${METRIC_LABEL[parent.metric]} (${unitOf(parent.metric)})`}</Field>
              <Field label="Counts">{parent.counts_label}</Field>
              <Field label="Applies to">
                {parent.product_scope === 'all'
                  ? SCOPE_LABEL.all
                  : `${SCOPE_LABEL[parent.product_scope]}: ${scopeSummary(parent.product_scope, parent.scope.length)}`}
              </Field>
              <div className="sm:col-span-2">
                <Field label="Start and end date">{`${shortDate(parent.start_date)} to ${shortDate(parent.end_date)}`}</Field>
              </div>
              <Field label="Split">{splitSummary(parent.split_every, parent.split_unit)}</Field>
            </div>
            <PeriodsTable unit={unitOf(parent.metric)}>
              {parent.periods.map((p) => {
                const future = p.period_start > today;
                return (
                  <tr key={p.id} className={p.is_current ? 'bg-primary/5' : undefined}>
                    <td className="whitespace-nowrap px-3 py-2">{`${shortDate(p.period_start)} to ${shortDate(p.period_end)}`}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">{formatFigure(p.target_value)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                      {future ? '-' : formatFigure(p.achieved_value)}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                      {future ? '-' : formatPct(p.achieved_pct)}
                    </td>
                  </tr>
                );
              })}
            </PeriodsTable>
          </>
        )}
      </section>
    </Card>
  );
}

/**
 * The Commission tab (plan 3.3; F2): read mode shows how the tiers pay and the tiers as rows;
 * Edit mode swaps them for inputs in place, with Add tier and a remove per row. Nothing on
 * this tab writes on its own: Save in the header sends the tiers with everything else.
 */
/** The counted accounts as rate columns, in Edit from the draft, else from the target. */
function accountColumns(
  target: SalesTargetDetail | undefined,
  draft: Draft | null,
  labels: Map<string, string>,
): { id: string; label: string }[] {
  if ((draft?.kind ?? target?.subject_kind) !== 'dealer') return [];
  const byId = new Map((target?.accounts ?? []).map((a) => [a.customer_id, a.label]));
  const ids = draft ? draft.accountIds : (target?.accounts ?? []).map((a) => a.customer_id);
  return ids.map((id) => ({ id, label: byId.get(id) ?? (labels.get(id) ?? '').split(' - ')[0] ?? '' }));
}

/** A dealer target's Accounts tab: the counted accounts with this period's sales, and the
 * account paid (owner ruling 16 Oct: it may be one the target does not count). */
function DealerAccounts({
  target,
  draft,
  labels,
  fetchCustomers,
  set,
}: {
  target: SalesTargetDetail | undefined;
  draft: Draft | null;
  labels: Map<string, string>;
  fetchCustomers: (query: string) => Promise<{ value: string; label: string }[]>;
  set: (patch: Partial<Draft>) => void;
}) {
  const current = target?.periods.find((p) => p.is_current) ?? target?.periods[target.periods.length - 1];
  const achieved = new Map((current?.accounts ?? []).map((a) => [a.customer_id, a.achieved_value]));
  const accounts: TargetAccount[] = draft
    ? draft.accountIds.map((id) => {
        const known = target?.accounts?.find((a) => a.customer_id === id);
        const [code, ...name] = (labels.get(id) ?? '').split(' - ');
        return known ?? { customer_id: id, code, name: name.join(' - '), label: name.join(' - ') };
      })
    : (target?.accounts ?? []);
  const period = current ? `${shortDate(current.period_start)} to ${shortDate(current.period_end)}` : '';
  return (
    <>
      {accounts.length ? (
        <div className="overflow-x-auto rounded-lg border">
          <table aria-label="Counted accounts" className="w-full min-w-[32rem] text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-start font-medium">Code</th>
                <th className="px-3 py-2 text-start font-medium">Customer</th>
                <th className="px-3 py-2 text-start font-medium">Account</th>
                {current && !draft ? <th className="px-3 py-2 text-end font-medium">{`Sales, ${period}`}</th> : null}
              </tr>
            </thead>
            <tbody className="divide-y">
              {accounts.map((a) => (
                <tr key={a.customer_id}>
                  <td className="whitespace-nowrap px-3 py-2">{a.code}</td>
                  <td className="max-w-[16rem] truncate px-3 py-2" title={a.name}>
                    {a.name}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2">{a.label}</td>
                  {current && !draft ? (
                    <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                      {formatFigure(achieved.get(a.customer_id) ?? 0)}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex w-full flex-col items-center gap-1 rounded-lg border border-dashed py-8 text-center">
          <span className="text-sm font-medium">No accounts yet</span>
          <span className="text-xs text-muted-foreground">Pick them under Details.</span>
        </div>
      )}
      <div className="sm:w-96">
        <Field label="Pays to" htmlFor={draft ? 'target-pays-to' : undefined}>
          {draft ? (
            <SearchableSelect
              id="target-pays-to"
              value={draft.payoutId}
              onChange={(v) => set({ payoutId: v })}
              fetchOptions={(query) => fetchCustomers(query)}
              selectedOption={
                draft.payoutId ? { value: draft.payoutId, label: labels.get(draft.payoutId) ?? '' } : undefined
              }
              placeholder="The first counted account"
              clearable
              wrapOptions
            />
          ) : target?.payout_account ? (
            `${target.payout_account.code} - ${target.payout_account.name} (${target.payout_account.label})`
          ) : (
            '-'
          )}
        </Field>
      </div>
    </>
  );
}

function CommissionTiers({
  target,
  draft,
  metric,
  problem,
  canEdit,
  set,
  onStartAdding,
  periodStarts,
  accounts,
}: {
  target: SalesTargetDetail | undefined;
  draft: Draft | null;
  metric: TargetMetric;
  problem: string;
  canEdit: boolean;
  set: (patch: Partial<Draft>) => void;
  onStartAdding: () => void;
  periodStarts: { start: string; end: string }[];
  accounts: { id: string; label: string }[];
}) {
  const rateUnit = metric === 'quantity' ? 'RM per unit' : '% of RM';
  const dealer = (draft?.kind ?? target?.subject_kind) === 'dealer';
  const periodLabel = (start: string) => {
    const p = periodStarts.find((x) => x.start === start);
    return p ? `${shortDate(p.start)} to ${shortDate(p.end)}` : start ? shortDate(start) : '-';
  };
  const empty = (action: ReactNode) => (
    <div className="flex w-full flex-col items-center gap-3 rounded-lg border border-dashed py-8 text-center">
      <span className="text-sm font-medium">No commission</span>
      {action}
    </div>
  );

  if (!draft) {
    const tiers = target?.tiers ?? [];
    if (!tiers.length) {
      return empty(
        canEdit && target ? (
          <Button variant="outline" size="sm" onClick={onStartAdding}>
            <Plus className="size-4" />
            Add tier
          </Button>
        ) : null,
      );
    }
    const unit = target?.threshold_unit ?? 'pct';
    const perPeriod = !!target?.tiers_per_period;
    const rateOf = (t: CommissionTier, accountId?: string) => {
      if (!t.account_rates) return formatFigure(t.rate);
      const r = t.account_rates.find((x) => x.customer_id === accountId);
      return r ? formatFigure(r.rate) : '-';
    };
    return (
      <>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
          <Field label="How tiers pay">{METHOD_LABEL[target?.commission_method ?? 'none']}</Field>
          <Field label="Threshold in">{THRESHOLD_LABEL[unit]}</Field>
          <Field label="Round to">{ROUNDING_LABEL[target?.rounding ?? 'sen']}</Field>
          <Field label="Tiers">{perPeriod ? 'Own tiers per period' : 'Same every period'}</Field>
        </div>
        <div className="overflow-x-auto rounded-lg border">
          <table aria-label="Commission tiers" className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                {perPeriod ? <th className="px-3 py-2 text-start font-medium">Period</th> : null}
                <th className="px-3 py-2 text-start font-medium">{`From (${THRESHOLD_LABEL[unit]})`}</th>
                {dealer ? (
                  accounts.map((a) => (
                    <th key={a.id} className="px-3 py-2 text-end font-medium">{`${a.label} rate`}</th>
                  ))
                ) : (
                  <th className="px-3 py-2 text-end font-medium">{`Rate (${rateUnit})`}</th>
                )}
                <th className="px-3 py-2 text-end font-medium">Bonus (RM)</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {tiers.map((t, i) => (
                <tr key={`${t.period_start ?? ''}-${i}`}>
                  {perPeriod ? (
                    <td className="whitespace-nowrap px-3 py-2">{periodLabel(t.period_start ?? '')}</td>
                  ) : null}
                  <td className="whitespace-nowrap px-3 py-2">
                    {t.is_additional ? '+ Additional from ' : ''}
                    {unit === 'pct' ? `${formatFigure(t.from_pct ?? null)}%` : formatFigure(t.threshold ?? null)}
                  </td>
                  {dealer ? (
                    accounts.map((a) => (
                      <td key={a.id} className="whitespace-nowrap px-3 py-2 text-end tabular-nums">
                        {rateOf(t, a.id)}
                      </td>
                    ))
                  ) : (
                    <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">{formatFigure(t.rate)}</td>
                  )}
                  <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">{formatFigure(t.bonus_amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    );
  }

  const setTier = (key: number, patch: Partial<TierDraft>) =>
    set({ tiers: draft.tiers.map((t) => (t.key === key ? { ...t, ...patch } : t)) });
  const addTier = () =>
    set({
      tiers: [
        ...draft.tiers,
        {
          ...tierDraft(undefined, draft.tiers.length || draft.thresholdUnit !== 'pct' ? '' : '0'),
          // A new row starts in the last row's period, the common case when typing Q1's tiers.
          period: draft.tiers[draft.tiers.length - 1]?.period ?? periodStarts[0]?.start ?? '',
        },
      ],
      // R5: "Higher rate above each threshold only" is the default once tiers are added; a
      // dealer's scheme pays the highest tier reached (owner, 16 Oct).
      method: draft.method === 'none' ? (dealer ? 'retroactive' : 'marginal') : draft.method,
    });
  const removeTier = (key: number) => {
    const tiers = draft.tiers.filter((t) => t.key !== key);
    set({ tiers, method: tiers.length ? draft.method : 'none' });
  };
  const addButton = (
    <Button variant="outline" size="sm" onClick={addTier}>
      <Plus className="size-4" />
      Add tier
    </Button>
  );
  const settings = (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
      <Field label="How tiers pay" htmlFor="target-method">
        <SearchableSelect
          id="target-method"
          value={draft.method}
          onChange={(v) => set({ method: (v as CommissionMethod) || 'none' })}
          options={METHOD_OPTIONS}
          wrapOptions
        />
      </Field>
      <Field label="Threshold in" htmlFor="target-threshold-unit">
        <SearchableSelect
          id="target-threshold-unit"
          value={draft.thresholdUnit}
          onChange={(v) => set({ thresholdUnit: (v as ThresholdUnit) || 'pct' })}
          options={THRESHOLD_OPTIONS}
        />
      </Field>
      <Field label="Round to" htmlFor="target-rounding">
        <SearchableSelect
          id="target-rounding"
          value={draft.rounding}
          onChange={(v) => set({ rounding: (v as Rounding) || 'sen' })}
          options={ROUNDING_OPTIONS}
        />
      </Field>
      <Field label="Own tiers per period">
        <Switch
          aria-label="Own tiers per period"
          checked={draft.perPeriod}
          onCheckedChange={(on) =>
            set({
              perPeriod: on,
              tiers: draft.tiers.map((t) => ({ ...t, period: on ? t.period || periodStarts[0]?.start || '' : '' })),
            })
          }
        />
      </Field>
    </div>
  );
  if (!draft.tiers.length) {
    return (
      <>
        {settings}
        {empty(addButton)}
      </>
    );
  }
  const fromLabel = draft.thresholdUnit === 'pct' ? 'From (%)' : `From (${THRESHOLD_LABEL[draft.thresholdUnit]})`;
  const periodOptions = periodStarts.map((p) => ({ value: p.start, label: periodLabel(p.start) }));
  return (
    <>
      {settings}
      {/* `relative`: the sr-only Remove header is absolutely placed, and must stay inside this
          scroll box or it widens the page at 375. */}
      <div className="relative overflow-x-auto rounded-lg border">
        <table aria-label="Commission tiers" className="w-full min-w-[36rem] text-sm">
          <thead className="bg-muted/40 text-xs text-muted-foreground">
            <tr>
              {draft.perPeriod ? <th className="px-3 py-2 text-start font-medium">Period</th> : null}
              <th className="px-3 py-2 text-start font-medium">Type</th>
              <th className="px-3 py-2 text-start font-medium">{fromLabel}</th>
              {dealer ? (
                accounts.map((a) => (
                  <th key={a.id} className="px-3 py-2 text-end font-medium">{`${a.label} rate`}</th>
                ))
              ) : (
                <th className="px-3 py-2 text-end font-medium">{`Rate (${rateUnit})`}</th>
              )}
              <th className="px-3 py-2 text-end font-medium">Bonus (RM)</th>
              <th className="w-10 px-3 py-2">
                <span className="sr-only">Remove</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {draft.tiers.map((t, i) => (
              <tr key={t.key}>
                {draft.perPeriod ? (
                  <td className="px-3 py-1.5">
                    <SearchableSelect
                      value={t.period}
                      onChange={(v) => setTier(t.key, { period: v })}
                      options={periodOptions}
                      placeholder="Period"
                      triggerClassName="h-8 w-44"
                      aria-label={`Tier ${i + 1} period`}
                    />
                  </td>
                ) : null}
                <td className="px-3 py-1.5">
                  <SearchableSelect
                    value={t.additional ? 'additional' : 'main'}
                    onChange={(v) => setTier(t.key, { additional: v === 'additional' })}
                    options={TIER_TYPE_OPTIONS}
                    triggerClassName="h-8 w-36"
                    aria-label={`Tier ${i + 1} type`}
                  />
                </td>
                <td className="px-3 py-1.5">
                  <Input
                    type="number"
                    min={0}
                    step="any"
                    aria-label={
                      draft.thresholdUnit === 'pct'
                        ? `Tier ${i + 1} from %`
                        : `Tier ${i + 1} from (${THRESHOLD_LABEL[draft.thresholdUnit]})`
                    }
                    value={t.from}
                    onChange={(e) => setTier(t.key, { from: e.target.value })}
                    className="h-8 w-28"
                  />
                </td>
                {dealer ? (
                  accounts.map((a) => (
                    <td key={a.id} className="px-3 py-1.5">
                      <Input
                        type="number"
                        min={0}
                        step="any"
                        aria-label={`Tier ${i + 1} ${a.label} rate`}
                        value={t.accountRates[a.id] ?? ''}
                        onChange={(e) => setTier(t.key, { accountRates: { ...t.accountRates, [a.id]: e.target.value } })}
                        className="ms-auto h-8 w-24 text-end"
                      />
                    </td>
                  ))
                ) : (
                  <td className="px-3 py-1.5">
                    <Input
                      type="number"
                      min={0}
                      step="any"
                      aria-label={`Tier ${i + 1} rate (${rateUnit})`}
                      value={t.rate}
                      onChange={(e) => setTier(t.key, { rate: e.target.value })}
                      className="ms-auto h-8 w-28 text-end"
                    />
                  </td>
                )}
                <td className="px-3 py-1.5">
                  <Input
                    type="number"
                    min={0}
                    step="any"
                    aria-label={`Tier ${i + 1} bonus (RM)`}
                    value={t.bonus}
                    onChange={(e) => setTier(t.key, { bonus: e.target.value })}
                    className="ms-auto h-8 w-28 text-end"
                  />
                </td>
                <td className="px-3 py-1.5 text-end">
                  <Button
                    variant="ghost"
                    size="sm"
                    mode="icon"
                    aria-label={`Remove tier ${i + 1}`}
                    onClick={() => removeTier(t.key)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {problem ? <p className="text-sm text-destructive">{problem}</p> : null}
      <div>{addButton}</div>
    </>
  );
}

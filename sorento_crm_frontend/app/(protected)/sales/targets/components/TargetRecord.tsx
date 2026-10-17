'use client';

import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
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
import { NumberInput } from '@/components/ui/number-input';
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
import { numberFieldError } from '@/lib/number-field';
import {
  useCreateSalesTarget,
  usePatchSalesTarget,
  useSalesTarget,
  useSalesTargetOptions,
  useSalesTargets,
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

type RecordTab = 'details' | 'periods' | 'agents' | 'team' | 'commission';

/** One commission tier as typed (plan 3.3); `key` keeps a row's inputs stable on remove. */
interface TierDraft {
  key: number;
  from: string;
  rate: string;
  bonus: string;
}

let nextTierKey = 0;
function tierDraft(tier?: CommissionTier, from = ''): TierDraft {
  nextTierKey += 1;
  return {
    key: nextTierKey,
    from: tier ? String(tier.from_pct) : from,
    rate: tier ? String(tier.rate) : '',
    bonus: tier?.bonus_amount === null || tier?.bonus_amount === undefined ? '' : String(tier.bonus_amount),
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
  };
}

function idsOf(target: SalesTargetDetail, kind: 'category' | 'product' | 'brand'): string[] {
  return target.scope.filter((s) => s.kind === kind).map((s) => s.id);
}

function draftOf(target: SalesTargetDetail): Draft {
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
    tiers: (target.tiers ?? []).map((t) => tierDraft(t)),
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

function tiersOf(draft: Draft): CommissionTier[] {
  return draft.tiers.map((t) => ({
    from_pct: Number(t.from),
    rate: Number(t.rate),
    bonus_amount: t.bonus.trim() === '' ? null : Number(t.bonus),
  }));
}

/** Why the tiers cannot be saved yet, or `''`. */
function tierProblem(draft: Draft): string {
  const bad = (text: string, required: boolean) =>
    text.trim() === '' ? required : !Number.isFinite(Number(text)) || Number(text) < 0;
  if (draft.tiers.some((t) => bad(t.from, true) || bad(t.rate, true) || bad(t.bonus, false))) {
    return 'Type a from % and a rate for every tier.';
  }
  const froms = draft.tiers.map((t) => Number(t.from));
  if (new Set(froms).size !== froms.length) return 'Two tiers start at the same %.';
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
    JSON.stringify(tiers) !== JSON.stringify(target.tiers ?? [])
  ) {
    out.commission_method = draft.method;
    out.tiers = tiers;
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

// The rules each typed number is held to when the target is saved.
const FIGURE_RULES = { min: 0 };
const NEW_FIGURE_RULES = { min: 0, optional: true };
const SPLIT_RULES = { min: 1, max: 99, integer: true };

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
  // Set once Save found a bad number: each field's sentence then follows its value live.
  const [showErrors, setShowErrors] = useState(false);

  // Every name a picked category, product or brand is known by (F5).
  const labels = useRef(new Map<string, string>());
  for (const item of target?.scope ?? []) labels.current.set(item.id, item.label);
  for (const c of options?.categories ?? []) labels.current.set(c.id, c.label);
  for (const b of options?.brands ?? []) labels.current.set(b.id, b.label);
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
  const isChild = !!target?.parent;
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
    (!isCreate ||
      (!!draft.kind && !!draft.subjectId && (isTeam ? members.length > 0 : true))) &&
    !saving;

  // Only the final values are checked, on Save: a blank or bad figure is told, never read as 0
  // or dropped.
  const numberProblem =
    !!draft &&
    ((draft.split && !!numberFieldError(draft.every, SPLIT_RULES)) ||
      (isCreate
        ? isTeam
          ? members.some((m) => !!numberFieldError(draft.agentFigures[m.sales_agent_id] ?? '', FIGURE_RULES))
          : !!numberFieldError(draft.figure, FIGURE_RULES)
        : Object.values(draft.figures).some((v) => !!numberFieldError(v, FIGURE_RULES)) ||
          Object.values(draft.newAgentFigures).some((byPeriod) =>
            Object.values(byPeriod).some((v) => !!numberFieldError(v, NEW_FIGURE_RULES)),
          )));

  const save = async () => {
    if (!draft || !canSave) return;
    if (numberProblem) {
      setShowErrors(true);
      return;
    }
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
      ...(draft.tiers.length ? { commission_method: draft.method, tiers: tiersOf(draft) } : {}),
    };
    const payload: SalesTargetCreatePayload =
      draft.kind === 'agent'
        ? { subject_kind: 'agent', sales_agent_id: draft.subjectId, target_value: toNumber(draft.figure), ...common }
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
  const cancel = () => {
    setShowErrors(false);
    if (isCreate) router.push('/sales/targets');
    else setDraft(null);
  };

  const tabs: { value: RecordTab; label: string; icon: typeof Info }[] = [
    { value: 'details', label: 'Details', icon: Info },
    { value: 'periods', label: 'Periods', icon: CalendarRange },
    ...(isTeam ? [{ value: 'agents' as const, label: 'Agents', icon: UsersRound }] : []),
    ...(isChild ? [{ value: 'team' as const, label: 'Team target', icon: TargetIcon }] : []),
    { value: 'commission', label: 'Commission', icon: CircleDollarSign },
  ];

  const actions: RecordAction[] =
    target && canEdit
      ? [{ key: 'sales_target.edit', label: 'Edit', icon: SquarePen, run: () => { setShowErrors(false); setDraft(draftOf(target)); } }, ...targetActions]
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
      method: base.method === 'none' ? 'marginal' : base.method,
      tiers: [...base.tiers, tierDraft(undefined, base.tiers.length ? '' : '0')],
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
                    onChange={(v) => set({ kind: (v as TargetSubjectKind) || '', subjectId: '', agentFigures: {} })}
                    options={KIND_OPTIONS}
                    placeholder="Pick one"
                    disabled={!!preset?.subjectId}
                  />
                ) : isTeam ? (
                  'Team'
                ) : (
                  'Agent'
                )}
              </Field>
              <Field label="Who" htmlFor={isCreate ? 'target-who' : undefined}>
                {isCreate && draft ? (
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
                          <NumberInput
                            id="target-split-every"
                            min={1}
                            max={99}
                            value={draft.every}
                            error={showErrors ? numberFieldError(draft.every, SPLIT_RULES) : undefined}
                            errorClassName="basis-full"
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
            {isCreate && draft && !isTeam ? (
              <Field label={`Target figure (${unit}) ${split ? 'per period' : 'for the whole range'}`} htmlFor="target-figure">
                <NumberInput
                  id="target-figure"
                  min={0}
                  step="any"
                  value={draft.figure}
                  error={showErrors ? numberFieldError(draft.figure, FIGURE_RULES) : undefined}
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
                <PeriodsTable unit={unit} commission={target.commission_method !== 'none'}>
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
                          {editing && draft && !isTeam ? (
                            <FigureInput
                              label={`Figure, ${periodLabel}`}
                              value={draft.figures[p.id] ?? ''}
                              error={showErrors ? numberFieldError(draft.figures[p.id] ?? '', FIGURE_RULES) : undefined}
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
                    showErrors={showErrors}
                    onChange={(agentId, value) => set({ agentFigures: { ...draft.agentFigures, [agentId]: value } })}
                  />
                ) : target ? (
                  <TeamFigures
                    target={target}
                    draft={draft}
                    unit={unit}
                    showErrors={showErrors}
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
  const subjectHref = isTeam
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
  children,
}: {
  unit: string;
  commission?: boolean;
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
  showErrors,
  onChange,
}: {
  members: { sales_agent_id: string; label: string }[];
  hasTeam: boolean;
  figures: Record<string, string>;
  unit: string;
  perWhat: string;
  sum: number;
  showErrors: boolean;
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
                  <NumberInput
                    min={0}
                    step="any"
                    aria-label={`${m.label} figure`}
                    value={figures[m.sales_agent_id] ?? ''}
                    error={showErrors ? numberFieldError(figures[m.sales_agent_id] ?? '', FIGURE_RULES) : undefined}
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
function FigureInput({
  label,
  value,
  error,
  onChange,
}: {
  label: string;
  value: string;
  error?: string;
  onChange: (value: string) => void;
}) {
  return (
    <NumberInput
      min={0}
      step="any"
      aria-label={label}
      value={value}
      error={error}
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
  showErrors,
}: {
  target: SalesTargetDetail;
  draft: Draft | null;
  unit: string;
  total: (period: SalesTargetDetail['periods'][number]) => number;
  onFigure: (periodId: string, value: string) => void;
  onNewFigure: (agentId: string, periodStart: string, value: string) => void;
  showErrors: boolean;
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
                        error={showErrors ? numberFieldError(draft.figures[cp.id] ?? '', FIGURE_RULES) : undefined}
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
                      error={
                        showErrors
                          ? numberFieldError(
                              draft.newAgentFigures[m.sales_agent_id]?.[p.period_start] ?? '',
                              NEW_FIGURE_RULES,
                            )
                          : undefined
                      }
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
function CommissionTiers({
  target,
  draft,
  metric,
  problem,
  canEdit,
  set,
  onStartAdding,
}: {
  target: SalesTargetDetail | undefined;
  draft: Draft | null;
  metric: TargetMetric;
  problem: string;
  canEdit: boolean;
  set: (patch: Partial<Draft>) => void;
  onStartAdding: () => void;
}) {
  const rateUnit = metric === 'quantity' ? 'RM per unit' : '% of RM';
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
    return (
      <>
        <Field label="How tiers pay">{METHOD_LABEL[target?.commission_method ?? 'none']}</Field>
        <div className="overflow-x-auto rounded-lg border">
          <table aria-label="Commission tiers" className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-start font-medium">From</th>
                <th className="px-3 py-2 text-end font-medium">{`Rate (${rateUnit})`}</th>
                <th className="px-3 py-2 text-end font-medium">Bonus (RM)</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {tiers.map((t) => (
                <tr key={t.from_pct}>
                  <td className="whitespace-nowrap px-3 py-2">{`${formatFigure(t.from_pct)}%`}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-end tabular-nums">{formatFigure(t.rate)}</td>
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
      tiers: [...draft.tiers, tierDraft(undefined, draft.tiers.length ? '' : '0')],
      // R5: "Higher rate above each threshold only" is the default once tiers are added.
      method: draft.method === 'none' ? 'marginal' : draft.method,
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
  if (!draft.tiers.length) return empty(addButton);
  return (
    <>
      <div className="sm:w-80">
        <Field label="How tiers pay" htmlFor="target-method">
          <SearchableSelect
            id="target-method"
            value={draft.method}
            onChange={(v) => set({ method: (v as CommissionMethod) || 'none' })}
            options={METHOD_OPTIONS}
            wrapOptions
          />
        </Field>
      </div>
      {/* `relative`: the sr-only Remove header is absolutely placed, and must stay inside this
          scroll box or it widens the page at 375. */}
      <div className="relative overflow-x-auto rounded-lg border">
        <table aria-label="Commission tiers" className="w-full min-w-[28rem] text-sm">
          <thead className="bg-muted/40 text-xs text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-start font-medium">From (%)</th>
              <th className="px-3 py-2 text-end font-medium">{`Rate (${rateUnit})`}</th>
              <th className="px-3 py-2 text-end font-medium">Bonus (RM)</th>
              <th className="w-10 px-3 py-2">
                <span className="sr-only">Remove</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {draft.tiers.map((t, i) => (
              <tr key={t.key}>
                <td className="px-3 py-1.5">
                  <NumberInput
                    min={0}
                    step="any"
                    aria-label={`Tier ${i + 1} from %`}
                    value={t.from}
                    onChange={(e) => setTier(t.key, { from: e.target.value })}
                    className="h-8 w-24"
                  />
                </td>
                <td className="px-3 py-1.5">
                  <NumberInput
                    min={0}
                    step="any"
                    aria-label={`Tier ${i + 1} rate (${rateUnit})`}
                    value={t.rate}
                    onChange={(e) => setTier(t.key, { rate: e.target.value })}
                    className="ms-auto h-8 w-28 text-end"
                  />
                </td>
                <td className="px-3 py-1.5">
                  <NumberInput
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

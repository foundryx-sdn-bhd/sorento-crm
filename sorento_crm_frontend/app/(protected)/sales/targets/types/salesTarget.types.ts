/**
 * Sales targets as `/api/v1/sales/targets` serialises them (plan 3.1, 3.8, 16.3). Dates are
 * `YYYY-MM-DD` strings, datetimes naive UTC, money and units plain JSON numbers.
 */
export type TargetSubjectKind = 'agent' | 'team' | 'dealer';
export type TargetMetric = 'amount' | 'quantity';
export type TargetBasis = 'ordered' | 'delivered';
export type TargetProductScope = 'all' | 'categories' | 'products' | 'brands';
export type TargetScopeKind = 'category' | 'product' | 'brand';
export type TargetSplitUnit = 'day' | 'week' | 'month';
/** How tiers pay (plan 3.3): `marginal` is "Higher rate above each threshold only", `retroactive`
 * is "Highest rate on everything". */
export type CommissionMethod = 'none' | 'marginal' | 'retroactive';
/** What a tier's threshold is in: % of the period's figure, RM (units), or points. */
export type ThresholdUnit = 'pct' | 'amount' | 'points';
export type Rounding = 'sen' | 'ringgit';

export interface TierAccountRate {
  customer_id: string;
  rate: number;
}

export interface CommissionTier {
  /** `pct` targets: the achievement % the tier starts at. */
  from_pct?: number | null;
  /** `amount` / `points` targets: the threshold in RM (units) or points. */
  threshold?: number | null;
  /** Amount target: % of the achieved amount. Quantity target: RM per unit. Null with rates per account. */
  rate: number | null;
  /** RM paid once per period when the tier is reached. */
  bonus_amount: number | null;
  /** A "+ Additional" line, paid on top of the main tier (they stack). */
  is_additional?: boolean;
  /** Tiers per period: the start of the period this tier belongs to. */
  period_start?: string | null;
  /** Dealer targets: only these accounts earn on the tier, each at its rate. */
  account_rates?: TierAccountRate[] | null;
}

export interface TargetAccount {
  customer_id: string;
  code: string;
  name: string;
  /** "A/C I" for a numbered account, else the customer's name. */
  label: string;
  achieved_value?: number | null;
}

export interface TargetMemberRef {
  sales_agent_id: string;
  /** `ALI - Ali Hassan`: code and name, never an id. */
  label: string;
}

/** One target period containing the list's date, or a "No target" row (target fields null). */
export interface SalesTargetRow {
  target_id: string | null;
  target_no: string | null;
  name: string | null;
  subject_kind: TargetSubjectKind;
  sales_agent_id: string | null;
  sales_team_id: string | null;
  subject_label: string;
  /** Agent rows: the team the agent is in on the date. Team rows: the team. */
  team_id: string | null;
  team_name: string | null;
  /** Agent rows under a team filter: the last day of a stay that ended on or before the date. */
  left_on: string | null;
  /** Team rows: the members on the date. Null on agent rows. */
  members: TargetMemberRef[] | null;
  metric: TargetMetric | null;
  basis: TargetBasis | null;
  product_scope: TargetProductScope | null;
  scope_labels: string[];
  period_id: string | null;
  period_start: string | null;
  period_end: string | null;
  /** The whole list (`all`): the target's own first day; `end_date` is its last. */
  start_date?: string | null;
  end_date?: string | null;
  target_value: number | null;
  achieved_value: number | null;
  achieved_pct: number | null;
  parent_target_id: string | null;
  /** Dealer rows: the counted accounts' labels and codes, and the paid account's label. */
  account_labels?: string[] | null;
  account_codes?: string[] | null;
  payout_label?: string | null;
  threshold_unit?: ThresholdUnit | null;
  /** The current period's commission; null with no tiers. */
  commission_earned?: number | null;
}

export interface SalesTargetList {
  on: string;
  rows: SalesTargetRow[];
  /** Ordered amount with no agent, in the calendar month of `on`. */
  unassigned_amount: number;
  /** Active agents in no team on `on`. */
  no_team_count: number;
}

export interface SalesTargetListParams {
  /** The date whose periods are listed. Ignored with `all`. */
  on?: string;
  /** Every target of the kind, one row each, whole-range figures (Targets > Teams / Agents). */
  all?: boolean;
  subject: TargetSubjectKind;
  /** A team id, or `none` (agents only) for agents in no team. */
  salesTeamId?: string;
  query?: string;
}

export interface SalesTargetPeriod {
  id: string;
  period_start: string;
  period_end: string;
  target_value: number;
  achieved_value: number | null;
  achieved_pct: number | null;
  is_current: boolean;
  /** Null when the target has no tiers. */
  commission_earned?: number | null;
  bonus_earned?: number | null;
  commission_lines?: { kind: 'main' | 'additional'; threshold_rm: number | null; amount: number }[];
  tier_reached?: number | null;
  /** Points targets: RM per point in force on the period's last day. */
  point_ratio?: number | null;
  ratio_missing?: boolean;
  /** Dealer targets: each counted account's achievement in the period. */
  accounts?: TargetAccount[];
  /** The dealer target in `replaced_by` reached a tier in this period: this one pays nothing. */
  replaced?: boolean;
}

export interface SalesTargetChild {
  target_id: string;
  target_no?: string;
  sales_agent_id: string;
  label: string;
  periods: { id?: string; period_start: string; target_value: number }[];
}

export interface SalesTargetDetail {
  id: string;
  target_no: string;
  name: string;
  subject_kind: TargetSubjectKind;
  sales_agent_id: string | null;
  sales_team_id: string | null;
  subject_label: string;
  /** Agent targets: the team the agent is in today. */
  subject_team_name: string | null;
  parent: { id: string; name: string; target_no?: string } | null;
  metric: TargetMetric;
  basis: TargetBasis;
  product_scope: TargetProductScope;
  start_date: string;
  end_date: string;
  split_every: number | null;
  split_unit: TargetSplitUnit | null;
  /** "Ordered", "Delivered", or "Delivered (by DO date)". */
  counts_label: string;
  /** The categories, products or brands counted: their id, kind and "CODE - Name" label. */
  scope: { id: string; kind: TargetScopeKind; label: string }[];
  periods: SalesTargetPeriod[];
  children: SalesTargetChild[];
  members_without_figure: TargetMemberRef[];
  child_count: number;
  commission_method: CommissionMethod;
  threshold_unit?: ThresholdUnit;
  rounding?: Rounding;
  tiers_per_period?: boolean;
  tiers: CommissionTier[];
  /** Dealer targets: the counted accounts and the paid one. */
  accounts?: TargetAccount[];
  payout_account?: TargetAccount | null;
  /** Dealer targets: the dealer target that replaces this one once it reaches a tier. */
  replaced_by?: { target_id: string; target_no: string; name: string } | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SalesTargetOptions {
  agents: { id: string; code: string; label: string; team_id: string | null; team_name: string | null }[];
  teams: {
    id: string;
    name: string;
    is_active: boolean;
    /** Every stay, so the modal can offer the members overlapping the chosen range. */
    members: { sales_agent_id: string; label: string; valid_from: string | null; valid_to: string | null }[];
  }[];
  categories: { id: string; label: string; parent_category_id: string | null }[];
  brands: { id: string; label: string }[];
}

interface TargetCreateBase {
  name: string;
  metric: TargetMetric;
  basis: TargetBasis;
  product_scope: TargetProductScope;
  category_ids?: string[];
  product_ids?: string[];
  brand_ids?: string[];
  start_date: string;
  end_date: string;
  split_every?: number;
  split_unit?: TargetSplitUnit;
  commission_method?: CommissionMethod;
  threshold_unit?: ThresholdUnit;
  rounding?: Rounding;
  tiers_per_period?: boolean;
  tiers?: CommissionTier[];
}

export interface AgentTargetCreatePayload extends TargetCreateBase {
  subject_kind: 'agent';
  sales_agent_id: string;
  /** Written to every period; left out when the thresholds are RM or points. */
  target_value?: number;
}

export interface DealerTargetCreatePayload extends TargetCreateBase {
  subject_kind: 'dealer';
  customer_ids: string[];
  payout_customer_id?: string;
  target_value?: number;
}

export interface TeamTargetCreatePayload extends TargetCreateBase {
  subject_kind: 'team';
  sales_team_id: string;
  /** One child agent target each; the team figure is their sum. */
  agent_figures: { sales_agent_id: string; target_value: number }[];
}

export type SalesTargetCreatePayload =
  | AgentTargetCreatePayload
  | TeamTargetCreatePayload
  | DealerTargetCreatePayload;

/** The header fields a PATCH may send; `split_every`/`split_unit` null turn the split off. */
export interface SalesTargetUpdatePayload {
  name?: string;
  metric?: TargetMetric;
  basis?: TargetBasis;
  product_scope?: TargetProductScope;
  category_ids?: string[];
  product_ids?: string[];
  brand_ids?: string[];
  start_date?: string;
  end_date?: string;
  split_every?: number | null;
  split_unit?: TargetSplitUnit | null;
  commission_method?: CommissionMethod;
  threshold_unit?: ThresholdUnit;
  rounding?: Rounding;
  tiers_per_period?: boolean;
  /** Dealer targets: replaces the counted accounts. */
  customer_ids?: string[];
  /** Dealer targets: null pays the first counted account. */
  payout_customer_id?: string | null;
  /** Replaces every tier; `[]` removes them. */
  tiers?: CommissionTier[];
  /** Every changed figure, in one request: this target's periods, or a team target's agents'. */
  figures?: { period_id: string; target_value: number }[];
  /** A team target's members with no figure yet, by the team period's start. */
  new_agents?: { sales_agent_id: string; figures: { period_start: string; target_value: number }[] }[];
}

export interface PointRatio {
  id: string;
  effective_from: string;
  rm_per_point: number;
}

export interface DealerImportSuggestion {
  customer_id: string;
  code: string;
  name: string;
}

export interface DealerImportRow {
  sheet: string;
  row: number;
  /** "<sheet>!<row>": the key a pick names. */
  pick_key: string;
  code: string | null;
  name_in_file: string | null;
  account_in_file: string | null;
  customer: string | null;
  /** auto (exact name and account), code, or picked. */
  match: 'auto' | 'code' | 'picked' | null;
  suggestions: DealerImportSuggestion[];
  status: 'mapped' | 'needs_pick' | 'skipped' | 'not_representable';
  reason: string | null;
  warnings: string[];
  target_keys: string[];
  target_nos: string[];
}

/** Per row ("<sheet>!<row>"): the picked customer id, or "skip". */
export type DealerImportPicks = Record<string, string>;

export interface DealerImportResult {
  dry_run: boolean;
  summary: {
    rows: number;
    mapped: number;
    auto_mapped: number;
    needs_pick: number;
    skipped: number;
    not_representable: number;
    targets_new: number;
    targets_updated: number;
  };
  rows: DealerImportRow[];
  notes: string[];
}

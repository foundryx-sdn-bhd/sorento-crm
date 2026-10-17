'use client';

import { useEffect, useMemo, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useClassScopeOptions, useProductScopeOptions } from '../hooks/usePolicies';
import {
  ABC_OPTIONS,
  BASELINE_SOURCE_OPTIONS,
  BUY_SCOPE_OPTIONS,
  POLICY_TYPE_OPTIONS,
  SAFETY_METHOD_OPTIONS,
  SCOPE_TYPE_LABEL,
  SCOPE_TYPE_OPTIONS,
  SPIKE_HANDLING_OPTIONS,
  SUPPLIER_SELECTION_OPTIONS,
  XYZ_OPTIONS,
} from '../lib/labels';
import type {
  PolicyType,
  ReorderPolicyRow,
  ReorderPolicyWrite,
  SafetyStockMethod,
  ScopeType,
  SupplierSelection,
} from '../types/policy.types';

/** A labelled control with novice-friendly helper text + inline error. */
function Field({
  label,
  help,
  error,
  children,
}: {
  label: string;
  help?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label className="mb-1 block">{label}</Label>
      {children}
      {help ? <p className="mt-1 text-2xs leading-snug text-muted-foreground">{help}</p> : null}
      {error ? <p className="mt-1 text-2xs text-destructive">{error}</p> : null}
    </div>
  );
}

interface FormState {
  scope_type: ScopeType;
  product_ref: string; // products.id (hidden) for sku scope
  class_ref: string; // category_code for product_class scope
  abc: string;
  xyz: string;
  policy_type: PolicyType;
  safety_stock_method: SafetyStockMethod;
  service_level_pct: string; // 0-100
  safety_days: string;
  review_period_days: string;
  lead_time_default_days: string;
  forecast_window_days: string;
  trajectory_window_retail_months: string;
  trajectory_window_project_months: string;
  price_stale_after_days: string;
  price_movement_threshold_pct: string;
  baseline_source: string;
  spike_handling: string;
  buy_scope: string;
  min_override: string;
  max_override: string;
  dead_stock_days: string;
  overstock_days: string;
  supplier_selection: SupplierSelection;
  priority: string;
  is_active: boolean;
}

const EMPTY_FORM: FormState = {
  scope_type: 'sku',
  product_ref: '',
  class_ref: '',
  abc: '',
  xyz: '',
  policy_type: 'reorder_point',
  safety_stock_method: 'fixed_days',
  service_level_pct: '',
  safety_days: '7',
  review_period_days: '',
  lead_time_default_days: '14',
  // Engine defaults (mirror reorder_engine.ensure_reorder_policy_defaults).
  forecast_window_days: '90',
  // Trajectory defaults: retail is stable so a quarter reads it; project takes a year.
  trajectory_window_retail_months: '3',
  trajectory_window_project_months: '12',
  price_stale_after_days: '180',
  price_movement_threshold_pct: '5',
  baseline_source: 'continuous_only',
  spike_handling: 'committed_only',
  buy_scope: 'network',
  min_override: '',
  max_override: '',
  dead_stock_days: '',
  overstock_days: '',
  supplier_selection: 'best_score',
  priority: '10',
  is_active: true,
};

function hydrate(row: ReorderPolicyRow): FormState {
  const [abc, xyz] = row.scope_type === 'abc_xyz_cell' && row.scope_ref
    ? row.scope_ref.split('-')
    : ['', ''];
  return {
    scope_type: row.scope_type,
    product_ref: row.scope_type === 'sku' ? row.scope_ref ?? '' : '',
    class_ref: row.scope_type === 'product_class' ? row.scope_ref ?? '' : '',
    abc: abc ?? '',
    xyz: xyz ?? '',
    policy_type: row.policy_type,
    safety_stock_method: row.safety_stock_method,
    service_level_pct: row.service_level != null ? String(Math.round(row.service_level * 100)) : '',
    safety_days: row.safety_days != null ? String(row.safety_days) : '',
    review_period_days: row.review_period_days != null ? String(row.review_period_days) : '',
    lead_time_default_days: row.lead_time_default_days != null ? String(row.lead_time_default_days) : '',
    forecast_window_days: row.forecast_window_days != null ? String(row.forecast_window_days) : '90',
    trajectory_window_retail_months: row.trajectory_window_retail_months != null ? String(row.trajectory_window_retail_months) : '3',
    trajectory_window_project_months: row.trajectory_window_project_months != null ? String(row.trajectory_window_project_months) : '12',
    price_stale_after_days: row.price_stale_after_days != null ? String(row.price_stale_after_days) : '180',
    price_movement_threshold_pct: row.price_movement_threshold_pct != null ? String(row.price_movement_threshold_pct) : '5',
    baseline_source: row.baseline_source ?? 'continuous_only',
    spike_handling: row.spike_handling ?? 'committed_only',
    buy_scope: row.buy_scope ?? 'network',
    min_override: row.min_override != null ? String(row.min_override) : '',
    max_override: row.max_override != null ? String(row.max_override) : '',
    dead_stock_days: row.dead_stock_days != null ? String(row.dead_stock_days) : '',
    overstock_days: row.overstock_days != null ? String(row.overstock_days) : '',
    supplier_selection: row.supplier_selection,
    priority: String(row.priority),
    is_active: row.is_active,
  };
}

/** Parse a non-empty numeric string to a number, else null. */
const numOrNull = (s: string): number | null => (s.trim() === '' ? null : Number(s));

export function AddEditPolicyModal({
  open,
  onOpenChange,
  mode,
  initial,
  onSubmit,
  isSubmitting,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: 'create' | 'edit';
  /** The row being edited (edit mode), else null. */
  initial: ReorderPolicyRow | null;
  onSubmit: (body: ReorderPolicyWrite) => Promise<void>;
  isSubmitting: boolean;
}) {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);

  const isGlobal = form.scope_type === 'global';
  // Global default row is edited with scope locked (AC-EDIT-4).
  const scopeLocked = mode === 'edit' && initial?.scope_type === 'global';

  const { data: productOptions, isLoading: productsLoading } = useProductScopeOptions();
  const { data: classOptions, isLoading: classesLoading } = useClassScopeOptions();

  useEffect(() => {
    if (!open) return;
    setErrors({});
    setFormError(null);
    setForm(initial ? hydrate(initial) : EMPTY_FORM);
  }, [open, initial]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const title = useMemo(() => {
    if (mode === 'edit') {
      return scopeLocked ? 'Edit global default policy' : 'Edit reorder policy';
    }
    return 'Add reorder policy';
  }, [mode, scopeLocked]);

  const validate = (): ReorderPolicyWrite | null => {
    const errs: Record<string, string> = {};

    // Scope target required for non-global scopes (AC-VAL-1).
    let scope_ref: string | null = null;
    if (form.scope_type === 'sku') {
      if (!form.product_ref) errs.scope_ref = 'Pick a product.';
      scope_ref = form.product_ref || null;
    } else if (form.scope_type === 'product_class') {
      if (!form.class_ref) errs.scope_ref = 'Pick a product class.';
      scope_ref = form.class_ref || null;
    } else if (form.scope_type === 'abc_xyz_cell') {
      if (!form.abc || !form.xyz) errs.scope_ref = 'Pick both an ABC and an XYZ class.';
      scope_ref = form.abc && form.xyz ? `${form.abc}-${form.xyz}` : null;
    }

    // Statistical safety stock hard-requires a service level (AC-VAL-7).
    let service_level: number | null = null;
    if (form.safety_stock_method === 'statistical') {
      if (form.service_level_pct.trim() === '') {
        errs.service_level = 'Statistical safety stock requires a service level.';
      } else {
        const pct = Number(form.service_level_pct);
        if (!(pct > 0 && pct < 100)) {
          errs.service_level = 'Service level must be between 0 and 100%.';
        } else {
          service_level = pct / 100;
        }
      }
    }

    // Positive day fields where provided (AC-VAL-4).
    const dayFields: { key: keyof FormState; err: string }[] = [
      { key: 'safety_days', err: 'Must be a positive number of days.' },
      { key: 'review_period_days', err: 'Must be a positive number of days.' },
      { key: 'lead_time_default_days', err: 'Must be a positive number of days.' },
      { key: 'forecast_window_days', err: 'Must be a positive number of days.' },
      { key: 'trajectory_window_retail_months', err: 'Must be a positive number of months.' },
      { key: 'trajectory_window_project_months', err: 'Must be a positive number of months.' },
      { key: 'price_stale_after_days', err: 'Must be a positive number of days.' },
      { key: 'price_movement_threshold_pct', err: 'Must be a positive percentage.' },
      { key: 'dead_stock_days', err: 'Must be a positive number of days.' },
      { key: 'overstock_days', err: 'Must be a positive number of days.' },
    ];
    for (const { key, err } of dayFields) {
      const v = form[key] as string;
      if (v.trim() !== '' && !(Number(v) > 0)) errs[key] = err;
    }

    // Overrides are optional but never negative.
    for (const key of ['min_override', 'max_override'] as const) {
      const problem = numberFieldError(form[key], { min: 0, optional: true });
      if (problem) errs[key] = problem;
    }

    // Min ≤ Max when both provided (AC-VAL-5).
    const min = numOrNull(form.min_override);
    const max = numOrNull(form.max_override);
    if (!errs.max_override && min != null && max != null && min > max) {
      errs.max_override = 'Max must be greater than or equal to Min.';
    }

    // Priority must be a whole number ≥ 0.
    const priority = Number(form.priority);
    if (numberFieldError(form.priority, { min: 0, integer: true })) {
      errs.priority = 'Priority must be a whole number of 0 or more.';
    }

    setErrors(errs);
    if (Object.keys(errs).length) {
      setFormError('Please fix the highlighted fields before saving.');
      return null;
    }
    setFormError(null);

    return {
      scope_type: form.scope_type,
      scope_ref: isGlobal ? null : scope_ref,
      policy_type: form.policy_type,
      service_level,
      safety_stock_method: form.safety_stock_method,
      safety_days: numOrNull(form.safety_days),
      review_period_days: form.policy_type === 'periodic_review' ? numOrNull(form.review_period_days) : null,
      forecast_window_days: numOrNull(form.forecast_window_days),
      trajectory_window_retail_months: numOrNull(form.trajectory_window_retail_months),
      trajectory_window_project_months: numOrNull(form.trajectory_window_project_months),
      price_stale_after_days: numOrNull(form.price_stale_after_days),
      price_movement_threshold_pct: numOrNull(form.price_movement_threshold_pct),
      baseline_source: form.baseline_source || null,
      spike_handling: form.spike_handling || null,
      buy_scope: form.buy_scope || null,
      dead_stock_days: numOrNull(form.dead_stock_days),
      overstock_days: numOrNull(form.overstock_days),
      min_override: form.policy_type === 'min_max' ? min : null,
      max_override: form.policy_type === 'min_max' ? max : null,
      priority,
      is_active: form.is_active,
      supplier_selection: form.supplier_selection,
      lead_time_default_days: numOrNull(form.lead_time_default_days),
    };
  };

  const submit = async () => {
    const body = validate();
    if (!body) return;
    await onSubmit(body);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <DialogBody className="space-y-5">
          {formError ? (
            <Alert variant="destructive">
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}

          {/* ── Scope ─────────────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Scope"
              help="How specific this policy is. More specific scopes win over the global default when a SKU is resolved."
            >
              {scopeLocked ? (
                <div className="flex h-10 items-center rounded-md border border-input bg-muted/40 px-3 text-sm text-muted-foreground">
                  {SCOPE_TYPE_LABEL.global}
                </div>
              ) : (
                <SearchableSelect
                  value={form.scope_type}
                  onChange={(v) => set('scope_type', v as ScopeType)}
                  options={SCOPE_TYPE_OPTIONS}
                  placeholder="Choose a scope"
                />
              )}
            </Field>

            {/* Scope target picker switches by scope type. */}
            {!isGlobal ? (
              <Field
                label="Scope target"
                help="Which SKU, product class, or ABC-XYZ cell this policy applies to."
                error={errors.scope_ref}
              >
                {form.scope_type === 'sku' ? (
                  <SearchableSelect
                    value={form.product_ref}
                    onChange={(v) => set('product_ref', v)}
                    options={productOptions ?? []}
                    disabled={productsLoading}
                    placeholder={productsLoading ? 'Loading products…' : 'Search a product'}
                    emptyMessage="No products found."
                  />
                ) : null}
                {form.scope_type === 'product_class' ? (
                  <SearchableSelect
                    value={form.class_ref}
                    onChange={(v) => set('class_ref', v)}
                    options={classOptions ?? []}
                    disabled={classesLoading}
                    placeholder={classesLoading ? 'Loading classes…' : 'Search a product class'}
                    emptyMessage="No product classes found."
                  />
                ) : null}
                {form.scope_type === 'abc_xyz_cell' ? (
                  <div className="grid grid-cols-2 gap-2">
                    <SearchableSelect
                      value={form.abc}
                      onChange={(v) => set('abc', v)}
                      options={ABC_OPTIONS}
                      placeholder="ABC"
                    />
                    <SearchableSelect
                      value={form.xyz}
                      onChange={(v) => set('xyz', v)}
                      options={XYZ_OPTIONS}
                      placeholder="XYZ"
                    />
                  </div>
                ) : null}
              </Field>
            ) : (
              <div />
            )}
          </div>

          {/* ── Policy type + supplier + priority + active ────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Policy type"
              help="Reorder point fires when stock falls to a trigger level. Periodic review tops up on a fixed cadence. Min-max replenishes between a floor and ceiling."
            >
              <SearchableSelect
                value={form.policy_type}
                onChange={(v) => set('policy_type', v as PolicyType)}
                options={POLICY_TYPE_OPTIONS}
              />
            </Field>
            <Field
              label="Supplier selection"
              help="How the engine picks a supplier for the buy: the primary one, the best-scoring, or the cheapest."
            >
              <SearchableSelect
                value={form.supplier_selection}
                onChange={(v) => set('supplier_selection', v as SupplierSelection)}
                options={SUPPLIER_SELECTION_OPTIONS}
              />
            </Field>
          </div>

          {/* ── Safety stock ─────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Safety-stock method"
              help="How much buffer to hold. Fixed days = a set number of days of demand. Statistical = sized to a service level. Manual = a fixed unit amount you set."
            >
              <SearchableSelect
                value={form.safety_stock_method}
                onChange={(v) => set('safety_stock_method', v as SafetyStockMethod)}
                options={SAFETY_METHOD_OPTIONS}
              />
            </Field>

            {form.safety_stock_method === 'statistical' ? (
              <Field
                label="Service level (%)"
                help="The odds of not stocking out during the lead time. Higher = more buffer. Required for statistical safety stock."
                error={errors.service_level}
              >
                <NumberInput
                  min={1}
                  max={99}
                  inputMode="numeric"
                  value={form.service_level_pct}
                  onChange={(e) => set('service_level_pct', e.target.value)}
                  placeholder="e.g. 95"
                />
              </Field>
            ) : (
              <Field
                label={form.safety_stock_method === 'manual' ? 'Manual safety stock (units)' : 'Safety buffer (days)'}
                help={
                  form.safety_stock_method === 'manual'
                    ? 'A fixed number of units to always keep on hand.'
                    : 'How many days of demand to hold as buffer.'
                }
                error={errors.safety_days}
              >
                <NumberInput
                  min={1}
                  inputMode="numeric"
                  value={form.safety_days}
                  onChange={(e) => set('safety_days', e.target.value)}
                  placeholder="e.g. 7"
                />
              </Field>
            )}
          </div>

          {/* ── Timing + thresholds ──────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Default lead time (days)"
              help="Fallback lead time used when a supplier has no measured or declared lead time."
              error={errors.lead_time_default_days}
            >
              <NumberInput
                min={1}
                inputMode="numeric"
                value={form.lead_time_default_days}
                onChange={(e) => set('lead_time_default_days', e.target.value)}
                placeholder="e.g. 14"
              />
            </Field>

            {form.policy_type === 'periodic_review' ? (
              <Field
                label="Review period (days)"
                help="How often stock is reviewed and topped up under a periodic-review policy."
                error={errors.review_period_days}
              >
                <NumberInput
                  min={1}
                  inputMode="numeric"
                  value={form.review_period_days}
                  onChange={(e) => set('review_period_days', e.target.value)}
                  placeholder="e.g. 7"
                />
              </Field>
            ) : (
              <div />
            )}
          </div>

          {form.policy_type === 'min_max' ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field
                label="Min level (units)"
                help="The floor. When net position drops to or below Min, reorder up to Max."
                error={errors.min_override}
              >
                <NumberInput
                  min={0}
                  inputMode="numeric"
                  value={form.min_override}
                  onChange={(e) => set('min_override', e.target.value)}
                  placeholder="e.g. 40"
                />
              </Field>
              <Field
                label="Max level (units)"
                help="The ceiling the min-max policy replenishes up to."
                error={errors.max_override}
              >
                <NumberInput
                  min={0}
                  inputMode="numeric"
                  value={form.max_override}
                  onChange={(e) => set('max_override', e.target.value)}
                  placeholder="e.g. 200"
                />
              </Field>
            </div>
          ) : null}

          {/* ── Disposition windows ──────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Dead-stock window (days)"
              help="No movement beyond this flags the SKU as dead stock for disposition."
              error={errors.dead_stock_days}
            >
              <NumberInput
                min={1}
                inputMode="numeric"
                value={form.dead_stock_days}
                onChange={(e) => set('dead_stock_days', e.target.value)}
                placeholder="e.g. 180"
              />
            </Field>
            <Field
              label="Overstock window (days)"
              help="Cover beyond this many days of demand flags the SKU as overstocked."
              error={errors.overstock_days}
            >
              <NumberInput
                min={1}
                inputMode="numeric"
                value={form.overstock_days}
                onChange={(e) => set('overstock_days', e.target.value)}
                placeholder="e.g. 120"
              />
            </Field>
          </div>

          {/* ── Engine (advanced) ────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Forecast window (days)"
              help="How far back demand is averaged to forecast future usage. Longer smooths noise; shorter reacts faster."
              error={errors.forecast_window_days}
            >
              <NumberInput
                min={1}
                inputMode="numeric"
                value={form.forecast_window_days}
                onChange={(e) => set('forecast_window_days', e.target.value)}
                placeholder="e.g. 90"
              />
            </Field>
            <Field
              label="Retail trend window (months)"
              help="How many months of retail orders decide whether demand is rising or dying off."
              error={errors.trajectory_window_retail_months}
            >
              <NumberInput
                min={1}
                inputMode="numeric"
                value={form.trajectory_window_retail_months}
                onChange={(e) => set('trajectory_window_retail_months', e.target.value)}
                placeholder="e.g. 3"
              />
            </Field>
            <Field
              label="Project trend window (months)"
              help="Project demand is erratic, so its window is longer."
              error={errors.trajectory_window_project_months}
            >
              <NumberInput
                min={1}
                inputMode="numeric"
                value={form.trajectory_window_project_months}
                onChange={(e) => set('trajectory_window_project_months', e.target.value)}
                placeholder="e.g. 12"
              />
            </Field>
            <Field
              label="Price too old after (days)"
              help="A last purchase older than this gets a re-quote suggestion."
              error={errors.price_stale_after_days}
            >
              <NumberInput
                min={1}
                inputMode="numeric"
                value={form.price_stale_after_days}
                onChange={(e) => set('price_stale_after_days', e.target.value)}
                placeholder="e.g. 180"
              />
            </Field>
            <Field
              label="Price change worth acting on (%)"
              help="Flags a moved price, and gates the cheaper-supplier suggestion."
              error={errors.price_movement_threshold_pct}
            >
              <NumberInput
                min={0.1}
                step={0.1}
                inputMode="decimal"
                value={form.price_movement_threshold_pct}
                onChange={(e) => set('price_movement_threshold_pct', e.target.value)}
                placeholder="e.g. 5"
              />
            </Field>
            <Field
              label="Baseline demand"
              help="Which sales feed the forecast. Continuous demand only excludes one-off spikes; All demand counts everything."
            >
              <SearchableSelect
                value={form.baseline_source}
                onChange={(v) => set('baseline_source', v)}
                options={BASELINE_SOURCE_OPTIONS}
                placeholder="Choose a baseline"
              />
            </Field>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Spike handling"
              help="How demand spikes are treated. Committed orders only sizes to firm orders; Statistical adds a buffer for variability; Ignore treats spikes as normal demand."
            >
              <SearchableSelect
                value={form.spike_handling}
                onChange={(v) => set('spike_handling', v)}
                options={SPIKE_HANDLING_OPTIONS}
                placeholder="Choose spike handling"
              />
            </Field>
            <Field
              label="Buy scope"
              help="Where the buy is planned. Network pools demand across warehouses then allocates; Per warehouse plans each location on its own."
            >
              <SearchableSelect
                value={form.buy_scope}
                onChange={(v) => set('buy_scope', v)}
                options={BUY_SCOPE_OPTIONS}
                placeholder="Choose a buy scope"
              />
            </Field>
          </div>

          {/* ── Priority + active ────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Priority"
              help="When two policies share the same scope, the higher priority wins the tie."
              error={errors.priority}
            >
              <NumberInput
                min={0}
                inputMode="numeric"
                value={form.priority}
                onChange={(e) => set('priority', e.target.value)}
                disabled={isGlobal}
                placeholder="e.g. 10"
              />
            </Field>
            <Field
              label="Active"
              help="Inactive policies are ignored by the reorder run - the next-most-specific active policy resolves instead."
            >
              <div className="flex h-10 items-center">
                <Switch checked={form.is_active} onCheckedChange={(v) => set('is_active', v)} />
                <span className="ms-2 text-sm text-muted-foreground">
                  {form.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>
            </Field>
          </div>

          <p className="rounded-md bg-muted/40 px-3 py-2 text-2xs text-muted-foreground">
            Changes take effect on the next reorder run.
          </p>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={isSubmitting}>
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            {mode === 'edit' ? 'Save changes' : 'Add policy'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

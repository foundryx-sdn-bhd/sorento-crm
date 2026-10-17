'use client';

import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { requiredNumber } from '@/lib/number-field-schema';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { useQuery } from '@tanstack/react-query';
import { getCompaniesSelect } from '@/app/(protected)/system-management/companies/services/companyService';
import type { ScheduledTask } from '../types/scheduledTask.types';
import { parseDateTimeAsUTC } from '@/lib/helpers';
import { ScmReorderRunScopeFields } from './ScmReorderRunScopeFields';

/** Format a Date as a `datetime-local` value in the browser's local timezone.
 *  start_at is serialized to UTC on submit (Date.toISOString), so the absolute
 *  instant is stored; this only controls the wall-clock the user sees + edits. */
function toLocalInputValue(d: Date | undefined | null): string {
  if (!d) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Seconds -> the shortest honest phrasing, e.g. 150 -> "2m 30s". */
function formatGrace(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  const rem = seconds % 60;
  return rem ? `${mins}m ${rem}s` : `${mins}m`;
}

const schema = z
  .object({
    name: z.string().min(1).optional(),
    description: z.string().nullable().optional(),
    enabled: z.boolean(),
    interval_value: requiredNumber({ min: 1 }),
    interval_unit: z.enum(['seconds', 'minutes', 'hours', 'days']),
    timezone: z.string().optional(),
    start_at: z.date().nullable().optional(),
    // Empty = every company (the default for every task). Narrowing is opt-in.
    company_ids: z.array(z.string()).optional(),
    send_in_app: z.boolean().optional(),
    send_email: z.boolean().optional(),
    // Empty string = "use the global default". NaN is what an `Input type=number`
    // yields for non-numeric text, so it must be rejected explicitly.
    grace_percent: z
      .union([z.literal(''), z.number().int().min(0).max(1000)])
      .optional()
      .refine((v) => v === '' || v === undefined || !Number.isNaN(v), {
        message: 'Enter a whole number, or leave blank to use the global default',
      }),
    // #1340: the scm_reorder_run task's configurable scope - see
    // `ScmReorderRunScopeFields`. Empty/undefined means the run's own today default
    // for every one of these; other task keys never populate them.
    warehouse_codes: z.array(z.string()).optional(),
    product_codes: z.array(z.string()).optional(),
    demand_class: z.enum(['', 'project', 'retail']).optional(),
    horizon_start_days: z
      .union([z.literal(''), z.number().int().min(-3650).max(3650)])
      .optional()
      .refine((v) => v === '' || v === undefined || !Number.isNaN(v), {
        message: 'Enter a whole number of days',
      }),
    horizon_end_days: z
      .union([z.literal(''), z.number().int().min(0).max(3650)])
      .optional()
      .refine((v) => v === '' || v === undefined || !Number.isNaN(v), {
        message: 'Enter a whole number of days, 0 or more',
      }),
    budget: z
      .union([z.literal(''), z.number().min(0)])
      .optional()
      .refine((v) => v === '' || v === undefined || !Number.isNaN(v), {
        message: 'Enter an amount, or leave blank to fund every buy',
      }),
    include_market: z.boolean().optional(),
  })
  .refine(
    (data) =>
      typeof data.horizon_start_days !== 'number' ||
      typeof data.horizon_end_days !== 'number' ||
      data.horizon_start_days <= data.horizon_end_days,
    {
      message: 'From must be on or before To',
      path: ['horizon_start_days'],
    },
  );

export type FormValues = z.infer<typeof schema>;

/** The scm_reorder_run scope block's own defaults, pre-filled from `task.metadata` - one
 *  helper shared by `useForm`'s `defaultValues` and the reset-on-task-change effect below,
 *  so the two never drift into a second reading of the same metadata. Any other task key
 *  gets the same "nothing set" shape the block would render as blank anyway. */
function scmReorderRunScopeDefaults(task: ScheduledTask) {
  const meta = task.key === 'scm_reorder_run' ? (task.metadata ?? {}) : {};
  const demandClass = meta.demand_class;
  // Each assigned to its own const first - inlining the ternary as an object literal
  // property widens it to `string` (TS drops the literal narrowing for a return-object
  // property computed inline), which then fails against FormValues' `'' | 'project' |
  // 'retail'` union.
  const demand_class: '' | 'project' | 'retail' =
    demandClass === 'project' || demandClass === 'retail' ? demandClass : '';
  const horizon_start_days: number | '' =
    typeof meta.horizon_start_days === 'number' ? meta.horizon_start_days : '';
  const horizon_end_days: number | '' =
    typeof meta.horizon_end_days === 'number' ? meta.horizon_end_days : '';
  const budget: number | '' = typeof meta.budget === 'number' ? meta.budget : '';
  return {
    warehouse_codes: Array.isArray(meta.warehouse_codes) ? (meta.warehouse_codes as string[]) : [],
    product_codes: Array.isArray(meta.product_codes) ? (meta.product_codes as string[]) : [],
    demand_class,
    horizon_start_days,
    horizon_end_days,
    budget,
    include_market: meta.include_market === true,
  };
}

interface ScheduledTaskFormProps {
  task: ScheduledTask;
  onSubmit: (values: FormValues) => void;
  isSubmitting?: boolean;
}

export function ScheduledTaskForm({ task, onSubmit, isSubmitting }: ScheduledTaskFormProps) {
  const {
    register,
    handleSubmit,
    setValue,
    reset,
    watch,
    formState: { errors, isDirty },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: 'onTouched',
    defaultValues: {
      name: task.name,
      description: task.description ?? '',
      enabled: task.enabled,
      interval_value: task.interval_value,
      interval_unit: task.interval_unit as 'seconds' | 'minutes' | 'hours' | 'days',
      timezone: task.timezone || 'UTC',
      start_at: task.start_at ? parseDateTimeAsUTC(task.start_at) ?? undefined : undefined,
      company_ids: Array.isArray(task.metadata?.company_ids)
        ? (task.metadata.company_ids as string[])
        : [],
      send_in_app:
        task.key === 'user_sla_daily_summary'
          ? (task.metadata?.send_in_app as boolean | undefined) !== false
          : true,
      send_email:
        task.key === 'user_sla_daily_summary'
          ? (task.metadata?.send_email as boolean | undefined) !== false
          : true,
      grace_percent:
        typeof task.metadata?.grace_percent === 'number'
          ? (task.metadata.grace_percent as number)
          : '',
      ...scmReorderRunScopeDefaults(task),
    },
  });

  const { data: companies } = useQuery({
    queryKey: ['companies-select'],
    queryFn: getCompaniesSelect,
  });

  const enabled = watch('enabled');
  const intervalUnit = watch('interval_unit');
  const startAt = watch('start_at');

  useEffect(() => {
    // reset(), not a pile of setValue() calls. setValue updates the VALUE but leaves
    // react-hook-form's defaultValues on the stale server state, so `isDirty` keeps
    // comparing against what the task looked like at mount. After saving companies
    // and then clearing them, the form read as pristine and "Save changes" stayed
    // disabled - the change could not be saved back. reset() re-baselines both.
    reset({
      name: task.name,
      description: task.description ?? '',
      enabled: task.enabled,
      interval_value: task.interval_value,
      interval_unit: task.interval_unit as 'seconds' | 'minutes' | 'hours' | 'days',
      timezone: task.timezone || 'UTC',
      start_at: task.start_at ? parseDateTimeAsUTC(task.start_at) ?? undefined : undefined,
      grace_percent:
        typeof task.metadata?.grace_percent === 'number'
          ? (task.metadata.grace_percent as number)
          : '',
      company_ids: Array.isArray(task.metadata?.company_ids)
        ? (task.metadata.company_ids as string[])
        : [],
      send_in_app:
        task.key === 'user_sla_daily_summary'
          ? (task.metadata?.send_in_app as boolean | undefined) !== false
          : true,
      send_email:
        task.key === 'user_sla_daily_summary'
          ? (task.metadata?.send_email as boolean | undefined) !== false
          : true,
      ...scmReorderRunScopeDefaults(task),
    });
  }, [task, reset]);

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="name">Name</Label>
          <Input id="name" {...register('name')} />
          {errors.name && (
            <p className="text-sm text-destructive">{errors.name.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="description">Description</Label>
          <Input id="description" {...register('description')} placeholder="Optional" />
        </div>
      </div>

      <div className="flex items-center space-x-2">
        <Switch
          id="enabled"
          checked={enabled}
          onCheckedChange={(v) => setValue('enabled', v, { shouldDirty: true })}
        />
        <Label htmlFor="enabled">Enabled</Label>
      </div>

      {task.key === 'scm_reorder_run' && (
        <ScmReorderRunScopeFields
          watch={watch}
          setValue={setValue}
          register={register}
          errors={errors}
        />
      )}

      {task.key === 'user_sla_daily_summary' && (
        <div className="space-y-3 rounded-md border p-4">
          <p className="text-sm font-medium">Notification channels</p>
          <div className="flex items-center space-x-2">
            <Switch
              id="send_in_app"
              checked={watch('send_in_app') !== false}
              onCheckedChange={(v) =>
                setValue('send_in_app', v, { shouldDirty: true })}
            />
            <Label htmlFor="send_in_app">In-app</Label>
          </div>
          <div className="flex items-center space-x-2">
            <Switch
              id="send_email"
              checked={watch('send_email') !== false}
              onCheckedChange={(v) =>
                setValue('send_email', v, { shouldDirty: true })}
            />
            <Label htmlFor="send_email">Email</Label>
          </div>
        </div>
      )}

      <div className="space-y-2 rounded-md border p-4">
        <Label>Companies</Label>
        <SearchableMultiSelect
          value={watch('company_ids') ?? []}
          onChange={(v) => setValue('company_ids', v, { shouldDirty: true })}
          options={(companies ?? []).map((c) => ({
            value: c.id,
            label: `${c.name} (${c.code})`,
          }))}
          placeholder="All companies"
        />
        <p className="text-muted-foreground text-xs">
          Leave empty to run across every company. Selecting companies restricts this
          task to their data only - it does not change who gets notified.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="space-y-2">
          <Label htmlFor="interval_value">Interval value</Label>
          <NumberInput
            id="interval_value"
            min={1}
            {...register('interval_value', { valueAsNumber: true })}
          />
          {errors.interval_value && (
            <p className="text-sm text-destructive">{errors.interval_value.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="interval_unit">Interval unit</Label>
          <SearchableSelect
            id="interval_unit"
            value={intervalUnit}
            onChange={(v) => setValue('interval_unit', v as 'seconds' | 'minutes' | 'hours' | 'days', { shouldDirty: true })}
            options={[
              { value: 'seconds', label: 'Seconds' },
              { value: 'minutes', label: 'Minutes' },
              { value: 'hours', label: 'Hours' },
              { value: 'days', label: 'Days' },
            ]}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="timezone">Timezone</Label>
          <Input id="timezone" {...register('timezone')} placeholder="UTC" />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="grace_percent">Grace period (%)</Label>
        <NumberInput
          id="grace_percent"
          min={0}
          max={1000}
          placeholder={
            task.grace_percent != null ? `${task.grace_percent} (global default)` : 'Global default'
          }
          {...register('grace_percent', {
            setValueAs: (v) => (v === '' || v === null ? '' : Number(v)),
          })}
        />
        {errors.grace_percent && (
          <p className="text-sm text-destructive">{errors.grace_percent.message}</p>
        )}
        <p className="text-xs text-muted-foreground">
          How late this task may run before it counts as overdue, as a percentage of its own
          interval. Clamped to between 1 minute and 30 minutes.
          {task.grace_seconds != null && (
            <> Currently <strong>{formatGrace(task.grace_seconds)}</strong>.</>
          )}{' '}
          Leave blank to use the global default.
        </p>
      </div>

      <div className="space-y-2">
        <Label>Start date &amp; time (optional)</Label>
        <Input
          type="datetime-local"
          value={toLocalInputValue(startAt ?? undefined)}
          onChange={(e) =>
            setValue('start_at', e.target.value ? new Date(e.target.value) : null, {
              shouldDirty: true,
            })
          }
        />
        <p className="text-xs text-muted-foreground">
          In your local timezone. Anchors the first run; the task then repeats every interval.
          For a fixed daily time (e.g. 8:00 am), set the interval to 1 day.
        </p>
      </div>

      <Button type="submit" disabled={!isDirty || isSubmitting}>
        {isSubmitting ? 'Saving...' : 'Save changes'}
      </Button>
    </form>
  );
}

'use client';

import { useEffect, useMemo, useState } from 'react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { AlertTriangle } from 'lucide-react';
import { RuleBuilder } from '@/components/rule-builder/RuleBuilder';
import type { RuleGroup } from '@/components/rule-builder/types';
import { useEmailTemplates } from '../../email-templates/hooks/useEmailTemplates';
import {
  useCreateAutomation,
  useTriggerCatalog,
  useUpdateAutomation,
} from '../hooks/useAutomations';
import { AutomationRuleValidationError } from '../services/automationService';
import type {
  Automation,
  RecipientConfig,
} from '../types/automation.types';
import RecipientPicker from './RecipientPicker';

const TIMEZONES = [
  'Asia/Kuala_Lumpur',
  'Asia/Singapore',
  'Asia/Jakarta',
  'Asia/Bangkok',
  'Asia/Tokyo',
  'UTC',
];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  automation?: Automation | null;
  onSaved?: () => void;
}

const emptyRecipients: RecipientConfig = {
  user_ids: [],
  role_ids: [],
  include_promotion_owner: false,
  extra_emails: [],
};

export default function AutomationForm({ open, onOpenChange, automation, onSaved }: Props) {
  const isEdit = !!automation;
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [triggerType, setTriggerType] = useState<string>('');
  const [daysBefore, setDaysBefore] = useState<number>(7);
  const [emailTemplateId, setEmailTemplateId] = useState<string>('');
  const [recipientConfig, setRecipientConfig] = useState<RecipientConfig>(emptyRecipients);
  const [groupMatches, setGroupMatches] = useState(true);
  const [scheduleType, setScheduleType] = useState<'manual' | 'daily'>('manual');
  const [runTime, setRunTime] = useState<string>('09:00');
  const [timezone, setTimezone] = useState<string>('Asia/Kuala_Lumpur');
  const [conditionsJson, setConditionsJson] = useState<RuleGroup | null>(null);
  const [ruleProblems, setRuleProblems] = useState<string[]>([]);

  const triggers = useTriggerCatalog();
  const templates = useEmailTemplates({ limit: 100 });
  const createMut = useCreateAutomation();
  const updateMut = useUpdateAutomation(automation?.id ?? '');
  const saving = createMut.isPending || updateMut.isPending;

  const triggerSpecs = triggers.data?.triggers ?? [];

  useEffect(() => {
    if (!open) return;
    if (automation) {
      setName(automation.name);
      setDescription(automation.description ?? '');
      setEnabled(automation.enabled);
      setTriggerType(automation.trigger_type);
      const cfg = automation.trigger_config as { days_before?: number };
      // An automation saved before the X was settable has an EMPTY config, so
      // fall back to its own trigger's default rather than a hardcoded 7 - a
      // certificate automation showing 7 under "Days before the certificate
      // expires" is the same type-blind guess that caused the empty config.
      const savedSpec = triggerSpecs.find((s) => s.type === automation.trigger_type);
      const savedDefault = (
        (savedSpec?.config_schema as { properties?: Record<string, unknown> } | undefined)
          ?.properties?.days_before as { default?: number } | undefined
      )?.default;
      setDaysBefore(
        typeof cfg?.days_before === 'number' ? cfg.days_before : (savedDefault ?? 7),
      );
      setEmailTemplateId(automation.email_template_id);
      // Spread the fetched config over the defaults rather than listing keys (AC-H16
      // round-trip): a hand-picked list silently drops any key the type declares that
      // this list forgets to name - `include_assigned_cs_pic` and `include_actor` both
      // hydrated `false` regardless of what was saved, and an unchanged Save then wrote
      // that `false` back for real.
      setRecipientConfig({ ...emptyRecipients, ...(automation.recipient_config ?? {}) });
      setGroupMatches(automation.group_matches ?? true);
      setScheduleType(automation.schedule_type);
      setRunTime(automation.run_time ? automation.run_time.slice(0, 5) : '09:00');
      setTimezone(automation.timezone || 'Asia/Kuala_Lumpur');
      setConditionsJson(automation.conditions_json ?? null);
      setRuleProblems([]);
    } else {
      setName('');
      setDescription('');
      setEnabled(true);
      setTriggerType(triggerSpecs[0]?.type ?? '');
      setDaysBefore(
        ((triggerSpecs[0]?.config_schema as
          | { properties?: Record<string, unknown> }
          | undefined)?.properties?.days_before as { default?: number } | undefined)
          ?.default ?? 7,
      );
      setEmailTemplateId('');
      setRecipientConfig(emptyRecipients);
      setGroupMatches(true);
      setScheduleType('manual');
      setRunTime('09:00');
      setTimezone('Asia/Kuala_Lumpur');
      setConditionsJson(null);
      setRuleProblems([]);
    }
  }, [open, automation, triggerSpecs.length]);

  const triggerSpec = useMemo(
    () => triggerSpecs.find((s) => s.type === triggerType),
    [triggerSpecs, triggerType],
  );

  // Degrade gracefully if the triggers API hasn't started returning fact_sources.
  const factSources = triggerSpec?.fact_sources ?? [];

  /**
   * The trigger's own `days_before` config, read from its JSON schema rather
   * than from a hardcoded trigger type. Every trigger that needs an X declares
   * it in `config_schema`, so a new one gets its input for free - the previous
   * `triggerType === 'days_before_promotion_end'` gate left the certificate
   * trigger with no input at all, saving `trigger_config: {}` and matching
   * nothing.
   */
  const daysBeforeSchema = useMemo(() => {
    const props = (triggerSpec?.config_schema as
      | { properties?: Record<string, unknown> }
      | undefined)?.properties;
    const schema = props?.days_before;
    return schema && typeof schema === 'object'
      ? (schema as { title?: string; default?: number })
      : null;
  }, [triggerSpec]);

  const supportsGrouping = triggerSpec?.supports_grouping ?? false;

  // Changing the trigger switches which facts are valid, so any existing
  // condition tree no longer applies - reset it.
  const handleTriggerChange = (t: string) => {
    setTriggerType(t);
    setConditionsJson(null);
    setRuleProblems([]);
    // Each trigger carries its own sensible X (7 for promotions, 30 for
    // certificates), so adopt the new trigger's default instead of carrying the
    // previous trigger's number across.
    const next = triggerSpecs.find((s) => s.type === t);
    const nextDefault = (
      (next?.config_schema as { properties?: Record<string, unknown> } | undefined)
        ?.properties?.days_before as { default?: number } | undefined
    )?.default;
    if (typeof nextDefault === 'number') setDaysBefore(nextDefault);
  };

  async function onSubmit() {
    if (!name.trim()) {
      toast.error('Name is required');
      return;
    }
    if (!triggerType) {
      toast.error('Pick a trigger type');
      return;
    }
    if (!emailTemplateId) {
      toast.error('Pick an email template');
      return;
    }
    if (scheduleType === 'daily' && !runTime) {
      toast.error('Pick a run time');
      return;
    }
    const totalRecipients =
      recipientConfig.user_ids.length +
      recipientConfig.role_ids.length +
      recipientConfig.extra_emails.length +
      (recipientConfig.include_promotion_owner ? 1 : 0) +
      (recipientConfig.include_assigned_cs_pic ? 1 : 0);
    if (totalRecipients === 0) {
      toast.error('At least one recipient is required');
      return;
    }
    const body = {
      name: name.trim(),
      description: description.trim() || null,
      enabled,
      trigger_type: triggerType,
      trigger_config: daysBeforeSchema ? { days_before: daysBefore } : {},
      action_type: 'send_email',
      email_template_id: emailTemplateId,
      recipient_config: recipientConfig,
      group_matches: supportsGrouping ? groupMatches : true,
      // Only send a condition tree for triggers that expose facts; null = match all.
      conditions_json: factSources.length > 0 ? conditionsJson : null,
      schedule_type: scheduleType,
      run_time: scheduleType === 'daily' ? `${runTime}:00` : null,
      timezone,
    };
    setRuleProblems([]);
    try {
      if (isEdit && automation) {
        await updateMut.mutateAsync(body);
        toast.success('Automation updated');
      } else {
        await createMut.mutateAsync(body);
        toast.success('Automation created');
      }
      onSaved?.();
      onOpenChange(false);
    } catch (err) {
      if (err instanceof AutomationRuleValidationError) {
        setRuleProblems(err.problems);
        toast.error('Fix the highlighted rule conditions before saving');
        return;
      }
      toast.error((err as Error).message || 'Save failed');
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] max-w-3xl flex-col">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit automation' : 'New automation'}</DialogTitle>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-y-auto px-1 md:grid-cols-2">
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="auto-name">Name</Label>
            <Input
              id="auto-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Promotion expiry reminder"
            />
          </div>
          <div className="space-y-1 md:col-span-2">
            <Label htmlFor="auto-desc">Description</Label>
            <Textarea
              id="auto-desc"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="space-y-1">
            <Label>Trigger</Label>
            <SearchableSelect
              value={triggerType}
              onChange={handleTriggerChange}
              options={triggerSpecs.map((s) => ({ value: s.type, label: s.label }))}
              placeholder="Pick trigger"
            />
            {triggerSpec && (
              <p className="text-xs text-muted-foreground">{triggerSpec.description}</p>
            )}
          </div>

          {daysBeforeSchema && (
            <div className="space-y-1">
              <Label htmlFor="auto-days">{daysBeforeSchema.title || 'Days before'}</Label>
              <Input
                id="auto-days"
                type="number"
                min={0}
                value={daysBefore}
                onChange={(e) => setDaysBefore(Number(e.target.value))}
              />
            </div>
          )}

          <div className="space-y-1 md:col-span-2">
            <Label>Email template</Label>
            <SearchableSelect
              value={emailTemplateId}
              onChange={setEmailTemplateId}
              loadError={templates.error}
              onRetry={() => void templates.refetch()}
              options={(templates.data?.data ?? []).map((t) => ({
                value: t.id,
                label: `${t.name} (${t.code})`,
              }))}
              placeholder="Pick template"
            />
          </div>

          <div className="md:col-span-2">
            <Label>Recipients</Label>
            <RecipientPicker value={recipientConfig} onChange={setRecipientConfig} />
          </div>

          {supportsGrouping && (
            <div className="md:col-span-2 rounded-md border p-3">
              <div className="flex items-start gap-3">
                <Switch
                  id="auto-group"
                  checked={groupMatches}
                  onCheckedChange={setGroupMatches}
                />
                <div className="space-y-0.5">
                  <Label htmlFor="auto-group">Combine into one email</Label>
                  <p className="text-xs text-muted-foreground">
                    When several records match on the same day, send each recipient a single
                    email listing them all instead of one email per match.
                  </p>
                </div>
              </div>
            </div>
          )}

          {factSources.length > 0 && (
            <div className="space-y-2 md:col-span-2">
              <Label>Conditions (optional)</Label>
              <p className="text-xs text-muted-foreground">
                Only act on matches that meet these conditions. Leave empty to act on every
                match.
              </p>
              {ruleProblems.length > 0 && (
                <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                  <ul className="list-disc space-y-0.5 ps-4">
                    {ruleProblems.map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                </div>
              )}
              <RuleBuilder
                key={triggerType}
                sources={factSources}
                value={conditionsJson}
                onChange={setConditionsJson}
              />
            </div>
          )}

          <div className="space-y-2 md:col-span-2">
            <Label>Schedule</Label>
            <RadioGroup
              value={scheduleType}
              onValueChange={(v) => setScheduleType(v as 'manual' | 'daily')}
              className="flex gap-4"
            >
              <label className="flex items-center gap-2 cursor-pointer">
                <RadioGroupItem value="manual" id="sched-manual" />
                <span>Manual</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <RadioGroupItem value="daily" id="sched-daily" />
                <span>Daily</span>
              </label>
            </RadioGroup>
            {scheduleType === 'daily' && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor="auto-runtime">Run time</Label>
                  <Input
                    id="auto-runtime"
                    type="time"
                    value={runTime}
                    onChange={(e) => setRunTime(e.target.value)}
                  />
                </div>
                <div className="space-y-1">
                  <Label>Timezone</Label>
                  <SearchableSelect
                    value={timezone}
                    onChange={setTimezone}
                    options={TIMEZONES.map((tz) => ({ value: tz, label: tz }))}
                  />
                </div>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2 md:col-span-2">
            <Switch id="auto-enabled" checked={enabled} onCheckedChange={setEnabled} />
            <Label htmlFor="auto-enabled">Enabled</Label>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={onSubmit} disabled={saving}>
            {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Create automation'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

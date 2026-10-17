'use client';

import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { ChevronDown } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Checkbox } from '@/components/ui/checkbox';
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import {
  createFormSLAConfig,
  updateFormSLAConfig,
  FORM_SLA_EVENT_OPTIONS,
  FORM_SLA_TYPE_LABELS,
  type FormSLAConfig,
  type FormSLAConfigInput,
  type FormSLASourceType,
} from '../../_shared/formSLAService';
import { getSLAPolicies } from '../../sla-policies/services/slaPolicyService';

function MultiEventSelect({
  value,
  options,
  onChange,
  placeholder = 'None',
  label,
}: {
  value: string[];
  options: readonly string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const summary = value.length === 0 ? placeholder : value.join(', ');
  const toggle = (event: string) => {
    if (value.includes(event)) {
      onChange(value.filter((v) => v !== event));
    } else {
      onChange([...value, event]);
    }
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className="flex h-9 w-full items-center justify-between rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
        >
          <span className={value.length === 0 ? 'text-muted-foreground' : ''}>
            {summary}
          </span>
          <ChevronDown className="size-4 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[260px] p-0" align="start">
        <div className="max-h-72 overflow-y-auto p-1">
          {options.map((opt) => {
            const checked = value.includes(opt);
            return (
              <label
                key={opt}
                className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-accent"
              >
                <Checkbox
                  checked={checked}
                  onCheckedChange={() => toggle(opt)}
                />
                <span>{opt}</span>
              </label>
            );
          })}
          {options.length === 0 ? (
            <div className="px-2 py-1.5 text-sm text-muted-foreground">
              No events
            </div>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function parseEventList(s: string | null | undefined): string[] {
  if (!s) return [];
  return s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);
}

function joinEventList(arr: string[]): string | null {
  if (!arr || arr.length === 0) return null;
  return arr.join(',');
}

interface FormSLAConfigDialogProps {
  open: boolean;
  existing: FormSLAConfig | null;
  configs: FormSLAConfig[];
  onClose: () => void;
  onSaved: () => void;
}

const FORM_TYPES: FormSLASourceType[] = [
  'stock_inquiry',
  'purchase_request',
  'sponsorship_form',
  'complaint',
  'price_tag_request',
];

const NONE_VALUE = '__none__';

export default function FormSLAConfigDialog({
  open,
  existing,
  configs,
  onClose,
  onSaved,
}: FormSLAConfigDialogProps) {
  const [sourceType, setSourceType] = useState<FormSLASourceType>('stock_inquiry');
  const [stageCode, setStageCode] = useState('');
  const [policyId, setPolicyId] = useState('');
  const [agentCode, setAgentCode] = useState('');
  const [teamSetCode, setTeamSetCode] = useState('');
  const [startEvent, setStartEvent] = useState('');
  const [respondEvents, setRespondEvents] = useState<string[]>([]);
  const [resolveEvents, setResolveEvents] = useState<string[]>([]);
  const [nextConfigId, setNextConfigId] = useState<string>(NONE_VALUE);
  const [advanceOnEvent, setAdvanceOnEvent] = useState<string>(NONE_VALUE);
  const [isActive, setIsActive] = useState(true);
  const [notifyAssignee, setNotifyAssignee] = useState(true);
  const [notifyOnEscalation, setNotifyOnEscalation] = useState(true);
  // Blank = inherit the global default. Stored as NULL, not 0, so a stage that has
  // never been configured keeps following the system setting rather than pinning
  // itself to "no grace" forever.
  const [graceSeconds, setGraceSeconds] = useState('');
  const [graceChecked, setGraceChecked] = useState(false);
  const graceError = numberFieldError(graceSeconds, { min: 0, max: 600, integer: true, optional: true });

  useEffect(() => {
    if (existing) {
      setSourceType(existing.source_entity_type);
      setStageCode(existing.stage_code);
      setPolicyId(existing.policy_id);
      setAgentCode(existing.agent_code);
      setTeamSetCode(existing.team_set_code ?? '');
      setStartEvent(existing.start_event);
      setRespondEvents(parseEventList(existing.respond_event));
      setResolveEvents(parseEventList(existing.resolve_event));
      setNextConfigId(existing.next_config_id ?? NONE_VALUE);
      setAdvanceOnEvent(existing.advance_on_event ?? NONE_VALUE);
      setIsActive(existing.is_active);
      setNotifyAssignee(existing.notify_assignee ?? true);
      setNotifyOnEscalation(existing.notify_on_escalation ?? true);
      setGraceSeconds(
        existing.grace_seconds === null || existing.grace_seconds === undefined
          ? ''
          : String(existing.grace_seconds),
      );
    } else if (open) {
      setSourceType('stock_inquiry');
      setStageCode('');
      setPolicyId('');
      setAgentCode('');
      setTeamSetCode('');
      setStartEvent('');
      setRespondEvents([]);
      setResolveEvents([]);
      setNextConfigId(NONE_VALUE);
      setAdvanceOnEvent(NONE_VALUE);
      setIsActive(true);
      setNotifyAssignee(true);
      setNotifyOnEscalation(true);
      // Without this, a new stage silently inherits the last-viewed stage's grace
      // as an explicit pin instead of blank-inherits-global.
      setGraceSeconds('');
    }
  }, [existing, open]);

  useEffect(() => {
    if (open) setGraceChecked(false);
  }, [open, existing]);

  const policiesQuery = useQuery({
    queryKey: ['sla-policies', 'select'],
    queryFn: () =>
      getSLAPolicies({
        pageIndex: 0,
        pageSize: 100,
        sorting: [{ id: 'name', desc: false }],
        searchQuery: '',
      }),
    enabled: open,
  });

  const eventOptions = FORM_SLA_EVENT_OPTIONS[sourceType] ?? [];

  const sameTypeConfigs = useMemo(
    () =>
      configs.filter(
        (c) => c.source_entity_type === sourceType && c.id !== existing?.id,
      ),
    [configs, sourceType, existing],
  );

  const saveMut = useMutation({
    mutationFn: async (body: FormSLAConfigInput) => {
      if (existing) {
        return updateFormSLAConfig(existing.id, body);
      }
      return createFormSLAConfig(body);
    },
    onSuccess: () => {
      toast.success(existing ? 'Configuration updated' : 'Configuration created');
      onSaved();
    },
    onError: (e: Error) => {
      toast.error(e.message || 'Save failed');
    },
  });

  const submit = () => {
    if (!stageCode.trim()) return toast.error('Stage code required');
    if (!policyId) return toast.error('Policy required');
    if (!agentCode.trim()) return toast.error('Agent code required');
    if (!startEvent) return toast.error('Start event required');
    if (graceError) {
      setGraceChecked(true);
      return;
    }
    saveMut.mutate({
      source_entity_type: sourceType,
      stage_code: stageCode.trim(),
      policy_id: policyId,
      agent_code: agentCode.trim(),
      team_set_code: teamSetCode.trim() || null,
      start_event: startEvent,
      respond_event: joinEventList(respondEvents),
      resolve_event: joinEventList(resolveEvents),
      next_config_id: nextConfigId === NONE_VALUE ? null : nextConfigId,
      advance_on_event: advanceOnEvent === NONE_VALUE ? null : advanceOnEvent,
      is_active: isActive,
      notify_assignee: notifyAssignee,
      notify_on_escalation: notifyOnEscalation,
      grace_seconds: graceSeconds.trim() === '' ? null : Number(graceSeconds),
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {existing ? 'Edit form SLA stage' : 'Add form SLA stage'}
          </DialogTitle>
        </DialogHeader>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="grid gap-1.5">
            <Label>Form type</Label>
            <SearchableSelect
              value={sourceType}
              onChange={(v) => setSourceType(v as FormSLASourceType)}
              disabled={!!existing}
              options={FORM_TYPES.map((t) => ({
                value: t,
                label: FORM_SLA_TYPE_LABELS[t],
              }))}
            />
          </div>

          <div className="grid gap-1.5">
            <Label>Stage code</Label>
            <Input
              value={stageCode}
              onChange={(e) => setStageCode(e.target.value)}
              placeholder="e.g. project_sales, purchasing"
            />
          </div>

          <div className="grid gap-1.5">
            <Label>SLA policy</Label>
            <SearchableSelect
              value={policyId}
              onChange={setPolicyId}
              options={(policiesQuery.data?.data ?? []).map((p) => ({
                value: p.id,
                label: `${p.name} (${p.code})`,
              }))}
              placeholder="Select policy"
            />
          </div>

          <div className="grid gap-1.5">
            <Label>Access agent code</Label>
            <Input
              value={agentCode}
              onChange={(e) => setAgentCode(e.target.value)}
              placeholder="e.g. lead_time_enquiries"
            />
          </div>

          <div className="grid gap-1.5">
            <Label>Team set code (optional)</Label>
            <Input
              value={teamSetCode}
              onChange={(e) => setTeamSetCode(e.target.value)}
              placeholder="e.g. purchasing"
            />
          </div>

          <div className="grid gap-1.5">
            <Label>Start event</Label>
            <SearchableSelect
              value={startEvent}
              onChange={setStartEvent}
              options={eventOptions.map((e) => ({ value: e, label: e }))}
              placeholder="When does the timer start?"
            />
          </div>

          <div className="grid gap-1.5">
            <Label>Respond event(s) (optional)</Label>
            <MultiEventSelect
              label="Respond events"
              value={respondEvents}
              options={eventOptions}
              onChange={setRespondEvents}
              placeholder="None"
            />
            <span className="text-xs text-muted-foreground">
              Any selected event marks tracker as responded.
            </span>
          </div>

          <div className="grid gap-1.5">
            <Label>Resolve event(s) (optional)</Label>
            <MultiEventSelect
              label="Resolve events"
              value={resolveEvents}
              options={eventOptions}
              onChange={setResolveEvents}
              placeholder="None"
            />
            <span className="text-xs text-muted-foreground">
              Any selected event marks tracker as resolved (e.g. approved + rejected).
            </span>
          </div>

          <div className="grid gap-1.5">
            <Label>Next stage on resolve (optional)</Label>
            <SearchableSelect
              value={nextConfigId}
              onChange={setNextConfigId}
              options={[
                { value: NONE_VALUE, label: 'None' },
                ...sameTypeConfigs.map((c) => ({
                  value: c.id,
                  label: c.stage_code,
                })),
              ]}
              placeholder="None"
            />
          </div>

          <div className="grid gap-1.5">
            <Label>Advance to next stage only on event (optional)</Label>
            <SearchableSelect
              value={advanceOnEvent}
              onChange={setAdvanceOnEvent}
              options={[
                { value: NONE_VALUE, label: 'Any resolve event' },
                ...eventOptions.map((e) => ({ value: e, label: e })),
              ]}
              placeholder="Any resolve event"
            />
            <span className="text-xs text-muted-foreground">
              Spawn the next stage only when resolved by this event (e.g. approved →
              customer service, while rejected just closes the stage). Blank = any.
            </span>
          </div>

          <div className="flex items-center gap-2">
            <Switch checked={isActive} onCheckedChange={setIsActive} id="active" />
            <Label htmlFor="active">Active</Label>
          </div>

          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Switch
                checked={notifyAssignee}
                onCheckedChange={setNotifyAssignee}
                id="notify-assignee"
              />
              <Label htmlFor="notify-assignee">Notify assignee on assignment</Label>
            </div>
            <span className="text-xs text-muted-foreground">
              When off, this stage assigns the tracker silently - the assignee is
              not notified when the stage spawns.
            </span>
          </div>

          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Switch
                checked={notifyOnEscalation}
                onCheckedChange={setNotifyOnEscalation}
                id="notify-on-escalation"
              />
              <Label htmlFor="notify-on-escalation">Notify on escalation</Label>
            </div>
            <span className="text-xs text-muted-foreground">
              When off, escalating a tracker on this stage reassigns silently - the
              new assignee is not notified.
            </span>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="grace-seconds">Undo grace window (seconds)</Label>
            <NumberInput
              id="grace-seconds"
              min={0}
              max={600}
              inputMode="numeric"
              placeholder="Leave blank to use the system default"
              value={graceSeconds}
              onChange={(e) => setGraceSeconds(e.target.value)}
              error={graceChecked ? graceError : undefined}
            />
            <span className="text-xs text-muted-foreground">
              How long an in-app action on this stage waits before it applies. During
              the wait nothing is written and nobody is notified, so the person who
              clicked can take it back. 0 disables the wait.
            </span>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saveMut.isPending}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saveMut.isPending}>
            {saveMut.isPending ? 'Saving…' : 'Save SLA config'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

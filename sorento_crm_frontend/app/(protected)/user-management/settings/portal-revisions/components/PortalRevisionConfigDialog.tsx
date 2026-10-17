'use client';

import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import {
  Dialog,
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
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import {
  listFormSLAConfigs,
  type FormSLASourceType,
} from '@/app/(protected)/sla-management/_shared/formSLAService';

import type {
  PortalRevisionConfig,
  PortalRevisionConfigInput,
} from '../services/portalRevisionConfigService';
import {
  PORTAL_REVISION_STATUS_OPTIONS,
  portalRevisionTypeLabel,
} from '../lib/portal-revision-options';

/** Edit one per-type revision policy. Modal by default, per the CRUD UX standard. */
export interface PortalRevisionConfigDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  config: PortalRevisionConfig | null;
  isSaving?: boolean;
  onSave: (input: PortalRevisionConfigInput) => Promise<unknown> | void;
}

const INHERIT = '__inherit__';

export function PortalRevisionConfigDialog({
  open,
  onOpenChange,
  config,
  isSaving = false,
  onSave,
}: PortalRevisionConfigDialogProps) {
  const [isEnabled, setIsEnabled] = useState(false);
  const [maxRevisions, setMaxRevisions] = useState('');
  const [allowedStatuses, setAllowedStatuses] = useState<string[]>([]);
  const [restartStageCode, setRestartStageCode] = useState<string>(INHERIT);

  useEffect(() => {
    if (!open || !config) return;
    setIsEnabled(Boolean(config.is_enabled));
    setMaxRevisions(config.max_revisions == null ? '' : String(config.max_revisions));
    setMaxChecked(false);
    setAllowedStatuses(config.allowed_statuses ?? []);
    setRestartStageCode(config.restart_stage_code ?? INHERIT);
  }, [open, config]);

  const sourceType = config?.source_entity_type ?? '';

  // Real stage codes off this type's own SLA chain, so the restart target can
  // never be a typo. Empty = the chain has no configured stages yet.
  const { data: slaConfigs } = useQuery({
    queryKey: ['form-sla-config', 'stages', sourceType],
    queryFn: () =>
      listFormSLAConfigs({ source_entity_type: sourceType as FormSLASourceType }),
    enabled: open && !!sourceType,
  });

  const stageOptions = useMemo(() => {
    const codes = Array.from(
      new Set((slaConfigs ?? []).map((c) => c.stage_code).filter(Boolean)),
    );
    return [
      { value: INHERIT, label: 'First stage of the chain' },
      ...codes.map((code) => ({ value: code, label: code.replace(/[_-]+/g, ' ') })),
    ];
  }, [slaConfigs]);

  const statusOptions = useMemo(
    () =>
      (PORTAL_REVISION_STATUS_OPTIONS[sourceType] ?? []).map((option) => ({
        value: option.value,
        label: option.label,
      })),
    [sourceType],
  );

  // Blank inherits the global; a typed value must be a whole number from 0 to 50.
  const [maxChecked, setMaxChecked] = useState(false);
  const maxError = numberFieldError(maxRevisions, { min: 0, max: 50, integer: true, optional: true });

  const handleSave = async () => {
    if (maxError) {
      setMaxChecked(true);
      return;
    }
    await onSave({
      is_enabled: isEnabled,
      max_revisions: maxRevisions.trim() === '' ? null : Number(maxRevisions.trim()),
      allowed_statuses: allowedStatuses,
      restart_stage_code: restartStageCode === INHERIT ? null : restartStageCode,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {sourceType ? portalRevisionTypeLabel(sourceType) : 'Portal revisions'}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-5 py-2">
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="portal-revision-enabled">Enabled</Label>
            <Switch
              id="portal-revision-enabled"
              checked={isEnabled}
              onCheckedChange={setIsEnabled}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="portal-revision-max">Max revisions</Label>
            <NumberInput
              id="portal-revision-max"
              inputMode="numeric"
              min={0}
              max={50}
              value={maxRevisions}
              onChange={(e) => setMaxRevisions(e.target.value)}
              placeholder="Inherit global"
              error={maxChecked ? maxError : undefined}
            />
          </div>

          <div className="space-y-2">
            <Label>Allowed statuses</Label>
            <SearchableMultiSelect
              value={allowedStatuses}
              onChange={setAllowedStatuses}
              options={statusOptions}
              placeholder="Select statuses"
              emptyMessage="No statuses"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="portal-revision-restart">Restart stage</Label>
            <SearchableSelect
              id="portal-revision-restart"
              value={restartStageCode}
              onChange={setRestartStageCode}
              options={stageOptions}
              placeholder="First stage of the chain"
              emptyMessage="No stages configured"
            />
          </div>
        </div>

        <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={isSaving}>
            {isSaving ? 'Saving…' : 'Save revision policy'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

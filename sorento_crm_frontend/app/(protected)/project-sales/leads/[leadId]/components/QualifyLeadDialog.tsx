'use client';

import * as React from 'react';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { ClashWarningPanel } from '../../../pipeline/components/ClashWarningPanel';
import {
  useLeadMutations,
  useProjectParties,
  useProjectTemplates,
  useProjectTypes,
  useQualifyPreview,
} from '../../../_shared/hooks/useProjects';
import type { ProjectLead } from '../../../_shared/types/project.types';

/**
 * Qualify: the moment a rumour becomes a claim (AC-O4).
 *
 * This is the ONLY place a lead meets the registration lock, and the clash panel is
 * the same component the register form uses, fed by the same matcher and thresholds.
 * A different-looking warning here would read as a different rule.
 *
 * The title is editable because one lead may become several projects (AC-O5): a
 * masterplan sighting is qualified once per phase, each with its own name.
 */
export function QualifyLeadDialog({
  lead,
  onDone,
}: {
  lead: ProjectLead;
  onDone: () => void;
}) {
  const { qualify } = useLeadMutations();
  const [title, setTitle] = React.useState(lead.title);
  const [developerPartyId, setDeveloperPartyId] = React.useState(
    lead.developer_party_id ?? '',
  );
  const [typeId, setTypeId] = React.useState('');
  const [templateId, setTemplateId] = React.useState('');

  const developers = useProjectParties({ party_type: 'developer', limit: 200 });
  const types = useProjectTypes();
  const templates = useProjectTemplates(typeId || undefined);

  const preview = useQualifyPreview(lead.id, {
    title,
    developerPartyId: developerPartyId || null,
    enabled: title.trim().length >= 4,
  });

  const candidates = preview.data?.candidates ?? [];
  const wouldBlock = preview.data?.would_block ?? false;

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-lg overflow-hidden">
        <DialogHeader>
          <DialogTitle>Qualify {lead.lead_code}</DialogTitle>
          <DialogDescription>
            This registers the development and locks it to its owner. Everything the lead
            already knows is carried over.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            await qualify.mutateAsync({
              id: lead.id,
              body: {
                title: title.trim(),
                developer_party_id: developerPartyId || null,
                type_id: typeId || null,
                template_id: templateId || null,
              },
            });
            onDone();
          }}
        >
          <DialogBody className="max-h-[62vh] space-y-4 overflow-y-auto">
            <div className="space-y-1.5">
              <Label htmlFor="qualify-title">
                Project title <span className="text-destructive">*</span>
              </Label>
              <Input
                id="qualify-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
              />
              <p className="text-xs text-muted-foreground">
                Split a masterplan here. Qualify once per phase, each with its own name.
              </p>
            </div>

            <ClashWarningPanel
              candidates={candidates}
              isLoading={preview.isFetching}
              developerChosen={Boolean(developerPartyId)}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="qualify-developer">Developer</Label>
                <SearchableSelect
                  id="qualify-developer"
                  value={developerPartyId}
                  onChange={setDeveloperPartyId}
                  clearable
                  loadError={developers.error}
                  onRetry={() => void developers.refetch()}
                  options={(developers.data?.data ?? []).map((party) => ({
                    value: party.id,
                    label: party.name,
                  }))}
                  placeholder="Search developers"
                  emptyMessage="No developers yet. Add one under Parties"
                />
                <p className="text-xs text-muted-foreground">
                  The lock is per developer, so naming one here sharpens the duplicate
                  check.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="qualify-type">Project type</Label>
                <SearchableSelect
                  id="qualify-type"
                  value={typeId}
                  onChange={(next) => {
                    setTypeId(next);
                    setTemplateId('');
                  }}
                  clearable
                  loadError={types.error}
                  onRetry={() => void types.refetch()}
                  options={(types.data ?? []).map((type) => ({
                    value: type.id,
                    label: type.name,
                  }))}
                  placeholder="Select a type"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="qualify-template">Template</Label>
                <SearchableSelect
                  id="qualify-template"
                  value={templateId}
                  onChange={setTemplateId}
                  clearable
                  disabled={!typeId}
                  loadError={templates.error}
                  onRetry={() => void templates.refetch()}
                  options={(templates.data ?? []).map((template) => ({
                    value: template.id,
                    label: template.name,
                  }))}
                  placeholder={typeId ? 'Select a template' : 'Pick a type first'}
                />
                <p className="text-xs text-muted-foreground">
                  The template decides the stakeholder roles and copies in its checklist.
                </p>
              </div>
            </div>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!title.trim() || wouldBlock || qualify.isPending}
            >
              {wouldBlock ? 'Blocked by an existing project' : 'Qualify and register'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

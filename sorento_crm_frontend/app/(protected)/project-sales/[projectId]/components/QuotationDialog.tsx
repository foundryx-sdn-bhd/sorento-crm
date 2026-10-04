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
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useProjectSeries, useQuotationMutations } from '../../_shared/hooks/useProjects';
import type { Project, ProjectQuotation } from '../../_shared/types/project.types';

const SCOPE_SUGGESTIONS = ['House Units', 'Common Area', 'Showroom', 'Clubhouse'];

/**
 * Add or rename a priced scope.
 *
 * The series is the reason this dialog exists rather than a single inline field: it is
 * what decides whether a line counts as non-standard (AC-E5). Leaving it empty is a
 * legitimate answer, and it means "do not judge the lines against a catalogue" rather
 * than "everything is standard", so the copy has to say so.
 */
export function QuotationDialog({
  project,
  quotation,
  onDone,
  onSubmit,
}: {
  project: Project;
  quotation: ProjectQuotation | null;
  onDone: () => void;
  /**
   * The quotation form page (#1341): the scope's name, series and notes are staged and saved
   * with the form, so the dialog hands them over instead of writing.
   */
  onSubmit?: (body: { scope_label: string; series_id: string | null; notes: string | null }) => void;
}) {
  const { create, update } = useQuotationMutations(project.id);
  const series = useProjectSeries();

  const [scopeLabel, setScopeLabel] = React.useState(quotation?.scope_label ?? '');
  const [seriesId, setSeriesId] = React.useState(quotation?.series_id ?? '');
  const [notes, setNotes] = React.useState(quotation?.notes ?? '');

  const isEdit = Boolean(quotation);
  const pending = create.isPending || update.isPending;

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-lg overflow-hidden">
        <DialogHeader>
          <DialogTitle>
            {isEdit
              ? quotation?.scope_label
                ? `Edit "${quotation.scope_label}"`
                : 'Edit scope'
              : 'Add a scope'}
          </DialogTitle>
          <DialogDescription>
            One quotation per scope, because each one is won or lost on its own.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            const body = {
              scope_label: scopeLabel.trim(),
              series_id: seriesId || null,
              notes: notes.trim() || null,
            };
            if (onSubmit) {
              onSubmit(body);
            } else if (quotation) {
              await update.mutateAsync({ id: quotation.id, body });
            } else {
              await create.mutateAsync(body);
            }
            onDone();
          }}
        >
          <DialogBody className="max-h-[65vh] space-y-4 overflow-y-auto">
            <div className="space-y-1.5">
              <Label htmlFor="quotation-scope">
                Scope <span className="text-destructive">*</span>
              </Label>
              <Input
                id="quotation-scope"
                value={scopeLabel}
                onChange={(event) => setScopeLabel(event.target.value)}
                placeholder="House Units"
                required
              />
              {!isEdit && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {SCOPE_SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => setScopeLabel(suggestion)}
                      className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="quotation-series">Series</Label>
              <SearchableSelect
                id="quotation-series"
                value={seriesId}
                onChange={setSeriesId}
                clearable
                loadError={series.error}
                onRetry={() => void series.refetch()}
                options={(series.data ?? [])
                  .filter((row) => row.is_active || row.id === quotation?.series_id)
                  .map((row) => ({
                    value: row.id,
                    label: row.name,
                    description: row.brand_name ?? undefined,
                  }))}
                placeholder="No series"
                emptyMessage="No series configured yet"
              />
              <p className="text-xs text-muted-foreground">
                Anything priced outside the series is flagged as non-standard. Leave it
                empty and no line is judged against a catalogue.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="quotation-notes">Notes</Label>
              <Textarea
                id="quotation-notes"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                rows={3}
                placeholder="Who asked for it, what the drawing covers, anything the next version has to keep"
              />
            </div>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={!scopeLabel.trim() || pending}>
              {isEdit ? 'Save changes' : 'Add scope'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

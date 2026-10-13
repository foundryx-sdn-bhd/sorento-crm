'use client';

import { useEffect, useMemo, useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { normalizeUseCases } from '@/lib/ideaUseCases';
import { useIdeaMutations, useIdeasQuery } from '@/hooks/useIdeas';
import type { Idea, IdeaMergeFieldKey, IdeaMergeInput } from '@/types/ideas';

const FIELD_ROWS: Array<{ key: IdeaMergeFieldKey; label: string }> = [
  { key: 'title', label: 'Title' },
  { key: 'problem', label: 'Business problem' },
  { key: 'desiredOutcome', label: 'Desired outcome' },
  { key: 'useCases', label: 'Use cases' },
  { key: 'currentWorkaround', label: 'Current workaround' },
  { key: 'rawText', label: 'Original message' },
];

const COLUMN_CLASS = 'min-w-48 w-48 shrink-0';

function ideaName(idea: Idea) {
  return idea.ideaNumber ?? idea.title ?? 'Idea';
}

function CellValue({ idea, field }: { idea: Idea; field: IdeaMergeFieldKey }) {
  if (field === 'useCases') {
    const lines = normalizeUseCases(idea.useCases);
    return lines.length > 0 ? (
      <div className="flex flex-col gap-0.5">
        {lines.map((line, index) => (
          <div key={index}>{line}</div>
        ))}
      </div>
    ) : (
      <span className="text-muted-foreground">-</span>
    );
  }
  const value = idea[field];
  return value ? (
    <span className="whitespace-pre-wrap break-words">{value}</span>
  ) : (
    <span className="text-muted-foreground">-</span>
  );
}

/**
 * Merge ideas into one: pick the ideas, pick the one to keep, then choose per field whose value
 * the survivor takes. By default the survivor's own value stays; `fields` is sent only for the
 * rows whose ticks differ from that.
 */
export function IdeaMergeModal({
  ideas,
  open,
  onOpenChange,
  onMerged,
}: {
  ideas: Idea[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Runs after a successful merge, before the modal closes. */
  onMerged?: () => void;
}) {
  const { data: list } = useIdeasQuery({});
  const { merge } = useIdeaMutations();
  const initialKey = ideas.map((i) => i.id).join(',');
  const [selectedIds, setSelectedIds] = useState<string[]>(() => ideas.map((i) => i.id));
  const [survivorId, setSurvivorId] = useState(() => ideas[0]?.id ?? '');
  // Explicit tick sets per row; a row not listed follows the survivor.
  const [overrides, setOverrides] = useState<Partial<Record<IdeaMergeFieldKey, string[]>>>({});
  const [attempted, setAttempted] = useState(false);

  useEffect(() => {
    if (!open) return;
    const ids = initialKey ? initialKey.split(',') : [];
    setSelectedIds(ids);
    setSurvivorId(ids[0] ?? '');
    setOverrides({});
    setAttempted(false);
  }, [open, initialKey]);

  const known = useMemo(() => {
    const byId = new Map<string, Idea>();
    for (const idea of list ?? []) byId.set(idea.id, idea);
    for (const idea of ideas) byId.set(idea.id, idea);
    return byId;
  }, [list, ideas]);

  const options = useMemo(
    () =>
      Array.from(known.values())
        .filter((i) => !i.mergedIntoId)
        .map((i) => ({
          value: i.id,
          label: [i.ideaNumber, i.title ?? i.problem].filter(Boolean).join(' - '),
        })),
    [known],
  );

  const columns = selectedIds.map((id) => known.get(id)).filter((i): i is Idea => !!i);
  const effectiveSurvivor = columns.some((c) => c.id === survivorId) ? survivorId : (columns[0]?.id ?? '');

  const ticksFor = (key: IdeaMergeFieldKey) => overrides[key] ?? [effectiveSurvivor];
  const emptyRows = FIELD_ROWS.filter((row) => !columns.some((c) => ticksFor(row.key).includes(c.id)));

  const onSelect = (ids: string[]) => {
    setSelectedIds(ids);
    setOverrides((current) => {
      const next: typeof current = {};
      for (const [key, picked] of Object.entries(current)) {
        next[key as IdeaMergeFieldKey] = (picked ?? []).filter((id) => ids.includes(id));
      }
      return next;
    });
  };

  const toggle = (key: IdeaMergeFieldKey, id: string) => {
    const current = ticksFor(key);
    setOverrides((prev) => ({
      ...prev,
      [key]: current.includes(id) ? current.filter((x) => x !== id) : [...current, id],
    }));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (columns.length < 2 || !effectiveSurvivor || merge.isPending) return;
    setAttempted(true);
    if (emptyRows.length > 0) return;
    const fields: NonNullable<IdeaMergeInput['fields']> = {};
    for (const row of FIELD_ROWS) {
      const picked = columns.map((c) => c.id).filter((id) => ticksFor(row.key).includes(id));
      const survivorOnly = picked.length === 1 && picked[0] === effectiveSurvivor;
      if (!survivorOnly) fields[row.key] = picked;
    }
    const input: IdeaMergeInput = { survivorId: effectiveSurvivor, ideaIds: columns.map((c) => c.id) };
    if (Object.keys(fields).length > 0) input.fields = fields;
    try {
      await merge.mutateAsync(input);
      onMerged?.();
      onOpenChange(false);
    } catch {
      // The hook toasted the reason; the modal stays open.
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Merge ideas</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="flex min-w-0 flex-col gap-4">
          <DialogBody className="flex min-w-0 flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="idea-merge-ideas">Ideas to merge</Label>
              <SearchableMultiSelect
                id="idea-merge-ideas"
                value={selectedIds}
                onChange={onSelect}
                options={options}
                placeholder="Pick ideas"
                emptyMessage="No other ideas."
                wrapOptions
              />
            </div>
            <div className="max-w-full overflow-x-auto">
              <RadioGroup
                value={effectiveSurvivor}
                onValueChange={setSurvivorId}
                className="w-max gap-0"
              >
                <div className="flex border-b border-border">
                  <div className="w-32 shrink-0 p-2" />
                  {columns.map((idea) => (
                    <div key={idea.id} className={`${COLUMN_CLASS} flex items-center gap-2 p-2`}>
                      <RadioGroupItem
                        value={idea.id}
                        id={`merge-keep-${idea.id}`}
                        aria-label={`Keep ${ideaName(idea)}`}
                      />
                      <span className="truncate text-sm font-medium" title={ideaName(idea)}>
                        {ideaName(idea)}
                      </span>
                    </div>
                  ))}
                </div>
                {FIELD_ROWS.map((row) => (
                  <div key={row.key} className="flex border-b border-border last:border-b-0">
                    <div className="w-32 shrink-0 p-2 text-sm font-medium">{row.label}</div>
                    {columns.map((idea) => (
                      <div key={idea.id} className={`${COLUMN_CLASS} flex items-start gap-2 p-2 text-sm`}>
                        <Checkbox
                          size="sm"
                          className="mt-0.5"
                          aria-label={`${row.label} from ${ideaName(idea)}`}
                          checked={ticksFor(row.key).includes(idea.id)}
                          onCheckedChange={() => toggle(row.key, idea.id)}
                        />
                        <div className="min-w-0 flex-1">
                          <CellValue idea={idea} field={row.key} />
                        </div>
                      </div>
                    ))}
                  </div>
                ))}
              </RadioGroup>
            </div>
            {attempted && emptyRows.length > 0 ? (
              <div className="flex flex-col gap-1 text-sm text-destructive" role="alert">
                {emptyRows.map((row) => (
                  <p key={row.key}>Pick at least one value for {row.label}</p>
                ))}
              </div>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={columns.length < 2 || merge.isPending}>
              {merge.isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
              Merge
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

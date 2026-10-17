'use client';

import * as React from 'react';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import {
  useTemplateTaskMutations,
  useTemplateTasks,
} from '../../_shared/hooks/useProjects';
import { groupTasksByCategory } from '../../_shared/lib/taskGrouping';
import type {
  ProjectTemplate,
  ProjectTemplateTask,
  TaskPhase,
} from '../../_shared/types/project.types';

const PHASE_OPTIONS = [
  { value: 'pursuit', label: 'Pursuit', description: 'Work that wins the project' },
  { value: 'delivery', label: 'Delivery', description: 'Work that fulfils it once won' },
];

/**
 * The checklist every new project of this template starts with.
 *
 * Editing it never touches projects that already exist. A project's tasks are COPIES
 * taken at registration, deliberately: a live pursuit whose checklist silently changed
 * underneath the salesperson is worse than one that is slightly out of date.
 *
 * Which is why delete is blocked once a checklist item has been copied anywhere. The
 * copies are what carry the audit trail, and `in_use_count` is how the server says so.
 */
export function TemplateChecklistPanel({ template }: { template: ProjectTemplate }) {
  const tasks = useTemplateTasks(template.id);
  const { remove, update } = useTemplateTaskMutations(template.id);
  const [editing, setEditing] = React.useState<ProjectTemplateTask | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [deleting, setDeleting] = React.useState<ProjectTemplateTask | null>(null);

  const rows = React.useMemo(() => tasks.data ?? [], [tasks.data]);
  // Same grouping as the project's own Tasks tab, so an admin sees the shape the
  // salesperson will get rather than a flat list that reads differently.
  const groups = React.useMemo(
    () =>
      groupTasksByCategory(
        rows.map((row) => ({
          ...row,
          id: row.id,
          project_id: template.id,
          is_open: row.is_active,
          is_overdue: false,
          can_edit: true,
        })),
      ),
    [rows, template.id],
  );

  const knownCategories = [...new Set(rows.map((row) => row.category).filter(Boolean) as string[])];

  return (
    <>
      <Card>
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <CardTitle className="text-sm">
              Starting checklist for {template.name}
            </CardTitle>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Copied into every new project of this template. Existing projects keep the
              checklist they were registered with.
            </p>
          </div>
          <Button type="button" size="sm" onClick={() => setCreating(true)}>
            <Plus className="size-4" aria-hidden />
            Add item
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {tasks.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : tasks.isError ? (
            <p className="text-sm text-destructive">
              {tasks.error instanceof Error
                ? tasks.error.message
                : 'The checklist could not be loaded.'}
            </p>
          ) : rows.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center">
              <h3 className="text-sm font-semibold">This template has no checklist</h3>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                A project registered against it starts with nothing to do, so its next
                action stays empty until somebody adds a task by hand.
              </p>
              <Button type="button" className="mt-4" onClick={() => setCreating(true)}>
                <Plus className="size-4" aria-hidden />
                Add the first item
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              {groups.map((group) => (
                <section key={group.label} className="rounded-lg border border-border">
                  <header className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
                    <span className="min-w-0 truncate text-sm font-medium" title={group.label}>
                      {group.label}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {group.total} item{group.total === 1 ? '' : 's'}
                    </span>
                  </header>
                  <ul className="divide-y divide-border">
                    {group.tasks.map((task) => {
                      const row = rows.find((candidate) => candidate.id === task.id);
                      if (!row) return null;
                      return (
                        <li
                          key={row.id}
                          className="flex flex-col gap-2 px-3 py-2.5 sm:flex-row sm:items-center sm:gap-3"
                        >
                          <div className="min-w-0 flex-1 space-y-1">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <p
                                className={
                                  row.is_active
                                    ? 'truncate text-sm font-medium'
                                    : 'truncate text-sm text-muted-foreground line-through'
                                }
                                title={row.name}
                              >
                                {row.name}
                              </p>
                              <Badge variant="outline" className="text-[11px] capitalize">
                                {row.task_phase}
                              </Badge>
                              {!row.is_active && (
                                <Badge variant="secondary" className="text-[11px]">
                                  Inactive
                                </Badge>
                              )}
                            </div>
                            <p className="text-xs text-muted-foreground">
                              {row.default_offset_days === null ||
                              row.default_offset_days === undefined
                                ? 'No default due date'
                                : `Due ${row.default_offset_days} day${row.default_offset_days === 1 ? '' : 's'} after registration`}
                              {row.in_use_count > 0
                                ? ` · used by ${row.in_use_count} project task${row.in_use_count === 1 ? '' : 's'}`
                                : ''}
                            </p>
                            {row.description && (
                              <p className="break-words text-xs text-muted-foreground">
                                {row.description}
                              </p>
                            )}
                          </div>
                          <div className="flex shrink-0 gap-1">
                            <Button
                              mode="icon"
                              variant="ghost"
                              size="sm"
                              onClick={() => setEditing(row)}
                              aria-label={`Edit ${row.name}`}
                            >
                              <Pencil className="size-3.5" />
                            </Button>
                            <Button
                              mode="icon"
                              variant="ghost"
                              size="sm"
                              onClick={() => setDeleting(row)}
                              aria-label={`Delete ${row.name}`}
                            >
                              <Trash2 className="size-3.5 text-destructive" />
                            </Button>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {(creating || editing) && (
        <ChecklistItemDialog
          templateId={template.id}
          item={editing}
          knownCategories={knownCategories}
          nextSortOrder={rows.length * 10}
          onDone={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      )}

      {/* Two different conversations, so two different dialogs. An in-use item cannot
          be deleted at all, and offering a Delete button the server is certain to
          refuse teaches the user that the app is unreliable. */}
      {deleting && deleting.in_use_count > 0 ? (
        <Dialog open onOpenChange={(next) => !next && setDeleting(null)}>
          <DialogContent className="w-full max-w-md">
            <DialogHeader>
              <DialogTitle>This item cannot be deleted</DialogTitle>
              <DialogDescription>
                &quot;{deleting.name}&quot; has already been copied into{' '}
                {deleting.in_use_count} project task
                {deleting.in_use_count === 1 ? '' : 's'}. Those copies carry their own
                history, so deleting the template item would leave them orphaned.
                {deleting.is_active
                  ? ' Deactivate it instead to keep it off future projects.'
                  : ' It is already inactive, so no new project will copy it.'}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
              <Button type="button" variant="outline" onClick={() => setDeleting(null)}>
                Cancel
              </Button>
              {deleting.is_active && (
                <Button
                  type="button"
                  disabled={update.isPending}
                  onClick={async () => {
                    await update.mutateAsync({
                      id: deleting.id,
                      body: { is_active: false },
                    });
                    setDeleting(null);
                  }}
                >
                  Deactivate instead
                </Button>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : (
        <ConfirmDeleteDialog
          open={Boolean(deleting)}
          onOpenChange={(next) => !next && setDeleting(null)}
          title="Confirm delete"
          description={
            deleting
              ? `Delete the checklist item "${deleting.name}"? This action cannot be undone. Projects already registered keep their copy.`
              : ''
          }
          onDelete={async () => {
            if (!deleting) return;
            await remove.mutateAsync(deleting.id);
          }}
          onSuccess={() => setDeleting(null)}
          successMessage="Checklist item removed"
        />
      )}
    </>
  );
}

function ChecklistItemDialog({
  templateId,
  item,
  knownCategories,
  nextSortOrder,
  onDone,
}: {
  templateId: string;
  item: ProjectTemplateTask | null;
  knownCategories: string[];
  nextSortOrder: number;
  onDone: () => void;
}) {
  const { create, update } = useTemplateTaskMutations(templateId);
  const [name, setName] = React.useState(item?.name ?? '');
  const [description, setDescription] = React.useState(item?.description ?? '');
  const [phase, setPhase] = React.useState<string>(item?.task_phase ?? 'pursuit');
  const [category, setCategory] = React.useState(item?.category ?? '');
  const [offset, setOffset] = React.useState(
    item?.default_offset_days === null || item?.default_offset_days === undefined
      ? ''
      : String(item.default_offset_days),
  );
  const [sortOrder, setSortOrder] = React.useState(
    String(item?.sort_order ?? nextSortOrder),
  );
  const [isActive, setIsActive] = React.useState(item?.is_active ?? true);

  const [showErrors, setShowErrors] = React.useState(false);
  const offsetError = numberFieldError(offset, { min: 0, integer: true, optional: true });
  const sortError = numberFieldError(sortOrder, { integer: true });

  const isEdit = Boolean(item);
  const pending = create.isPending || update.isPending;

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-lg overflow-hidden">
        <DialogHeader>
          <DialogTitle>{isEdit ? `Edit "${item?.name}"` : 'Add a checklist item'}</DialogTitle>
          <DialogDescription>
            Its due date is set from registration day plus the offset. Leave the offset
            empty for work that has to happen but not by a date.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (offsetError || sortError) {
              setShowErrors(true);
              return;
            }
            const body = {
              name: name.trim(),
              description: description.trim() || null,
              task_phase: phase as TaskPhase,
              category: category.trim() || null,
              default_offset_days: offset.trim() === '' ? null : Number(offset),
              sort_order: Number(sortOrder),
              is_active: isActive,
            };
            if (item) {
              await update.mutateAsync({ id: item.id, body });
            } else {
              await create.mutateAsync(body);
            }
            onDone();
          }}
        >
          <DialogBody className="max-h-[65vh] space-y-4 overflow-y-auto">
            <div className="space-y-1.5">
              <Label htmlFor="checklist-name">
                Item <span className="text-destructive">*</span>
              </Label>
              <Input
                id="checklist-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Confirm the specified finish with the architect"
                required
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="checklist-phase">Phase</Label>
                <SearchableSelect
                  id="checklist-phase"
                  value={phase}
                  onChange={setPhase}
                  options={PHASE_OPTIONS}
                  placeholder="Select a phase"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="checklist-offset">Due, days after registration</Label>
                <NumberInput
                  id="checklist-offset"
                  min={0}
                  value={offset}
                  error={showErrors ? offsetError : undefined}
                  onChange={(event) => setOffset(event.target.value)}
                  placeholder="No default"
                />
              </div>

              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="checklist-category">Work-stream</Label>
                <Input
                  id="checklist-category"
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                  placeholder="Spec-in, Sampling, Commercial"
                />
                {knownCategories.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {knownCategories.map((known) => (
                      <button
                        key={known}
                        type="button"
                        onClick={() => setCategory(known)}
                        className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted"
                      >
                        {known}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="checklist-sort">Order</Label>
                <NumberInput
                  id="checklist-sort"
                  value={sortOrder}
                  error={showErrors ? sortError : undefined}
                  onChange={(event) => setSortOrder(event.target.value)}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="checklist-description">Notes</Label>
              <Textarea
                id="checklist-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={3}
                placeholder="What good looks like, and who to ask"
              />
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={isActive}
                onChange={(event) => setIsActive(event.target.checked)}
                className="size-4 rounded border-border"
              />
              Active. Inactive items stop copying into new projects
            </label>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || pending}>
              {isEdit ? 'Save changes' : 'Add item'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

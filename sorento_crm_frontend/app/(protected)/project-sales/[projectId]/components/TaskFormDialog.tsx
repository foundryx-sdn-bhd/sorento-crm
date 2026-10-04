'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
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
import { getUserLookup } from '@/services/userSelectService';
import type { Status } from '@/app/(protected)/system-management/status-graphs/types/statusGraph.types';
import { useTaskMutations } from '../../_shared/hooks/useProjects';
import type {
  Project,
  ProjectTask,
  TaskPhase,
} from '../../_shared/types/project.types';

const PHASE_OPTIONS = [
  { value: 'pursuit', label: 'Pursuit', description: 'Work that wins the project' },
  { value: 'delivery', label: 'Delivery', description: 'Work that fulfils it once won' },
];

/**
 * Add or edit one task.
 *
 * Category is free text on purpose. Work-streams differ per project type and the
 * template author invents them, so a fixed enum would go stale the first time a new
 * kind of job turns up. Known categories on this project are offered as one-click
 * chips, which keeps spelling consistent without forbidding a new one.
 *
 * Status is only offered on CREATE. Changing it afterwards goes through the status
 * dialog, because escalate and stuck demand context this form does not collect.
 */
export function TaskFormDialog({
  project,
  task,
  statuses,
  defaultPhase,
  knownCategories,
  onDone,
}: {
  project: Project;
  task: ProjectTask | null;
  statuses: Status[];
  defaultPhase: TaskPhase;
  knownCategories: string[];
  onDone: () => void;
}) {
  const { create, update } = useTaskMutations(project.id);

  const [name, setName] = React.useState(task?.name ?? '');
  const [description, setDescription] = React.useState(task?.description ?? '');
  const [phase, setPhase] = React.useState<string>(task?.task_phase ?? defaultPhase);
  const [category, setCategory] = React.useState(task?.category ?? '');
  const [assignee, setAssignee] = React.useState(task?.assignee_user_id ?? '');
  const [startDate, setStartDate] = React.useState(task?.start_date ?? '');
  const [dueDate, setDueDate] = React.useState(task?.due_date ?? '');
  const [statusId, setStatusId] = React.useState(
    task?.status_id ?? statuses.find((status) => status.is_initial)?.id ?? '',
  );

  const users = useQuery({
    queryKey: ['user-lookup', 'task-assignee'],
    queryFn: () => getUserLookup(),
  });

  const isEdit = Boolean(task);
  const pending = create.isPending || update.isPending;

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-lg overflow-hidden">
        <DialogHeader>
          <DialogTitle>{isEdit ? `Edit "${task?.name}"` : 'Add a task'}</DialogTitle>
          <DialogDescription>
            A dated task is what drives this project&apos;s next action. Leave the due
            date empty for something that has to happen but not by a date.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            const body = {
              name: name.trim(),
              description: description.trim() || null,
              task_phase: phase as TaskPhase,
              category: category.trim() || null,
              assignee_user_id: assignee || null,
              start_date: startDate || null,
              due_date: dueDate || null,
            };
            if (task) {
              await update.mutateAsync({ id: task.id, body });
            } else {
              await create.mutateAsync({ ...body, status_id: statusId || null });
            }
            onDone();
          }}
        >
          <DialogBody className="max-h-[65vh] space-y-4 overflow-y-auto">
            <div className="space-y-1.5">
              <Label htmlFor="task-name">
                Task <span className="text-destructive">*</span>
              </Label>
              <Input
                id="task-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Get the finish specified in the tender drawing"
                required
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="task-phase">Phase</Label>
                <SearchableSelect
                  id="task-phase"
                  value={phase}
                  onChange={setPhase}
                  options={PHASE_OPTIONS}
                  placeholder="Select a phase"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="task-assignee">Assignee</Label>
                <SearchableSelect
                  id="task-assignee"
                  value={assignee}
                  onChange={setAssignee}
                  clearable
                  loadError={users.error}
                  onRetry={() => void users.refetch()}
                  options={(users.data ?? []).map((user) => ({
                    value: user.id,
                    label: user.name || 'Unnamed user',
                  }))}
                  placeholder="Unassigned"
                  emptyMessage="No active users found"
                />
              </div>

              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="task-category">Work-stream</Label>
                <Input
                  id="task-category"
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
                <Label htmlFor="task-start">Start date</Label>
                <Input
                  id="task-start"
                  type="date"
                  value={startDate}
                  onChange={(event) => setStartDate(event.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="task-due">Due date</Label>
                <Input
                  id="task-due"
                  type="date"
                  value={dueDate}
                  onChange={(event) => setDueDate(event.target.value)}
                />
              </div>

              {!isEdit && statuses.length > 0 && (
                <div className="space-y-1.5 sm:col-span-2">
                  <Label htmlFor="task-status">Starting status</Label>
                  <SearchableSelect
                    id="task-status"
                    value={statusId}
                    onChange={setStatusId}
                    options={statuses
                      .filter((status) => !status.is_terminal)
                      .map((status) => ({ value: status.id, label: status.label }))}
                    placeholder="Select a status"
                  />
                </div>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="task-description">Notes</Label>
              <Textarea
                id="task-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                rows={3}
                placeholder="What good looks like, who to talk to, what we already tried"
              />
            </div>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || pending}>
              {isEdit ? 'Save changes' : 'Add task'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

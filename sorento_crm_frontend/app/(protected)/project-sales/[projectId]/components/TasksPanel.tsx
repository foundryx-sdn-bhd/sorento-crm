'use client';

import * as React from 'react';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Clock,
  History,
  Pencil,
  Plus,
  Trash2,
  TriangleAlert,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useProjectStatusGraph } from '@/app/(protected)/project-sales/_shared/hooks/useProjectStatusGraph';
import type { Status } from '@/app/(protected)/system-management/status-graphs/types/statusGraph.types';
import { useProjectTasks, useTaskMutations } from '../../_shared/hooks/useProjects';
import {
  defaultPhaseForProject,
  groupTasksByCategory,
  requiredContextForStatus,
} from '../../_shared/lib/taskGrouping';
import type { Project, ProjectTask, TaskPhase } from '../../_shared/types/project.types';
import { TaskFormDialog } from './TaskFormDialog';
import { TaskHistoryDialog } from './TaskHistoryDialog';
import { TaskStatusDialog } from './TaskStatusDialog';
import { TaskTimelineView } from './TaskTimelineView';

const PHASE_TABS: { id: TaskPhase | 'all'; label: string }[] = [
  { id: 'pursuit', label: 'Pursuit' },
  { id: 'delivery', label: 'Delivery' },
  { id: 'all', label: 'Both' },
];

/**
 * Sections is the default because it is where the work gets DONE (each row carries its
 * own status control). Timeline answers a different question -- when does this land --
 * and is read-only by design.
 */
const VIEWS = [
  { id: 'sections', label: 'Sections' },
  { id: 'timeline', label: 'Timeline' },
] as const;

type TaskView = (typeof VIEWS)[number]['id'];

/**
 * The project's checklist, grouped by WORK-STREAM and never by status.
 *
 * A status-column kanban would tell you that four things are in progress and hide
 * which four parts of the job they belong to. Grouping by category answers the
 * question the salesperson actually asks ("where is spec-in up to?"), with each task
 * carrying its own status inside the section.
 *
 * Two moves are not one click: escalating needs somebody to escalate TO, and going
 * stuck needs a reason. Both are collected in the same request as the status change so
 * the task is never briefly escalated to nobody.
 */
export function TasksPanel({ project }: { project: Project }) {
  const [phase, setPhase] = React.useState<TaskPhase | 'all'>(() =>
    defaultPhaseForProject(project),
  );
  const [view, setView] = React.useState<TaskView>('sections');
  const tasks = useProjectTasks(project.id, phase === 'all' ? undefined : phase);
  const graph = useProjectStatusGraph('project_task', project.template_id ?? null);
  const { remove } = useTaskMutations(project.id);

  const [creating, setCreating] = React.useState(false);
  const [editing, setEditing] = React.useState<ProjectTask | null>(null);
  const [moving, setMoving] = React.useState<{ task: ProjectTask; status: Status } | null>(null);
  const [viewingHistory, setViewingHistory] = React.useState<ProjectTask | null>(null);
  const [deleting, setDeleting] = React.useState<ProjectTask | null>(null);

  const rows = React.useMemo(() => tasks.data ?? [], [tasks.data]);
  const groups = React.useMemo(() => groupTasksByCategory(rows), [rows]);
  const statuses = React.useMemo(
    () =>
      (graph.data?.statuses ?? [])
        .filter((status) => status.is_active)
        .sort((a, b) => a.sort_order - b.sort_order),
    [graph.data],
  );

  const openTotal = rows.filter((task) => task.is_open).length;
  const overdueTotal = rows.filter((task) => task.is_overdue).length;

  return (
    <>
      <Card>
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <CardTitle className="text-sm">Tasks</CardTitle>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div
              className="flex rounded-md border border-border p-0.5"
              role="tablist"
              aria-label="Task phase"
            >
              {PHASE_TABS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={phase === tab.id}
                  onClick={() => setPhase(tab.id)}
                  className={
                    phase === tab.id
                      ? 'rounded px-2.5 py-1 text-xs font-medium bg-muted text-foreground'
                      : 'rounded px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground'
                  }
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <div
              className="flex rounded-md border border-border p-0.5"
              role="tablist"
              aria-label="Task view"
            >
              {VIEWS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  role="tab"
                  aria-selected={view === option.id}
                  onClick={() => setView(option.id)}
                  className={
                    view === option.id
                      ? 'rounded px-2.5 py-1 text-xs font-medium bg-muted text-foreground'
                      : 'rounded px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground'
                  }
                >
                  {option.label}
                </button>
              ))}
            </div>
            {project.can_edit && (
              <Button type="button" size="sm" onClick={() => setCreating(true)}>
                <Plus className="size-4" aria-hidden />
                Add task
              </Button>
            )}
          </div>
        </CardHeader>

        <CardContent className="space-y-3">
          {rows.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>
                {openTotal} open of {rows.length}
              </span>
              {overdueTotal > 0 && (
                <Badge variant="destructive" className="gap-1 text-[11px]">
                  <AlertTriangle className="size-3" aria-hidden />
                  {`${overdueTotal} overdue`}
                </Badge>
              )}
            </div>
          )}

          {tasks.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          ) : tasks.isError ? (
            <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-8 text-center">
              <h3 className="text-sm font-semibold text-destructive">
                Tasks could not be loaded
              </h3>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                {tasks.error instanceof Error ? tasks.error.message : 'Try again shortly.'}
              </p>
            </div>
          ) : rows.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center">
              <h3 className="text-sm font-semibold">
                {phase === 'all'
                  ? 'No tasks on this project'
                  : `No ${phase} tasks on this project`}
              </h3>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                {project.template_id
                  ? 'The template checklist copies in when a project is registered against it. Add a task by hand for anything the checklist does not cover.'
                  : 'Set a project type and template so the checklist copies in, or add tasks by hand.'}
              </p>
              {project.can_edit && (
                <Button type="button" className="mt-4" onClick={() => setCreating(true)}>
                  <Plus className="size-4" aria-hidden />
                  Add the first task
                </Button>
              )}
            </div>
          ) : view === 'timeline' ? (
            <TaskTimelineView tasks={rows} />
          ) : (
            <div className="space-y-2">
              {groups.map((group) => (
                <TaskCategorySection
                  key={group.label}
                  label={group.label}
                  total={group.total}
                  openCount={group.openCount}
                  overdueCount={group.overdueCount}
                >
                  <ul className="divide-y divide-border">
                    {group.tasks.map((task) => (
                      <TaskRow
                        key={task.id}
                        task={task}
                        statuses={statuses}
                        statusError={graph.error}
                        onRetryStatuses={() => void graph.refetch()}
                        showPhase={phase === 'all'}
                        onMove={(status) => setMoving({ task, status })}
                        onEdit={() => setEditing(task)}
                        onHistory={() => setViewingHistory(task)}
                        onDelete={() => setDeleting(task)}
                      />
                    ))}
                  </ul>
                </TaskCategorySection>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {(creating || editing) && (
        <TaskFormDialog
          project={project}
          task={editing}
          statuses={statuses}
          defaultPhase={phase === 'all' ? defaultPhaseForProject(project) : phase}
          knownCategories={[
            ...new Set(rows.map((task) => task.category).filter(Boolean) as string[]),
          ]}
          onDone={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      )}

      {moving && (
        <TaskStatusDialog
          project={project}
          task={moving.task}
          status={moving.status}
          requires={requiredContextForStatus(moving.status.key)}
          onDone={() => setMoving(null)}
        />
      )}

      {viewingHistory && (
        <TaskHistoryDialog
          projectId={project.id}
          task={viewingHistory}
          onDone={() => setViewingHistory(null)}
        />
      )}

      <ConfirmDeleteDialog
        open={Boolean(deleting)}
        onOpenChange={(next) => !next && setDeleting(null)}
        title="Confirm delete"
        description={
          deleting
            ? `Delete the task "${deleting.name}"? This action cannot be undone, and its history goes with it.`
            : ''
        }
        onDelete={async () => {
          if (!deleting) return;
          await remove.mutateAsync(deleting.id);
        }}
        onSuccess={() => setDeleting(null)}
        successMessage="Task deleted"
      />
    </>
  );
}

/**
 * Collapsible per work-stream. Sections with nothing open start collapsed: a finished
 * work-stream is context, not the thing the user came here to act on.
 */
function TaskCategorySection({
  label,
  total,
  openCount,
  overdueCount,
  children,
}: {
  label: string;
  total: number;
  openCount: number;
  overdueCount: number;
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(openCount > 0);

  return (
    <section className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/50"
      >
        {open ? (
          <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        ) : (
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        )}
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={label}>
          {label}
        </span>
        {overdueCount > 0 && (
          <Badge variant="destructive" className="text-[11px]">
            {`${overdueCount} overdue`}
          </Badge>
        )}
        <span className="shrink-0 text-xs text-muted-foreground">
          {openCount} open / {total}
        </span>
      </button>
      {open && <div className="border-t border-border">{children}</div>}
    </section>
  );
}

function TaskRow({
  task,
  statuses,
  statusError,
  onRetryStatuses,
  showPhase,
  onMove,
  onEdit,
  onHistory,
  onDelete,
}: {
  task: ProjectTask;
  statuses: Status[];
  statusError?: unknown;
  onRetryStatuses?: () => void;
  showPhase: boolean;
  onMove: (status: Status) => void;
  onEdit: () => void;
  onHistory: () => void;
  onDelete: () => void;
}) {
  return (
    <li className="flex flex-col gap-2 px-3 py-2.5 sm:flex-row sm:items-center sm:gap-3">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <p
            className={
              task.is_open
                ? 'truncate text-sm font-medium'
                : 'truncate text-sm text-muted-foreground line-through'
            }
            title={task.name}
          >
            {task.name}
          </p>
          {showPhase && (
            <Badge variant="outline" className="text-[11px] capitalize">
              {task.task_phase}
            </Badge>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {task.assignee_name && <span className="truncate">{task.assignee_name}</span>}
          {task.due_date && (
            <span
              className={
                task.is_overdue
                  ? 'flex items-center gap-1 font-medium text-destructive'
                  : 'flex items-center gap-1'
              }
            >
              <Clock className="size-3" aria-hidden />
              {formatTaskDate(task.due_date)}
              {task.is_overdue ? ' (overdue)' : dueSuffix(task.days_until_due)}
            </span>
          )}
          {task.is_open && !task.due_date && <span>No due date</span>}
          {task.escalated_to_name && (
            <span className="flex items-center gap-1 text-amber-600 dark:text-amber-500">
              <TriangleAlert className="size-3" aria-hidden />
              Escalated to {task.escalated_to_name}
            </span>
          )}
        </div>

        {task.stuck_reason && (
          <p className="break-words rounded-md bg-muted/60 px-2 py-1 text-xs text-muted-foreground">
            Stuck: {task.stuck_reason}
          </p>
        )}
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-1.5">
        {task.can_edit && (statuses.length > 0 || Boolean(statusError)) ? (
          <div className="w-full sm:w-40">
            <SearchableSelect
              value={task.status_id ?? ''}
              onChange={(next) => {
                if (!next || next === task.status_id) return;
                const status = statuses.find((candidate) => candidate.id === next);
                if (status) onMove(status);
              }}
              options={statuses.map((status) => ({ value: status.id, label: status.label }))}
              loadError={statusError}
              onRetry={onRetryStatuses}
              placeholder="No status"
              aria-label={`Status of ${task.name}`}
            />
          </div>
        ) : (
          <Badge variant="secondary" className="text-[11px]">
            {task.status_label ?? 'No status'}
          </Badge>
        )}
        <Button
          mode="icon"
          variant="ghost"
          size="sm"
          onClick={onHistory}
          aria-label={`History of ${task.name}`}
        >
          <History className="size-3.5" />
        </Button>
        {task.can_edit && (
          <>
            <Button
              mode="icon"
              variant="ghost"
              size="sm"
              onClick={onEdit}
              aria-label={`Edit ${task.name}`}
            >
              <Pencil className="size-3.5" />
            </Button>
            <Button
              mode="icon"
              variant="ghost"
              size="sm"
              onClick={onDelete}
              aria-label={`Delete ${task.name}`}
            >
              <Trash2 className="size-3.5 text-destructive" />
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

function dueSuffix(days?: number | null): string {
  if (days === null || days === undefined) return '';
  if (days === 0) return ' (today)';
  if (days === 1) return ' (tomorrow)';
  return ` (in ${days} days)`;
}

export function formatTaskDate(iso?: string | null): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString('en-MY', { day: '2-digit', month: 'short', year: 'numeric' });
}

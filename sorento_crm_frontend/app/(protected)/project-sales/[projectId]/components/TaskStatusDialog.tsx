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
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { getUserLookup } from '@/services/userSelectService';
import type { Status } from '@/app/(protected)/system-management/status-graphs/types/statusGraph.types';
import { useTaskMutations } from '../../_shared/hooks/useProjects';
import type { Project, ProjectTask } from '../../_shared/types/project.types';

/**
 * The status move plus whatever context that status demands, in ONE request.
 *
 * Escalating without a target, or going stuck without a reason, is exactly the state
 * the server refuses. Collecting it here means the user never sees that rejection, and
 * there is no window where the task sits escalated to nobody.
 *
 * An ordinary rung needs nothing, so this dialog never opens for it: the caller applies
 * the move directly on mount.
 */
export function TaskStatusDialog({
  project,
  task,
  status,
  requires,
  onDone,
}: {
  project: Project;
  task: ProjectTask;
  status: Status;
  requires: 'escalated_to_user_id' | 'stuck_reason' | null;
  onDone: () => void;
}) {
  const { move } = useTaskMutations(project.id);
  const [escalateTo, setEscalateTo] = React.useState(task.escalated_to_user_id ?? '');
  const [reason, setReason] = React.useState(task.stuck_reason ?? '');
  const applied = React.useRef(false);

  const users = useQuery({
    queryKey: ['user-lookup', 'task-escalation'],
    queryFn: () => getUserLookup(),
    enabled: requires === 'escalated_to_user_id',
  });

  // No context needed: fire the move immediately rather than making the user confirm a
  // dialog that asks nothing. The ref guards against StrictMode's double effect.
  React.useEffect(() => {
    if (requires || applied.current) return;
    applied.current = true;
    move
      .mutateAsync({ id: task.id, body: { to_status_id: status.id } })
      .finally(() => onDone());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requires]);

  if (!requires) return null;

  const valid =
    requires === 'escalated_to_user_id' ? Boolean(escalateTo) : reason.trim().length > 0;

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-md overflow-hidden">
        <DialogHeader>
          <DialogTitle>
            {requires === 'escalated_to_user_id'
              ? `Escalate "${task.name}"`
              : `Flag "${task.name}" as stuck`}
          </DialogTitle>
          <DialogDescription>
            {requires === 'escalated_to_user_id'
              ? 'Escalation needs somebody to escalate to. They see it in My Tasks straight away.'
              : 'A stuck task without a reason is just a stalled task. Say what is blocking it so somebody can unblock it.'}
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            await move.mutateAsync({
              id: task.id,
              body: {
                to_status_id: status.id,
                escalated_to_user_id:
                  requires === 'escalated_to_user_id' ? escalateTo : undefined,
                stuck_reason: requires === 'stuck_reason' ? reason.trim() : undefined,
              },
            });
            onDone();
          }}
        >
          <DialogBody className="max-h-[60vh] space-y-4 overflow-y-auto">
            {requires === 'escalated_to_user_id' ? (
              <div className="space-y-1.5">
                <Label htmlFor="task-escalate-to">
                  Escalate to <span className="text-destructive">*</span>
                </Label>
                <SearchableSelect
                  id="task-escalate-to"
                  value={escalateTo}
                  onChange={setEscalateTo}
                  loadError={users.error}
                  onRetry={() => void users.refetch()}
                  options={(users.data ?? []).map((user) => ({
                    value: user.id,
                    label: user.name || 'Unnamed user',
                  }))}
                  placeholder="Select a person"
                  emptyMessage="No active users found"
                />
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label htmlFor="task-stuck-reason">
                  What is blocking it <span className="text-destructive">*</span>
                </Label>
                <Textarea
                  id="task-stuck-reason"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  rows={4}
                  placeholder="Waiting on the architect to confirm the finish before we can quote"
                  required
                />
              </div>
            )}
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={!valid || move.isPending}>
              {requires === 'escalated_to_user_id' ? 'Escalate' : 'Flag as stuck'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

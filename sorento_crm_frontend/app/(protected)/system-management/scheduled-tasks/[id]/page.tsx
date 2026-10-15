'use client';

import { use } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { MoveLeft, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { toast } from '@/lib/toast';
import RequireAccess from '@/app/components/common/RequireAccess';
import {
  useScheduledTask,
  useUpdateScheduledTask,
  useRunScheduledTaskNow,
} from '../hooks/useScheduledTasks';
import { ScheduledTaskForm } from '../components/ScheduledTaskForm';
import { RunLogsTable } from '../components/RunLogsTable';
import type { FormValues } from '../components/ScheduledTaskForm';
import { mapFormToUpdateBody } from '../lib/mapFormToUpdateBody';

type DetailPageProps = {
  params: Promise<{ id: string }>;
};

export default function ScheduledTaskDetailPage({ params }: DetailPageProps) {
  const { id } = use(params);
  // Admin only, like the list page: the server takes the admin role for the edit and
  // for Run now (ROUTE-PERM-GATES).
  return (
    <RequireAccess superadmin>
      <ScheduledTaskDetail id={id} />
    </RequireAccess>
  );
}

function ScheduledTaskDetail({ id }: { id: string }) {
  const router = useRouter();

  const { data: task, isLoading } = useScheduledTask(id);
  const updateMutation = useUpdateScheduledTask(id);
  const runNowMutation = useRunScheduledTaskNow(id);

  const handleSubmit = (values: FormValues) => {
    if (!task) return;
    updateMutation.mutate(mapFormToUpdateBody(values, task), {
      onSuccess: () => toast.success('Task updated'),
      onError: (e) => toast.error(e instanceof Error ? e.message : 'Update failed'),
    });
  };

  const handleRunNow = () => {
    runNowMutation.mutate(undefined, {
      onSuccess: (result) => {
        if (result.status === 'success') {
          toast.success('Task run started');
        } else if (result.status === 'failed') {
          toast.error(result.error || 'Run failed');
        } else {
          toast.info(result.status);
        }
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : 'Run failed'),
    });
  };

  if (isLoading) {
    return (
      <>
        <Container>
          <PageHeader title="Scheduled Task" />
        </Container>
        <Container>
          <div className="space-y-6">
            <Skeleton className="h-10 w-64" />
            <Skeleton className="h-96 w-full" />
          </div>
        </Container>
      </>
    );
  }

  if (!task) {
    return (
      <>
        <Container>
          <PageHeader title="Scheduled Task" />
        </Container>
        <Container>
          <div className="text-center py-12">
            <p className="text-muted-foreground">Task not found</p>
            <Button
              variant="outline"
              onClick={() => router.push('/system-management/scheduled-tasks')}
              className="mt-4"
            >
              Back to Scheduled Tasks
            </Button>
          </div>
        </Container>
      </>
    );
  }

  return (
    <>
      <Container>
        <PageHeader
          title={task.name}
          actions={
            <>
              <Button asChild variant="outline">
                <Link href="/system-management/scheduled-tasks">
                  <MoveLeft /> Back to list
                </Link>
              </Button>
              <Button
                variant="primary"
                onClick={handleRunNow}
                disabled={runNowMutation.isPending}
              >
                <Play className="size-4 mr-2" />
                {runNowMutation.isPending ? 'Running...' : 'Run now'}
              </Button>
            </>
          }
        />
      </Container>

      <Container>
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Task config</CardTitle>
                <Badge status={task.last_status ?? ''}>
                  {task.last_status ?? '-'}
                </Badge>
              </div>
              {task.description && (
                <p className="text-sm text-muted-foreground">{task.description}</p>
              )}
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 text-sm mb-6">
                <div>
                  <p className="text-muted-foreground">Key</p>
                  <p className="font-medium">{task.key}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Next run</p>
                  <p className="font-medium">
                    {task.next_run_at
                      ? formatDateTimeInMalaysia(task.next_run_at)
                      : '-'}
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground">Last run</p>
                  <p className="font-medium">
                    {task.last_run_at
                      ? formatDateTimeInMalaysia(task.last_run_at)
                      : '-'}
                  </p>
                </div>
                {task.last_error && (
                  <div className="sm:col-span-2">
                    <p className="text-muted-foreground">Last error</p>
                    <p className="font-medium text-destructive text-xs truncate" title={task.last_error}>
                      {task.last_error}
                    </p>
                  </div>
                )}
              </div>
              <ScheduledTaskForm
                task={task}
                onSubmit={handleSubmit}
                isSubmitting={updateMutation.isPending}
              />
            </CardContent>
          </Card>

          <div>
            <h2 className="text-lg font-semibold mb-3">Run logs</h2>
            <RunLogsTable taskId={task.id} />
          </div>
        </div>
      </Container>
    </>
  );
}

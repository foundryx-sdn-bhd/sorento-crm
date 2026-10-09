'use client';

import { History } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import DetailActions from '@/components/common/DetailActions';
import { formatDateSafe, formatDateTimeSafe } from '@/lib/helpers';
import { useGrantQuery, useGrantRevocation } from '../hooks/useClaudeConnector';
import { grantsPagerQuery } from '../lib/grantsPager';
import type { GrantCall } from '../types/claudeConnector.types';

const LIST_PATH = '/user-management/claude-connector';

const CALL_STATUS: Record<string, { label: string; variant: 'success' | 'destructive' | 'warning' }> = {
  ok: { label: 'OK', variant: 'success' },
  denied: { label: 'Denied', variant: 'destructive' },
  error: { label: 'Error', variant: 'destructive' },
  rejected: { label: 'Rejected', variant: 'warning' },
};

function CallStatus({ status }: { status: string }) {
  const entry = CALL_STATUS[status] ?? { label: status, variant: 'warning' as const };
  return (
    <Badge variant={entry.variant} appearance="light" size="sm">
      {entry.label}
    </Badge>
  );
}

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm font-medium">{children}</span>
    </div>
  );
}

/** One grant: header (user, status, prev/next, Revoke), metadata directly under it, and the
 *  Recent tool calls tab. */
export default function GrantDetail({ id }: { id: string }) {
  const { data: grant, isLoading, isError } = useGrantQuery(id);
  const revocation = useGrantRevocation();

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  if (isError || !grant) {
    return (
      <Card className="flex flex-col items-center gap-2 p-10 text-center">
        <div className="text-sm font-semibold">The grant could not be loaded</div>
      </Card>
    );
  }

  const calls: GrantCall[] = grant.recent_calls ?? [];
  const parked = revocation.targetId === grant.id && revocation.countdown;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="block py-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h2 className="min-w-0 break-words text-lg font-semibold">{grant.user_name}</h2>
              <Badge status={grant.status} appearance="light">
                {grant.status}
              </Badge>
            </div>
            <DetailActions
              pager={{
                ...grantsPagerQuery,
                detailPath: LIST_PATH,
                currentId: id,
                ariaLabel: 'grant',
              }}
              pendingAction={parked || undefined}
              primary={
                grant.status === 'Active' ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-destructive"
                    disabled={revocation.isPending}
                    onClick={() => revocation.run({ id: grant.id, subject: grant.user_name })}
                  >
                    Revoke
                  </Button>
                ) : undefined
              }
            />
          </div>
        </CardHeader>
        <div className="grid grid-cols-1 gap-4 border-t border-border px-5 py-4 sm:grid-cols-2 lg:grid-cols-4">
          <Meta label="Client">
            {grant.client_name} <span className="text-muted-foreground">(claude.ai)</span>
          </Meta>
          <Meta label="Created">{formatDateTimeSafe(grant.created_at)}</Meta>
          <Meta label="Last used">{formatDateTimeSafe(grant.last_used_at)}</Meta>
          <Meta label="Expires">{formatDateSafe(grant.expires_at)}</Meta>
        </div>
      </Card>

      <Tabs defaultValue="calls" className="w-full">
        <TabsList variant="line" className="mb-4 w-full justify-start">
          <TabsTrigger value="calls">
            <History />
            <span>Recent tool calls</span>
          </TabsTrigger>
        </TabsList>
        <TabsContent value="calls" className="mt-0 focus-visible:outline-none">
          {calls.length === 0 ? (
            <Card className="flex flex-col items-center gap-1 p-10 text-center">
              <div className="text-sm font-semibold">No tool calls yet</div>
              <div className="text-sm text-muted-foreground">
                Calls made through this grant appear here.
              </div>
            </Card>
          ) : (
            <Card>
              <Table className="table-fixed">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-44">Time</TableHead>
                    <TableHead className="w-64">Tool</TableHead>
                    <TableHead className="w-24 text-right">Rows</TableHead>
                    <TableHead className="w-24 text-right">ms</TableHead>
                    <TableHead className="w-28">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {calls.map((c, i) => (
                    <TableRow key={`${c.time}-${i}`}>
                      <TableCell className="truncate" title={formatDateTimeSafe(c.time)}>
                        {formatDateTimeSafe(c.time)}
                      </TableCell>
                      <TableCell className="truncate font-mono text-xs" title={c.tool}>
                        {c.tool}
                      </TableCell>
                      <TableCell className="text-right">{c.rows == null ? '-' : c.rows.toLocaleString('en-US')}</TableCell>
                      <TableCell className="text-right">{c.ms == null ? '-' : c.ms.toLocaleString('en-US')}</TableCell>
                      <TableCell>
                        <CallStatus status={c.status} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

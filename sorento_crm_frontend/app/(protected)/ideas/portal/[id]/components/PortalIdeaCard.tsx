'use client';

import { useMemo, useState, type ReactNode } from 'react';
import { useSession } from 'next-auth/react';
import { Heart, LoaderCircleIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardHeader } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Container } from '@/components/common/container';
import LoadErrorState from '@/components/common/LoadErrorState';
import {
  CommentThreadView,
  type ThreadComment,
} from '@/components/common/comments/CommentThreadView';
import { useDeletingIds } from '@/components/common/comments/useDeletingIds';
import { IdeaCommentDelete } from '@/components/ideas/IdeaComments';
import { IdeaStatusBadge } from '@/components/ideas/IdeaStatusBadge';
import { VoteBox } from '@/components/ideas/VoteBox';
import { useHasPermission } from '@/hooks/usePermissions';
import {
  usePortalActivityQuery,
  usePortalCommentsQuery,
  usePortalIdeaQuery,
  usePortalMutations,
  usePortalVotersQuery,
} from '@/hooks/useIdeasPortal';
import { formatDateTime } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import type { PortalIdeaDetail, PortalTimelineStep } from '@/types/ideasPortal';

const MANAGE = 'ideation.ideas.manage';
const NOTE_MAX = 500;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <section aria-label={title} className="flex flex-col gap-3 p-5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {children}
      </section>
    </Card>
  );
}

function Empty({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed py-6 text-center">
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="text-sm text-muted-foreground">{hint}</p>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="min-w-0 text-sm">{children}</div>
    </div>
  );
}

function Text({ value }: { value: string | null | undefined }) {
  return value ? (
    <p className="whitespace-pre-wrap break-words">{value}</p>
  ) : (
    <span className="text-muted-foreground">Not set</span>
  );
}

const STEP_DOT: Record<PortalTimelineStep['state'], string> = {
  done: 'bg-primary',
  current: 'bg-primary ring-4 ring-primary/20',
  upcoming: 'bg-muted-foreground/30',
};

function Timeline({ steps }: { steps: PortalTimelineStep[] }) {
  if (steps.length === 0) return <Empty title="No timeline" hint="Status steps appear here." />;
  return (
    <ol className="flex gap-6 overflow-x-auto pb-1">
      {steps.map((step) => (
        <li
          key={step.key}
          aria-current={step.state === 'current' ? 'step' : undefined}
          className="flex shrink-0 items-center gap-2"
        >
          <span className={cn('size-2.5 rounded-full', STEP_DOT[step.state])} aria-hidden />
          <span
            className={cn(
              'text-sm',
              step.state === 'upcoming' ? 'text-muted-foreground' : 'font-medium',
            )}
          >
            {step.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Voters({ id }: { id: string }) {
  const { data, isLoading } = usePortalVotersQuery(id);
  if (isLoading) return <Skeleton className="h-12 w-full" />;
  if (!data || data.length === 0) return <Empty title="No votes yet" hint="People who upvote appear here." />;
  return (
    <ul className="flex flex-col divide-y rounded-lg border">
      {data.map((voter, index) => (
        <li key={`${voter.name}-${index}`} className="flex min-w-0 items-center gap-2 px-3 py-2 text-sm">
          <span className="min-w-0 truncate" title={voter.name}>
            {voter.name}
          </span>
          {voter.isMe ? (
            <Badge variant="outline" size="xs">
              You
            </Badge>
          ) : null}
          {voter.role ? <span className="ms-auto shrink-0 text-xs text-muted-foreground">{voter.role}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function Activity({ id }: { id: string }) {
  const { data, isLoading } = usePortalActivityQuery(id);
  if (isLoading) return <Skeleton className="h-12 w-full" />;
  if (!data || data.length === 0) return <Empty title="No activity yet" hint="Changes to this idea appear here." />;
  return (
    <ul className="flex flex-col divide-y rounded-lg border">
      {data.map((row) => (
        <li key={row.id} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-2 text-sm">
          <span className="min-w-0 break-words">{row.label}</span>
          <span className="ms-auto shrink-0 text-xs text-muted-foreground">
            {[row.actorName, formatDateTime(row.at)].filter(Boolean).join(' - ')}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Comments({ id }: { id: string }) {
  const { data: session } = useSession();
  const { data: comments, isLoading } = usePortalCommentsQuery(id);
  const { comment, like } = usePortalMutations();
  const { isDeleting, report } = useDeletingIds();

  const byId = useMemo(() => new Map((comments ?? []).map((c) => [c.id, c])), [comments]);
  const thread = useMemo<ThreadComment[]>(
    () =>
      (comments ?? []).map((c) => ({
        id: c.id,
        parentId: c.parentId,
        body: c.body,
        authorName: c.authorName,
        createdAt: c.createdAt,
        editedAt: c.editedAt,
        deleted: c.deleted,
        canEdit: false,
        canDelete: c.canDelete,
        canReply: true,
      })),
    [comments],
  );

  return (
    <CommentThreadView
      comments={thread}
      isLoading={isLoading}
      authorName={session?.user?.name ?? ''}
      composerPosition="top"
      readOnly={false}
      emptyHint="Comments on this idea appear here."
      pending={comment.isPending}
      isDeleting={isDeleting}
      onAdd={async (body, _files, parentId) => {
        await comment.mutateAsync({ id, body, parentId });
      }}
      onEdit={async () => undefined}
      renderExtra={(c) => {
        const row = byId.get(c.id);
        if (!row) return null;
        return (
          <Button
            type="button"
            variant="dim"
            size="sm"
            className="h-7 gap-1 px-1.5"
            aria-label={row.likedByMe ? 'Unlike' : 'Like'}
            aria-pressed={row.likedByMe}
            disabled={like.isPending}
            onClick={() => like.mutate({ id, commentId: row.id })}
          >
            <Heart className={cn('size-3.5', row.likedByMe && 'fill-current')} />
            {row.likeCount > 0 ? <span className="tabular-nums">{row.likeCount}</span> : null}
          </Button>
        );
      }}
      renderDelete={(c) => <IdeaCommentDelete ideaId={id} commentId={c.id} onActive={report} />}
    />
  );
}

/** Manager controls: hide the card from the portal, and edit the changelog note in place. */
function ManagerControls({ idea }: { idea: PortalIdeaDetail }) {
  const { hidden, note } = usePortalMutations();
  const [text, setText] = useState(idea.note ?? '');
  const tooLong = text.length > NOTE_MAX;

  const save = () => {
    if (tooLong || note.isPending) return;
    note.mutate({ id: idea.id, note: text });
  };

  return (
    <Section title="Portal">
      <div className="flex items-center gap-3">
        <Switch
          id="portal-hidden"
          checked={!!idea.portalHidden}
          disabled={hidden.isPending}
          onCheckedChange={(value) => hidden.mutate({ id: idea.id, hidden: value })}
        />
        <Label htmlFor="portal-hidden" className="text-sm">
          Hide from portal
        </Label>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="portal-note" className="text-xs text-muted-foreground">
          Changelog note
        </Label>
        <Textarea
          id="portal-note"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          aria-invalid={tooLong}
        />
        <div className="flex items-center justify-between gap-2">
          <span className={cn('text-xs tabular-nums', tooLong ? 'text-destructive' : 'text-muted-foreground')}>
            {text.length} / {NOTE_MAX}
          </span>
          <Button type="button" variant="primary" size="sm" disabled={tooLong || note.isPending} onClick={save}>
            {note.isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
            Save
          </Button>
        </div>
      </div>
    </Section>
  );
}

/** One idea on the Roadmap portal: identity and vote, the four fields, timeline, voters, comments, activity. */
export function PortalIdeaCard({ id }: { id: string }) {
  const canManage = useHasPermission(MANAGE);
  const { data: idea, isLoading, isError, error, refetch, isFetching } = usePortalIdeaQuery(id);
  const { vote } = usePortalMutations();

  if (isLoading) {
    return (
      <Container>
        <div className="space-y-4">
          <Skeleton className="h-28 w-full rounded-xl" />
          <Skeleton className="h-64 w-full rounded-xl" />
        </div>
      </Container>
    );
  }

  if (isError || !idea) {
    return (
      <Container>
        <Card>
          <LoadErrorState
            title="Could not load this idea"
            message={error instanceof Error ? error.message : undefined}
            onRetry={() => void refetch()}
            retrying={isFetching}
          />
        </Card>
      </Container>
    );
  }

  const title = idea.title ?? idea.ideaNumber ?? 'Idea';

  return (
    <Container>
      <div className="space-y-5">
        <Card>
          <CardHeader className="block py-4">
            <div className="flex min-w-0 items-start gap-4">
              <VoteBox
                size="md"
                count={idea.upvotes}
                voted={idea.myVote === true}
                onVote={() => vote.mutate(idea.id)}
              />
              <div className="flex min-w-0 flex-col gap-1.5">
                <span className="text-xs tabular-nums text-muted-foreground">{idea.ideaNumber}</span>
                <h2 className="break-words text-lg font-semibold" title={title}>
                  {title}
                </h2>
                <div className="flex flex-wrap items-center gap-2">
                  <IdeaStatusBadge label={idea.statusLabel} color={idea.statusColor} />
                  {idea.submitterFirstName ? (
                    <span className="text-xs text-muted-foreground">Submitted by {idea.submitterFirstName}</span>
                  ) : null}
                </div>
              </div>
            </div>
          </CardHeader>
        </Card>

        <Section title="Details">
          <div className="flex flex-col gap-4">
            <Field label="Business problem">
              <Text value={idea.problem} />
            </Field>
            <Field label="Desired outcome">
              <Text value={idea.desiredOutcome} />
            </Field>
            <Field label="Use cases">
              {idea.useCases.length > 0 ? (
                <ul className="flex flex-col gap-0.5">
                  {idea.useCases.map((line, index) => (
                    <li key={index} className="break-words">
                      {line}
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="text-muted-foreground">Not set</span>
              )}
            </Field>
            <Field label="Current workaround">
              <Text value={idea.currentWorkaround} />
            </Field>
          </div>
        </Section>

        <Section title="Timeline">
          <Timeline steps={idea.timeline} />
        </Section>

        {canManage ? <ManagerControls idea={idea} /> : null}

        <Section title="Voters">
          <Voters id={id} />
        </Section>

        <Section title="Comments">
          <Comments id={id} />
        </Section>

        <Section title="Activity">
          <Activity id={id} />
        </Section>
      </div>
    </Container>
  );
}

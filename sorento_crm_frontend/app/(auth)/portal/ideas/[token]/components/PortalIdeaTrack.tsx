'use client';

import { useState } from 'react';
import { ChevronUp, LoaderCircleIcon } from 'lucide-react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { usePortalIdea } from '@/hooks/usePortalIdea';
import { formatDate, formatDateTime } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import type { PortalIdeaComment } from '@/types/ideas';
import { IdeaStatusBadge } from '@/components/ideas/IdeaStatusBadge';

function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join('') || '?'
  );
}

function Comment({ comment, reply }: { comment: PortalIdeaComment; reply?: boolean }) {
  const size = reply ? 'size-6' : 'size-8';
  if (comment.deleted) {
    return (
      <div className="flex items-start gap-3 py-3 text-sm text-muted-foreground">
        <Avatar className={size}>
          <AvatarFallback />
        </Avatar>
        <span className="pt-1 italic">Comment deleted</span>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-3 py-3">
      <Avatar className={size}>
        <AvatarFallback className="text-[0.6875rem] font-medium">{initials(comment.authorName)}</AvatarFallback>
      </Avatar>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
          <span className="font-medium">{comment.authorName}</span>
          {comment.isSubmitter ? (
            <Badge variant="outline" size="xs">
              You
            </Badge>
          ) : null}
          <span className="text-xs text-muted-foreground">{formatDateTime(comment.createdAt)}</span>
        </div>
        <p className="whitespace-pre-wrap break-words text-sm">{comment.body}</p>
      </div>
    </div>
  );
}

/** The customer's track page: their idea, its status, and the conversation under it. */
export function PortalIdeaTrack({ token }: { token: string }) {
  const { idea, comments, loading, notFound, error, posting, post } = usePortalIdea(token);
  const [body, setBody] = useState('');

  if (loading) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-12">
        <Skeleton className="mb-4 h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (notFound || error || !idea) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-12">
        <Card className="flex flex-col items-center gap-1 p-10 text-center">
          <h1 className="text-base font-semibold">{notFound ? 'Idea not found' : 'Could not load this idea'}</h1>
          <p className="text-sm text-muted-foreground">
            {notFound ? 'This link is not valid.' : (error ?? 'Try again in a moment.')}
          </p>
        </Card>
      </div>
    );
  }

  const roots = comments.filter((c) => !c.parentId);
  const canPost = body.trim().length > 0 && !posting;
  const submit = async () => {
    if (!canPost) return;
    if (await post(body)) setBody('');
  };

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-5 px-4 py-12">
      <Card className="p-5">
        <div className="flex items-start gap-4">
          <div
            className="flex w-14 shrink-0 flex-col items-center rounded-md border border-input py-2 text-base font-semibold tabular-nums"
            aria-label={`${idea.upvotes} votes`}
          >
            <ChevronUp className="size-5 text-muted-foreground" />
            {idea.upvotes}
          </div>
          <div className="flex min-w-0 flex-col gap-1.5">
            <h1 className="break-words text-lg font-semibold">{idea.title || idea.problem}</h1>
            <div className="flex flex-wrap items-center gap-2">
              <IdeaStatusBadge label={idea.statusLabel} color={idea.statusColor} />
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {idea.ideaNumber ? <span>{idea.ideaNumber}</span> : null}
              {idea.submittedAt ? <span>Submitted {formatDate(idea.submittedAt)}</span> : null}
            </div>
          </div>
        </div>
      </Card>

      <Card className="p-5">
        <section aria-label="Idea" className="flex flex-col gap-4">
          {[
            ['Problem statement', idea.problem],
            ['Proposed solution', idea.proposedSolution],
            ['Impact', idea.impact],
            ['Department', idea.department],
          ]
            .filter(([, value]) => !!value)
            .map(([label, value]) => (
              <div key={label} className="flex min-w-0 flex-col gap-1">
                <span className="text-xs text-muted-foreground">{label}</span>
                <p className="whitespace-pre-wrap break-words text-sm">{value}</p>
              </div>
            ))}
        </section>
      </Card>

      <Card className="p-5">
        <section aria-label="Comments" className="flex flex-col gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            Comments
            <Badge variant="secondary" size="sm" shape="circle">
              {comments.filter((c) => !c.deleted).length}
            </Badge>
          </h2>
          <div className="flex items-start gap-3">
            <Avatar className="size-8">
              <AvatarFallback className="text-[0.6875rem] font-medium">
                {initials(idea.submitterFirstName ?? '')}
              </AvatarFallback>
            </Avatar>
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <Textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                placeholder="Write a comment"
                aria-label="Write a comment"
                rows={3}
              />
              <div className="flex justify-end">
                <Button type="button" variant="primary" size="sm" disabled={!canPost} onClick={submit}>
                  {posting ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
                  Comment
                </Button>
              </div>
            </div>
          </div>
          {roots.length === 0 ? (
            <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed py-6 text-center">
              <span className="text-sm font-medium">No comments yet</span>
              <span className="text-sm text-muted-foreground">Comments on this idea appear here.</span>
            </div>
          ) : (
            <div className="flex flex-col divide-y">
              {roots.map((root) => {
                const replies = comments.filter((c) => c.parentId === root.id);
                return (
                  <div key={root.id}>
                    <Comment comment={root} />
                    {replies.length > 0 ? (
                      <div className={cn('ms-4 border-s ps-4 sm:ms-11')}>
                        {replies.map((reply) => (
                          <Comment key={reply.id} comment={reply} reply />
                        ))}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </Card>
    </div>
  );
}

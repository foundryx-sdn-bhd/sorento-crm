'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { GripVertical, Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Kanban,
  KanbanBoard,
  KanbanColumn,
  KanbanColumnContent,
  KanbanItem,
  KanbanItemHandle,
  KanbanOverlay,
  type KanbanMoveEvent,
} from '@/components/ui/kanban';
import { Skeleton } from '@/components/ui/skeleton';
import { Container } from '@/components/common/container';
import LoadErrorState from '@/components/common/LoadErrorState';
import { toast } from '@/lib/toast';
import { PageHeader } from '@/components/common/PageHeader';
import { useIdeaBoardQuery, useIdeaMutations } from '@/hooks/useIdeas';
import type { Idea } from '@/types/ideas';
import { IdeaCaptureModal } from './IdeaCaptureModal';
import { IdeaStatusBadge } from './IdeaStatusBadge';
import { IdeasViewToggle } from './IdeasViewToggle';
import { VoteBox } from './VoteBox';
import { useCanManageIdeas } from './ideasAccess';

type Columns = Record<string, Idea[]>;

export const NOT_ALLOWED_MESSAGE = 'This idea cannot move to that status.';

function IdeaCard({
  idea,
  canDrag,
  onVote,
}: {
  idea: Idea;
  canDrag: boolean;
  onVote: () => void;
}) {
  const label = idea.title ?? idea.problem;
  return (
    <div className="flex items-start gap-2 rounded-md border bg-background p-2.5">
      <VoteBox
        count={idea.upvotes}
        voted={idea.myVote === 'up'}
        onVote={onVote}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {idea.ideaNumber ? (
          <span className="text-xs tabular-nums text-muted-foreground">
            {idea.ideaNumber}
          </span>
        ) : null}
        <Link
          href={`/ideas/${idea.id}`}
          className="line-clamp-2 text-sm font-medium text-primary [overflow-wrap:anywhere] hover:underline"
          title={label}
        >
          {label}
        </Link>
        <span className="truncate text-xs text-muted-foreground">
          {idea.submitterName}
        </span>
      </div>
      {canDrag ? (
        <KanbanItemHandle asChild>
          <button
            type="button"
            aria-label="Drag to reorder"
            className="-me-1 shrink-0 rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <GripVertical className="size-4" />
          </button>
        </KanbanItemHandle>
      ) : null}
    </div>
  );
}

/**
 * The triage board: lanes from the tenant's status set, drag to reorder within a lane or to move
 * a card to another status. A move the server refuses puts the card back and says why.
 */
export function IdeasBoardView() {
  const canManage = useCanManageIdeas();
  const [modalOpen, setModalOpen] = useState(false);
  const {
    data,
    isLoading,
    isError,
    error,
    dataUpdatedAt,
    refetch,
    isFetching,
  } = useIdeaBoardQuery();
  const { vote, move, reorder } = useIdeaMutations();
  // Every lane key exists from the first render: the server's lanes are the base, and a drag
  // only layers an optimistic override on top of them.
  const base = useMemo<Columns>(
    () =>
      Object.fromEntries(
        (data?.columns ?? []).map((c) => [c.statusId, c.ideas]),
      ),
    [data],
  );
  const [override, setOverride] = useState<Columns | null>(null);
  const columns = override ?? base;
  const setColumns = setOverride;

  // `dataUpdatedAt`, not `data`: a refused move refetches identical data, and the board still has
  // to snap back to it.
  useEffect(() => {
    setOverride(null);
  }, [dataUpdatedAt]);

  const handleMove = ({
    event,
    activeContainer,
    activeIndex,
    overContainer,
    overIndex,
  }: KanbanMoveEvent) => {
    const ideaId = String(event.active.id);
    const source = columns[activeContainer] ?? [];
    const target = columns[overContainer] ?? [];
    const card = source[activeIndex];
    if (!card) return;

    if (activeContainer === overContainer) {
      const next = [...source];
      next.splice(activeIndex, 1);
      next.splice(Math.min(overIndex, next.length), 0, card);
      if (next.every((idea, i) => idea.id === source[i].id)) return;
      setColumns({ ...columns, [activeContainer]: next });
      reorder.mutate(next.map((idea) => idea.id));
      return;
    }

    // A card moves only along one of its own transitions; anything else never reaches ss.
    const transition = card.transitions.find(
      (t) => t.toStatusId === overContainer,
    );
    if (!transition) {
      setOverride(null);
      toast.error(NOT_ALLOWED_MESSAGE);
      return;
    }

    const nextSource = source.filter((idea) => idea.id !== ideaId);
    const nextTarget = [...target];
    nextTarget.splice(Math.min(overIndex, nextTarget.length), 0, card);
    setColumns({
      ...columns,
      [activeContainer]: nextSource,
      [overContainer]: nextTarget,
    });
    move.mutate(
      { id: ideaId, toStatusId: overContainer },
      { onSuccess: () => reorder.mutate(nextTarget.map((idea) => idea.id)) },
    );
  };

  return (
    <>
      <Container>
        <PageHeader
          title="Ideas board"
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <IdeasViewToggle active="board" />
              <Button variant="primary" onClick={() => setModalOpen(true)}>
                <Plus className="size-4" />
                Capture idea
              </Button>
            </div>
          }
        />
      </Container>
      <Container>
        <div className="space-y-3">
          {isError && !data ? (
            <LoadErrorState
              className="rounded-lg border"
              title="Could not load the board"
              message={error instanceof Error ? error.message : undefined}
              onRetry={() => void refetch()}
              retrying={isFetching}
            />
          ) : isLoading || !data ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-64 w-full" />
              ))}
            </div>
          ) : (
            <Kanban<Idea>
              value={columns}
              onValueChange={setColumns}
              getItemValue={(idea) => idea.id}
              onMove={handleMove}
            >
              <KanbanBoard className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5">
                {data.columns.map((column) => {
                  const ideas = columns[column.statusId] ?? [];
                  return (
                    <KanbanColumn
                      key={column.statusId}
                      value={column.statusId}
                      className="min-h-[200px] gap-2 rounded-lg border bg-muted/20 p-3"
                    >
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <IdeaStatusBadge
                          label={column.title}
                          color={column.color}
                        />
                        <span className="text-xs tabular-nums text-muted-foreground">
                          {ideas.length}
                        </span>
                      </div>
                      <KanbanColumnContent
                        value={column.statusId}
                        className="min-h-10"
                      >
                        {ideas.length === 0 ? (
                          <p className="px-1 text-xs text-muted-foreground">
                            No ideas
                          </p>
                        ) : (
                          ideas.map((idea) => (
                            <KanbanItem
                              key={idea.id}
                              value={idea.id}
                              disabled={!canManage}
                            >
                              <IdeaCard
                                idea={idea}
                                canDrag={canManage}
                                onVote={() => vote.mutate(idea.id)}
                              />
                            </KanbanItem>
                          ))
                        )}
                      </KanbanColumnContent>
                    </KanbanColumn>
                  );
                })}
              </KanbanBoard>
              <KanbanOverlay>
                {({ value }) => {
                  const idea = Object.values(columns)
                    .flat()
                    .find((i) => i.id === value);
                  return idea ? (
                    <Card className="shadow-lg">
                      <IdeaCard
                        idea={idea}
                        canDrag={false}
                        onVote={() => undefined}
                      />
                    </Card>
                  ) : null;
                }}
              </KanbanOverlay>
            </Kanban>
          )}
        </div>
      </Container>
      <IdeaCaptureModal open={modalOpen} onOpenChange={setModalOpen} />
    </>
  );
}

'use client';

import { useState, type ReactNode } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  Archive,
  ArrowRight,
  FileText,
  Image as ImageIcon,
  LoaderCircleIcon,
  Lightbulb,
  Merge,
  Mic,
  Paperclip,
  Pencil,
  Rocket,
  Split,
  Trash2,
  Undo2,
  Video,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardHeader } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import LoadErrorState from '@/components/common/LoadErrorState';
import BackToList from '@/components/common/BackToList';
import { PageHeader } from '@/components/common/PageHeader';
import DetailActions from '@/components/common/DetailActions';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import { FileDropzone } from '@/components/common/FileDropzone';
import RecordNavigation from '@/components/common/RecordNavigation';
import type { RecordAction } from '@/components/common/recordActions';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import {
  IDEAS_KEY,
  useIdeaMutations,
  useIdeaQuery,
  useIdeasQuery,
} from '@/hooks/useIdeas';
import { formatDateTime } from '@/lib/helpers';
import { fetchAttachment } from '@/services/ideasService';
import { toast } from '@/lib/toast';
import type { Idea, IdeaAttachment } from '@/types/ideas';
import { IdeaComments } from './IdeaComments';
import { IdeaMergeModal } from './IdeaMergeModal';
import { IdeaMergedList } from './IdeaMergedList';
import { IdeaStatusBadge } from './IdeaStatusBadge';
import { VoteBox } from './VoteBox';
import { useCanManageIdeas } from './ideasAccess';

type IdeaTab = 'details' | 'attachments';

const IDEA_TABS: { value: IdeaTab; label: string; icon: typeof Lightbulb }[] = [
  { value: 'details', label: 'Details', icon: Lightbulb },
  { value: 'attachments', label: 'Attachments', icon: FileText },
];

const SOURCE_LABEL: Record<string, string> = {
  whatsapp: 'WhatsApp',
  manual: 'Manual',
  email: 'Email',
  web: 'Web',
};

const ATTACHMENT_ICON: Record<IdeaAttachment['kind'], typeof FileText> = {
  image: ImageIcon,
  audio: Mic,
  video: Video,
  file: FileText,
};

function formatBytes(bytes: number | null): string {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A labelled read-only value, or its input while editing, in the same place. */
function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      {htmlFor ? (
        <Label htmlFor={htmlFor} className="text-xs text-muted-foreground">
          {label}
        </Label>
      ) : (
        <span className="text-xs text-muted-foreground">{label}</span>
      )}
      <div className="min-w-0 text-sm">{children}</div>
    </div>
  );
}

function Text({ value }: { value: string | null }) {
  return value ? (
    <p className="whitespace-pre-wrap break-words">{value}</p>
  ) : (
    <span className="text-muted-foreground">Not set</span>
  );
}

/**
 * The idea's own page. The header card holds the identity (vote box, title, status pill, meta
 * strip) and the actions; line tabs below hold Details (with the comments under the fields),
 * Attachments. Edit swaps each value for its input in place.
 *
 * Action states (mock section 2): the primary is the next status move; Edit is the outline
 * button to its left. Archived: primary Restore. Merged child: primary Unmerge, no Edit. No next
 * move: Edit is the primary.
 */
/** The page header: the idea number. The full title lives in the record card beside the vote box. */
export function IdeaDetailHeader({ id }: { id: string }) {
  const { data: idea } = useIdeaQuery(id);
  return (
    <PageHeader
      title={idea?.ideaNumber ?? 'Idea'}
      actions={<BackToList listPath="/ideas" label="Back to ideas" />}
    />
  );
}

export function IdeaDetail({ id }: { id: string }) {
  const router = useRouter();
  const canManage = useCanManageIdeas();
  const {
    data: idea,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useIdeaQuery(id);
  const { data: list } = useIdeasQuery({});
  const { vote, update, move, restore, unmerge, upload, promote } =
    useIdeaMutations();

  const [tab, setTab] = useState<IdeaTab>('details');
  const [isEditing, setIsEditing] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [problem, setProblem] = useState('');
  const [proposedSolution, setProposedSolution] = useState('');
  const [impact, setImpact] = useState('');
  const [department, setDepartment] = useState('');
  const [rawText, setRawText] = useState('');

  const subject = idea?.title ?? idea?.problem ?? '';
  const archiving = useDeferredAction({
    actionKey: 'idea.archive',
    entityType: 'idea',
    entityId: id,
    verb: 'Archiving',
    subject,
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Idea archived',
    invalidateKeys: [IDEAS_KEY],
  });
  const deletion = useDeferredAction({
    actionKey: 'idea.delete',
    entityType: 'idea',
    entityId: id,
    verb: 'Deleting',
    subject,
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Idea deleted',
    invalidateKeys: [IDEAS_KEY],
    onCommitted: () => router.push('/ideas'),
  });

  const rows = list ?? [];
  const index = rows.findIndex((row) => row.id === id);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-28 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  if (isError && (error as { status?: number } | null)?.status !== 404) {
    return (
      <Card>
        <LoadErrorState
          title="Could not load this idea"
          message={error instanceof Error ? error.message : undefined}
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      </Card>
    );
  }

  if (isError || !idea) {
    return (
      <Card className="flex flex-col items-center gap-1 p-10 text-center">
        <div className="text-sm font-semibold">Idea not found</div>
        <p className="max-w-md text-sm text-muted-foreground">
          This idea does not exist, or it was deleted after this link was made.
        </p>
      </Card>
    );
  }

  const isMergedChild = !!idea.mergedIntoId;
  const advance =
    idea.transitions.find((t) => t.id === idea.advanceTransitionId) ?? null;
  // Restore leaves an archived idea by one of its own outgoing transitions. ss also flags closed,
  // duplicate and rejected as archived, so with no way out there is nothing to restore to.
  const restoreTransition = advance ?? idea.transitions[0] ?? null;
  const busy = archiving.isPending || deletion.isPending;

  const beginEdit = (current: Idea) => {
    setProblem(current.problem);
    setProposedSolution(current.proposedSolution ?? '');
    setImpact(current.impact ?? '');
    setDepartment(current.department ?? '');
    setRawText(current.rawText);
    setTab('details');
    setIsEditing(true);
  };

  const handleSave = async () => {
    if (!problem.trim() || update.isPending) return;
    try {
      await update.mutateAsync({
        id: idea.id,
        problem: problem.trim(),
        proposedSolution: proposedSolution.trim() || null,
        impact: impact.trim() || null,
        department: department.trim() || null,
        rawText,
      });
      setIsEditing(false);
    } catch {
      // The hook toasted the reason; the edit stays open so nothing typed is lost.
    }
  };

  const gear: RecordAction[] = [];
  if (canManage) {
    if (!isMergedChild) {
      gear.push({
        key: 'idea.promote',
        label: 'Promote to BR',
        icon: Rocket,
        disabled: busy || promote.isPending,
        run: () => promote.mutate({ id: idea.id, title: subject }),
      });
    }
    if (!isMergedChild && idea.mergedCount === 0) {
      gear.push({
        key: 'idea.merge',
        label: 'Merge into another idea',
        icon: Merge,
        disabled: busy,
        run: () => setMergeOpen(true),
      });
    }
    if (!idea.statusIsArchived && !isMergedChild) {
      gear.push({
        key: 'idea.archive',
        label: 'Archive',
        icon: Archive,
        disabled: busy || archiving.isBlocked,
        run: () => archiving.start(),
      });
    }
    gear.push({
      key: 'idea.delete',
      label: 'Delete',
      icon: Trash2,
      kind: 'destructive',
      disabled: busy || deletion.isBlocked,
      run: () => deletion.start(),
    });
  }

  const editButton = (variant: 'primary' | 'outline') => (
    <Button
      variant={variant}
      size="sm"
      className="gap-1.5"
      onClick={() => beginEdit(idea)}
    >
      <Pencil className="size-4" />
      Edit
    </Button>
  );

  let primary: ReactNode = null;
  if (canManage) {
    if (isMergedChild) {
      primary = (
        <Button
          variant="primary"
          size="sm"
          className="gap-1.5"
          disabled={unmerge.isPending}
          onClick={() => unmerge.mutate(idea.id)}
        >
          {unmerge.isPending ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <Split className="size-4" />
          )}
          Unmerge
        </Button>
      );
    } else if (idea.statusIsArchived && restoreTransition) {
      primary = (
        <>
          {editButton('outline')}
          <Button
            variant="primary"
            size="sm"
            className="gap-1.5"
            disabled={restore.isPending}
            onClick={() =>
              restore.mutate({
                id: idea.id,
                toStatusId: restoreTransition.toStatusId,
              })
            }
          >
            {restore.isPending ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : (
              <Undo2 className="size-4" />
            )}
            Restore
          </Button>
        </>
      );
    } else if (advance) {
      primary = (
        <>
          {editButton('outline')}
          <Button
            variant="primary"
            size="sm"
            className="gap-1.5"
            disabled={move.isPending}
            onClick={() =>
              move.mutate({ id: idea.id, toStatusId: advance.toStatusId })
            }
          >
            {move.isPending ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : (
              <ArrowRight className="size-4" />
            )}
            Move to {advance.toStatusLabel}
          </Button>
        </>
      );
    } else {
      primary = editButton('primary');
    }
  } else if (idea.isMine === true && !isMergedChild) {
    // The submitter edits their own idea with view access alone; every other action stays manage-only.
    primary = editButton('primary');
  }

  const openAttachment = async (attachment: IdeaAttachment) => {
    if (!attachment.hasContent) {
      // A link ss holds: only a web address is ever opened (never `javascript:` or `data:`).
      if (/^https?:\/\//i.test(attachment.url))
        window.open(attachment.url, '_blank', 'noopener,noreferrer');
      return;
    }
    try {
      const blob = await fetchAttachment(idea.id, attachment.id);
      // Saved as a file, never opened inline: an uploaded file is not ours to render in this origin.
      const href = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = attachment.name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(href), 60_000);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : 'Failed to download the file',
      );
    }
  };

  const onPickFiles = async (files: File[]) => {
    for (const file of files) {
      await upload.mutateAsync({ id: idea.id, file }).catch(() => undefined);
    }
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="block py-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex min-w-0 items-start gap-4">
              <VoteBox
                size="md"
                count={idea.upvotes}
                voted={idea.myVote === 'up'}
                disabled={isMergedChild}
                onVote={() => vote.mutate(idea.id)}
              />
              <div className="flex min-w-0 flex-col gap-1.5">
                <h2
                  className="break-words text-lg font-semibold"
                  title={subject}
                >
                  {subject}
                </h2>
                <div className="flex flex-wrap items-center gap-2">
                  <IdeaStatusBadge
                    label={idea.statusLabel}
                    color={idea.statusColor}
                  />
                  {isMergedChild ? (
                    <Badge variant="outline" size="sm">
                      Merged
                    </Badge>
                  ) : null}
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span>Submitted by {idea.submitterName}</span>
                  <span>{SOURCE_LABEL[idea.source] ?? idea.source}</span>
                  <span>{idea.productName}</span>
                  <span>Captured {formatDateTime(idea.createdAt)}</span>
                  {idea.mergedInto ? (
                    <span className="min-w-0 truncate">
                      Merged into{' '}
                      <Link
                        href={`/ideas/${idea.mergedInto.id}`}
                        className="text-primary hover:underline"
                        title={idea.mergedInto.title ?? undefined}
                      >
                        {idea.mergedInto.ideaNumber ?? idea.mergedInto.title}
                      </Link>
                    </span>
                  ) : null}
                </div>
              </div>
            </div>
            {isEditing ? (
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setIsEditing(false)}
                  disabled={update.isPending}
                >
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleSave}
                  disabled={!problem.trim() || update.isPending}
                >
                  {update.isPending ? (
                    <LoaderCircleIcon className="size-4 animate-spin" />
                  ) : null}
                  Save
                </Button>
              </div>
            ) : (
              <DetailActions
                pagerNode={
                  <RecordNavigation
                    index={index >= 0 ? index + 1 : null}
                    total={rows.length}
                    hasPrevious={index > 0}
                    hasNext={index >= 0 && index < rows.length - 1}
                    onPrevious={() =>
                      router.push(`/ideas/${rows[index - 1].id}`)
                    }
                    onNext={() => router.push(`/ideas/${rows[index + 1].id}`)}
                    ariaLabel="idea"
                  />
                }
                gear={
                  gear.length > 0 ? (
                    <DetailActionsMenu
                      actions={gear}
                      trigger="ellipsis"
                      ariaLabel="Idea options"
                    />
                  ) : undefined
                }
                pendingAction={archiving.countdown ?? deletion.countdown}
                primary={primary}
              />
            )}
          </div>
        </CardHeader>
      </Card>

      <Tabs value={tab} onValueChange={(v) => setTab(v as IdeaTab)}>
        <TabsList variant="line" className="mb-5">
          {IDEA_TABS.map((t) => (
            <TabsTrigger
              key={t.value}
              value={t.value}
              onClick={() => setTab(t.value)}
            >
              <t.icon className="size-4" />
              <span>{t.label}</span>
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="details">
          <Card>
            <div className="flex flex-col gap-5 p-5">
              <section aria-label="Details" className="flex flex-col gap-4">
                <Field
                  label="Problem statement"
                  htmlFor={isEditing ? 'idea-edit-problem' : undefined}
                >
                  {isEditing ? (
                    <Textarea
                      id="idea-edit-problem"
                      value={problem}
                      onChange={(e) => setProblem(e.target.value)}
                      rows={3}
                    />
                  ) : (
                    <Text value={idea.problem} />
                  )}
                </Field>
                <Field
                  label="Proposed solution"
                  htmlFor={isEditing ? 'idea-edit-solution' : undefined}
                >
                  {isEditing ? (
                    <Textarea
                      id="idea-edit-solution"
                      value={proposedSolution}
                      onChange={(e) => setProposedSolution(e.target.value)}
                      rows={2}
                    />
                  ) : (
                    <Text value={idea.proposedSolution} />
                  )}
                </Field>
                <Field
                  label="Impact"
                  htmlFor={isEditing ? 'idea-edit-impact' : undefined}
                >
                  {isEditing ? (
                    <Textarea
                      id="idea-edit-impact"
                      value={impact}
                      onChange={(e) => setImpact(e.target.value)}
                      rows={2}
                    />
                  ) : (
                    <Text value={idea.impact} />
                  )}
                </Field>
                <Field
                  label="Department"
                  htmlFor={isEditing ? 'idea-edit-department' : undefined}
                >
                  {isEditing ? (
                    <Input
                      id="idea-edit-department"
                      value={department}
                      maxLength={120}
                      onChange={(e) => setDepartment(e.target.value)}
                      className="h-8"
                    />
                  ) : (
                    <Text value={idea.department} />
                  )}
                </Field>
                <Field
                  label="Original message"
                  htmlFor={isEditing ? 'idea-edit-raw' : undefined}
                >
                  {isEditing ? (
                    <Textarea
                      id="idea-edit-raw"
                      value={rawText}
                      onChange={(e) => setRawText(e.target.value)}
                      rows={3}
                    />
                  ) : (
                    <Text value={idea.rawText} />
                  )}
                </Field>
              </section>
              <IdeaMergedList ideaId={idea.id} count={idea.mergedCount} />
              <IdeaComments ideaId={idea.id} frozen={isMergedChild} />
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="attachments">
          <Card>
            <section
              aria-label="Attachments"
              className="flex flex-col gap-3 p-5"
            >
              {canManage || (idea.isMine === true && !isMergedChild) ? (
                <FileDropzone
                  multiple
                  disabled={upload.isPending}
                  files={[]}
                  onFilesChange={(files) => void onPickFiles(files)}
                  aria-label="Upload attachments"
                  title={
                    upload.isPending
                      ? 'Uploading...'
                      : 'Drop files here or click to upload'
                  }
                  hint=""
                />
              ) : null}
              {idea.attachments.length === 0 ? (
                <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed py-8 text-center">
                  <span className="text-sm font-medium">No attachments</span>
                  <span className="text-sm text-muted-foreground">
                    Files sent with this idea appear here.
                  </span>
                </div>
              ) : (
                <ul className="flex flex-col divide-y rounded-lg border">
                  {idea.attachments.map((attachment) => {
                    const Icon = ATTACHMENT_ICON[attachment.kind] ?? Paperclip;
                    const detail = [
                      formatBytes(attachment.sizeBytes),
                      attachment.durationSec != null
                        ? `${attachment.durationSec}s`
                        : '',
                    ]
                      .filter(Boolean)
                      .join(', ');
                    return (
                      <li
                        key={attachment.id}
                        className="flex min-w-0 items-center gap-3 px-3 py-2"
                      >
                        <Icon className="size-4 shrink-0 text-muted-foreground" />
                        {attachment.hasContent ||
                        /^https?:\/\//i.test(attachment.url) ? (
                          <button
                            type="button"
                            className="min-w-0 flex-1 truncate text-start text-sm text-primary hover:underline"
                            title={attachment.name}
                            onClick={() => void openAttachment(attachment)}
                          >
                            {attachment.name}
                          </button>
                        ) : (
                          <span
                            className="min-w-0 flex-1 truncate text-sm"
                            title={attachment.name}
                          >
                            {attachment.name}
                          </span>
                        )}
                        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                          {detail}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </Card>
        </TabsContent>
      </Tabs>
      <IdeaMergeModal
        idea={idea}
        open={mergeOpen}
        onOpenChange={setMergeOpen}
      />
    </div>
  );
}

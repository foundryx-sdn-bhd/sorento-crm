'use client';

import { useState, type ReactNode } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { FileDropzone } from '@/components/common/FileDropzone';
import { cn } from '@/lib/utils';

/**
 * The comment-thread primitives the Ideation thread introduced, shared so every thread in the
 * product (ideas, form submissions) looks and behaves the same.
 */

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

export function CommentAvatar({
  name,
  small,
}: {
  name: string;
  small?: boolean;
}) {
  return (
    <Avatar className={small ? 'size-6' : 'size-8'}>
      <AvatarFallback className="text-[0.6875rem] font-medium">
        {initials(name)}
      </AvatarFallback>
    </Avatar>
  );
}

export interface CommentComposerProps {
  authorName: string;
  placeholder: string;
  submitLabel: string;
  initial?: string;
  pending?: boolean;
  small?: boolean;
  autoFocus?: boolean;
  /** Set to let the comment carry files (up to this many). Omit for text only. */
  maxFiles?: number;
  /** `files` is always [] when `maxFiles` is not set. */
  onSubmit: (body: string, files: File[]) => Promise<void>;
  onCancel?: () => void;
}

export function CommentComposer({
  authorName,
  placeholder,
  submitLabel,
  initial = '',
  pending,
  small,
  autoFocus,
  maxFiles,
  onSubmit,
  onCancel,
}: CommentComposerProps) {
  const [body, setBody] = useState(initial);
  const [files, setFiles] = useState<File[]>([]);
  const canSubmit = (body.trim().length > 0 || files.length > 0) && !pending;

  const submit = async () => {
    if (!canSubmit) return;
    try {
      await onSubmit(body, files);
      setBody('');
      setFiles([]);
    } catch {
      // The hook toasted the reason; what was typed stays.
    }
  };

  return (
    <div className="flex items-start gap-3">
      <CommentAvatar name={authorName} small={small} />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <Textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder={placeholder}
          aria-label={placeholder}
          rows={small ? 2 : 3}
          autoFocus={autoFocus}
        />
        {maxFiles ? (
          <FileDropzone
            multiple
            files={files}
            onFilesChange={(next) => setFiles(next.slice(0, maxFiles))}
            disabled={pending}
            title="Attach files"
            hint={`Up to ${maxFiles} files`}
            aria-label="Attach files to the comment"
          />
        ) : null}
        <div className="flex justify-end gap-2">
          {onCancel ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onCancel}
            >
              Cancel
            </Button>
          ) : null}
          <Button
            type="button"
            variant="primary"
            size="sm"
            disabled={!canSubmit}
            onClick={submit}
          >
            {pending ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : null}
            {submitLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** "Comments <count>" heading, the composer slot, then loading / empty / the list. */
export function CommentsSection({
  count,
  composer,
  isLoading,
  isEmpty,
  emptyHint,
  className,
  footer,
  children,
}: {
  count: number;
  composer: ReactNode;
  /** Rendered after the list (or the empty state): a composer at the bottom of the story. */
  footer?: ReactNode;
  isLoading: boolean;
  isEmpty: boolean;
  emptyHint: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-label="Comments"
      className={cn('flex flex-col gap-3 border-t pt-5', className)}
    >
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        Comments
        <Badge variant="secondary" size="sm" shape="circle">
          {count}
        </Badge>
      </h3>

      {composer}

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : isEmpty ? (
        <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed py-6 text-center">
          <span className="text-sm font-medium">No comments yet</span>
          <span className="text-sm text-muted-foreground">{emptyHint}</span>
        </div>
      ) : (
        children
      )}

      {footer}
    </section>
  );
}

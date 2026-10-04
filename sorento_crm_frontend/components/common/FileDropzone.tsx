'use client';

import * as React from 'react';
import { Upload, X, File as FileIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export interface FileDropzoneProps {
  /** Comma-separated extension list, e.g. `.pdf,.jpg`. Omit to accept anything. */
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  /** Reject anything larger, in MB. Omit for no ceiling. */
  maxSizeMb?: number;
  /** Currently held files. Controlled - the parent owns the state. */
  files: File[];
  onFilesChange: (files: File[]) => void;
  /**
   * Raised once per rejected file so the caller can toast in its own voice.
   * `extra` means the drop carried more files than a single-file zone can hold, which
   * used to be silent: a user who dragged three workbooks got one imported and no word
   * about the other two.
   */
  onReject?: (file: File, reason: 'type' | 'size' | 'extra') => void;
  /** Put on the hidden input, so a `<Label htmlFor>` can bind to it. */
  id?: string;
  /** Put on the hidden input, for e2e selectors that need to target it directly. */
  inputTestId?: string;
  /** Headline inside the empty zone. */
  title?: string;
  /** Small print under the headline. Defaults to the accepted extensions. */
  hint?: string;
  className?: string;
  /** Labels the hidden input for screen readers. */
  'aria-label'?: string;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot === -1 ? '' : name.slice(dot).toLowerCase();
}

/**
 * The one file-drop surface in the system.
 *
 * Every upload screen used to hand-roll its own dashed box, so the drag highlight, the
 * remove affordance, the size ceiling and the keyboard path all drifted apart. Anything
 * that takes a file from the user renders this instead.
 *
 * The whole zone is the drop target AND the click target, so a user who never thinks to
 * drag still lands on the file picker by clicking where the instructions are.
 */
export function FileDropzone({
  accept,
  multiple = false,
  disabled = false,
  maxSizeMb,
  files,
  onFilesChange,
  onReject,
  title,
  hint,
  className,
  id,
  inputTestId,
  'aria-label': ariaLabel,
}: FileDropzoneProps) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = React.useState(false);

  const allowed = React.useMemo(
    () =>
      (accept ?? '')
        .split(',')
        .map((entry) => entry.trim().toLowerCase())
        .filter(Boolean),
    [accept],
  );

  const admit = React.useCallback(
    (incoming: FileList | File[] | null | undefined) => {
      if (!incoming || disabled) return;
      const candidates = Array.from(incoming)
        // macOS writes a sibling `._name` resource fork into every drag off a DMG or zip.
        .filter((file) => !file.name.trim().startsWith('._'));

      const kept: File[] = [];
      for (const file of candidates) {
        if (allowed.length && !allowed.includes(extensionOf(file.name))) {
          onReject?.(file, 'type');
          continue;
        }
        if (maxSizeMb != null && file.size > maxSizeMb * 1024 * 1024) {
          onReject?.(file, 'size');
          continue;
        }
        kept.push(file);
      }
      if (!kept.length) return;
      if (!multiple && kept.length > 1) {
        // Say so rather than quietly keeping the first: the user watched three files
        // land on the zone and needs to know only one of them counted.
        kept.slice(1).forEach((file) => onReject?.(file, 'extra'));
      }
      onFilesChange(multiple ? [...files, ...kept] : [kept[0]]);
    },
    [allowed, disabled, files, maxSizeMb, multiple, onFilesChange, onReject],
  );

  const openPicker = () => {
    if (!disabled) inputRef.current?.click();
  };

  const remove = (index: number) => {
    onFilesChange(files.filter((_, i) => i !== index));
  };

  return (
    <div className={cn('w-full min-w-0 space-y-2', className)}>
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled || undefined}
        onClick={openPicker}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openPicker();
          }
        }}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={(event) => {
          event.preventDefault();
          setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          admit(event.dataTransfer?.files);
        }}
        className={cn(
          'rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          disabled
            ? 'cursor-not-allowed opacity-60'
            : 'cursor-pointer hover:border-primary/60',
          dragging ? 'border-primary bg-primary/5' : 'border-muted-foreground/25',
        )}
      >
        <Upload className="mx-auto size-5 text-muted-foreground" aria-hidden />
        <p
          className="mt-2 break-words text-sm font-medium"
          title={dragging ? undefined : (title ?? 'Drop a file here, or click to browse')}
        >
          {dragging ? 'Drop it here' : (title ?? 'Drop a file here, or click to browse')}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {hint ?? (allowed.length ? allowed.join(', ') : 'Any file type')}
          {maxSizeMb != null ? ` - up to ${maxSizeMb} MB` : ''}
        </p>
        <input
          ref={inputRef}
          id={id}
          data-testid={inputTestId}
          type="file"
          accept={accept}
          multiple={multiple}
          disabled={disabled}
          className="hidden"
          aria-label={ariaLabel ?? title ?? 'Choose a file'}
          onChange={(event) => {
            admit(event.target.files);
            // Clearing lets the same filename be picked again after a remove.
            event.target.value = '';
          }}
        />
      </div>

      {files.length > 0 && (
        <ul className="space-y-1.5">
          {files.map((file, index) => (
            <li
              key={`${file.name}-${index}`}
              className="flex items-center gap-2 rounded-md border border-border px-3 py-2"
            >
              <FileIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              <div className="min-w-0 flex-1 text-left">
                <p className="truncate text-sm font-medium" title={file.name}>
                  {file.name}
                </p>
                <p className="text-xs text-muted-foreground">{formatSize(file.size)}</p>
              </div>
              <Button
                type="button"
                mode="icon"
                variant="ghost"
                size="sm"
                disabled={disabled}
                aria-label={`Remove ${file.name}`}
                onClick={() => remove(index)}
              >
                <X className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default FileDropzone;

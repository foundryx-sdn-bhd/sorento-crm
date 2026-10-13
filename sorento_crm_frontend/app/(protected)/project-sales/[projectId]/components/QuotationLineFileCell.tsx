'use client';

import * as React from 'react';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import type {
  QuotationLine,
  QuotationLineFileCandidate,
} from '../../_shared/types/project.types';
import { QuotationLinePhoto } from './QuotationLinePhoto';

export type LineFileKind = 'photo' | 'drawing';

/** The picture a cell shows, whichever way it was decided. */
export interface LineFileShown {
  attachmentId: string;
  url: string;
  previewUrl: string;
  filename: string | null;
}

function candidatesOf(
  kind: LineFileKind,
  line: QuotationLine,
): QuotationLineFileCandidate[] {
  return (
    (kind === 'photo'
      ? line.product_image?.candidates
      : line.technical_drawing?.candidates) ?? []
  );
}

/**
 * The photo or drawing a line shows: the one picked in this edit session when there is one,
 * else what the server answered (a stored pick, the issued stamp, the product's own photo or its
 * first drawing). Shared by the cell and the viewer's gallery, so the two cannot disagree.
 */
export function resolveLineFile(
  kind: LineFileKind,
  line: QuotationLine | null,
  pickedId: string,
): LineFileShown | null {
  if (!line) return null;
  const cell = kind === 'photo' ? line.product_image : line.technical_drawing;
  if (pickedId) {
    const candidate = candidatesOf(kind, line).find(
      (row) => row.attachment_id === pickedId,
    );
    if (candidate?.url) {
      return {
        attachmentId: candidate.attachment_id,
        url: candidate.url,
        previewUrl: candidate.preview_url || candidate.url,
        filename: candidate.filename ?? null,
      };
    }
  }
  if (!cell?.url || !cell.attachment_id) return null;
  if (pickedId && pickedId !== cell.attachment_id) return null;
  const shownState =
    kind === 'photo' ? cell.state === 'chosen' : cell.state !== 'off_catalog';
  if (!shownState) return null;
  return {
    attachmentId: cell.attachment_id,
    url: cell.url,
    previewUrl: cell.preview_url || cell.url,
    filename: cell.filename ?? null,
  };
}

const NOUN: Record<LineFileKind, string> = {
  photo: 'photo',
  drawing: 'drawing',
};
const ALT: Record<LineFileKind, string> = {
  photo: 'product photo',
  drawing: 'technical drawing',
};

/**
 * The Photo or Drawing cell (AC-QLG05, AC-QLG07, AC-QLG08): a 40px thumbnail that opens the
 * viewer, and, while editing a line whose product has several, a picker that writes the line's
 * `image_attachment_id` / `drawing_attachment_id`. The product's own files are never changed.
 *
 * `line` is null for a row added in this session or whose product was changed: nothing has been
 * resolved for it yet, so the cell claims nothing until Save.
 */
export function QuotationLineFileCell({
  kind,
  line,
  label,
  pickedId,
  editable,
  onPick,
  onPreview,
}: {
  kind: LineFileKind;
  line: QuotationLine | null;
  label: string;
  pickedId: string;
  editable: boolean;
  onPick: (attachmentId: string) => void;
  onPreview: (attachmentId: string) => void;
}) {
  const [open, setOpen] = React.useState(false);
  if (!line) return null;

  const candidates = candidatesOf(kind, line);
  const shown = resolveLineFile(kind, line, pickedId);
  const canPick = editable && candidates.length > 1;
  const code = line.product_code ?? null;

  const thumbnail = shown ? (
    <button
      type="button"
      onClick={() => onPreview(shown.attachmentId)}
      className="block rounded focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      title={shown.filename ?? undefined}
    >
      <img
        src={shown.url}
        alt={code ? `${code} ${ALT[kind]}` : ALT[kind]}
        className="size-10 rounded border border-border bg-white object-contain"
        loading="lazy"
      />
    </button>
  ) : null;

  if (!canPick) {
    if (kind === 'photo' && !thumbnail) {
      // Today's empty states: no product, nobody chose among several, nothing on file.
      return <QuotationLinePhoto line={line} />;
    }
    if (thumbnail) {
      return (
        <span className="relative inline-block">
          {thumbnail}
          {candidates.length > 1 && <CountBadge count={candidates.length} />}
        </span>
      );
    }
    const state = line.technical_drawing?.state;
    return (
      <span className="block text-2xs leading-tight text-muted-foreground">
        {state === 'no_drawings' ? 'no drawing' : '-'}
      </span>
    );
  }

  const action = shown ? 'Change' : 'Choose';
  return (
    <Popover open={open} onOpenChange={setOpen}>
      {shown ? (
        <span className="relative inline-block">
          {thumbnail}
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-label={`${action} ${NOUN[kind]} on ${label}`}
              className="absolute -end-1.5 -bottom-1.5 min-w-4 rounded-full bg-foreground px-1 text-2xs leading-4 font-medium text-background tabular-nums"
            >
              {candidates.length}
            </button>
          </PopoverTrigger>
        </span>
      ) : (
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={`${action} ${NOUN[kind]} on ${label}`}
            className="flex size-10 flex-col items-center justify-center rounded border border-dashed border-primary bg-primary/5 text-center text-2xs leading-tight text-primary"
          >
            <span>Choose</span>
            <span className="tabular-nums">{candidates.length}</span>
          </button>
        </PopoverTrigger>
      )}
      <PopoverContent
        align="start"
        className="w-[min(21rem,calc(100vw-2rem))] p-2.5"
      >
        <div className="mb-2 flex items-center justify-between gap-2 text-xs">
          <span className="truncate font-medium">{`${kind === 'photo' ? 'Photo' : 'Drawing'} for ${label}`}</span>
          <span className="shrink-0 text-muted-foreground">{`${candidates.length} on file`}</span>
        </div>
        <div className="grid grid-cols-4 gap-2">
          {candidates.map((candidate) => {
            const selected = shown?.attachmentId === candidate.attachment_id;
            const name = candidate.filename ?? NOUN[kind];
            return (
              <button
                key={candidate.attachment_id}
                type="button"
                aria-label={`Use ${name}`}
                aria-pressed={selected}
                title={name}
                onClick={() => {
                  onPick(candidate.attachment_id);
                  setOpen(false);
                }}
                className={cn(
                  'rounded-md border border-border p-0.5 text-center hover:border-primary',
                  selected && 'border-2 border-primary p-0',
                )}
              >
                {candidate.url ? (
                  <img
                    src={candidate.url}
                    alt=""
                    className="mx-auto size-16 object-contain"
                    loading="lazy"
                  />
                ) : (
                  <span className="mx-auto block size-16" />
                )}
                <span className="mt-0.5 block truncate text-2xs text-muted-foreground">
                  {name}
                </span>
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function CountBadge({ count }: { count: number }) {
  return (
    <span className="absolute -end-1.5 -bottom-1.5 min-w-4 rounded-full bg-foreground px-1 text-center text-2xs leading-4 font-medium text-background tabular-nums">
      {count}
    </span>
  );
}

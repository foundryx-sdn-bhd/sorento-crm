'use client';

import { useMemo, useState } from 'react';
import { ColumnDef, getCoreRowModel, getSortedRowModel, useReactTable } from '@tanstack/react-table';
import { MoreHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { SPEC_REGISTRY_QUERY_KEY } from '../hooks/useSpecRegistryQuery';

export interface WordsDataGridProps {
  /** e.g. "Other names for this specification" - the field label sits ABOVE this. */
  words: string[];
  mode: 'view' | 'edit';
  /** The registry key this word list belongs to - `spec_word.remove`'s record (D7). */
  specKey: string;
  /** The value bucket these words describe; `_self` for the specification's own names. */
  value: string;
  onAdd: (word: string) => void;
  onRename: (oldWord: string, newWord: string) => void;
  /** The server committed a removal - strip it from whatever draft is open too.
   *  Also called straight away for a word that exists only in the draft. */
  onRemoved?: (word: string) => void;
  /**
   * The words the server holds for this value. Only one of these has anything for
   * `spec_word.remove` to remove; a word added in this sitting is dropped locally,
   * with no countdown. Absent means every word listed is a saved one.
   */
  savedWords?: readonly string[];
  emptyMessage?: string;
  addPlaceholder?: string;
  /** word -> the other specifications that also own it; shown under the word. */
  alsoOn?: Record<string, string[]>;
  /** A refused word: its sentence shows under the row holding that word (edit mode). */
  error?: { word: string; message: string } | null;
}

interface WordRow {
  word: string;
}

/**
 * Every list of words is a data grid (D13, owner ruling 27 Sep 2026: "this should
 * be tabulated with data grid"). One row per word, sortable, edited in place,
 * `+ Add a word` at the foot - the shared `DataGrid` (fixed, resizable columns),
 * not a hand-rolled table.
 *
 * Remove is `spec_word.remove` (D7, D8, fix round 1): a server-deferred action,
 * not a local timer - the record is the SPEC KEY (one pending removal per key
 * across every word, same as every other record action), so a second Remove
 * while one is already counting down waits its turn rather than racing it.
 */
export function WordsDataGrid({
  words,
  mode,
  specKey,
  value,
  onAdd,
  onRename,
  onRemoved,
  savedWords,
  emptyMessage = 'No words yet.',
  addPlaceholder = 'a word',
  alsoOn,
  error,
}: WordsDataGridProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newWord, setNewWord] = useState('');
  const [removingWord, setRemovingWord] = useState<string | null>(null);

  const removal = useDeferredAction({
    actionKey: 'spec_word.remove',
    entityType: 'spec_word',
    entityId: specKey || null,
    verb: 'Removing',
    subject: removingWord ?? '',
    surface: 'inline',
    watchFromMount: mode === 'edit',
    successMessage: 'Word removed',
    invalidateKeys: [SPEC_REGISTRY_QUERY_KEY],
    onCommitted: () => {
      if (removingWord) onRemoved?.(removingWord);
      setRemovingWord(null);
    },
  });

  // B-5: one removal at a time - see `SpecRulesGrid`'s own note.
  const removalBusy = removal.isPending || removal.isBlocked;
  const fold = (word: string) => word.trim().toLowerCase().replace(/\s+/g, ' ');
  const isSavedWord = (word: string) =>
    savedWords === undefined || savedWords.some((saved) => fold(saved) === fold(word));

  const startRemoval = (word: string) => {
    if (!isSavedWord(word)) {
      onRemoved?.(word);
      return;
    }
    if (removalBusy) return;
    setRemovingWord(word);
    removal.start({ value, word });
  };

  const rows = useMemo<WordRow[]>(() => words.map((word) => ({ word })), [words]);

  const columns = useMemo<ColumnDef<WordRow>[]>(() => {
    const wordColumn: ColumnDef<WordRow> = {
      id: 'word',
      accessorFn: (row) => row.word,
      header: ({ column }) => <DataGridColumnHeader title="Word" column={column} />,
      cell: ({ row }) => {
        const word = row.original.word;
        if (mode === 'edit' && editing === word) {
          return (
            <Input
              autoFocus
              defaultValue={word}
              className="h-8"
              aria-label={`Edit ${word}`}
              onBlur={(e) => {
                const next = e.target.value.trim();
                if (next && next !== word) onRename(word, next);
                setEditing(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur();
                if (e.key === 'Escape') setEditing(null);
              }}
            />
          );
        }
        const others = alsoOn?.[word] ?? [];
        return (
          <div className="min-w-0">
            <button
              type="button"
              disabled={mode !== 'edit'}
              onClick={() => mode === 'edit' && setEditing(word)}
              className={`block w-full truncate text-left ${mode === 'edit' ? 'hover:underline' : ''}`}
              title={word}
            >
              {word}
            </button>
            {others.length > 0 && (
              <span className="block truncate text-xs text-muted-foreground">
                Also on: {others.join(', ')}
              </span>
            )}
            {mode === 'edit' && error && error.word === word && (
              <span role="alert" className="block text-xs text-destructive">
                {error.message}
              </span>
            )}
          </div>
        );
      },
      size: 280,
      minSize: 140,
    };

    if (mode !== 'edit') return [wordColumn];

    const actionsColumn: ColumnDef<WordRow> = {
      id: 'actions',
      header: () => <span className="sr-only">Actions</span>,
      enableSorting: false,
      enableResizing: false,
      cell: ({ row }) => {
        const word = row.original.word;
        if (removingWord === word && removal.pending) return removal.countdown;
        const saved = isSavedWord(word);
        return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label={`${word} actions`}
                className="size-7 text-muted-foreground"
                disabled={removal.isBlocked}
              >
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => setEditing(word)}>Edit</DropdownMenuItem>
              <DropdownMenuItem
                variant="destructive"
                disabled={saved && removalBusy}
                onClick={() => startRemoval(word)}
              >
                Remove
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        );
      },
      size: 60,
      minSize: 60,
    };

    return [wordColumn, actionsColumn];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    mode,
    editing,
    removingWord,
    removal.countdown,
    removal.pending,
    removal.isBlocked,
    removalBusy,
    savedWords,
    alsoOn,
    error,
    onRename,
  ]);

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.word,
    // Alphabetical by default, the same as the hand-rolled table this replaces;
    // the header's own sort toggle can still flip it.
    initialState: { sorting: [{ id: 'word', desc: false }] },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    columnResizeMode: 'onChange',
  });

  const commitAdd = () => {
    const trimmed = newWord.trim();
    if (trimmed) onAdd(trimmed);
    setNewWord('');
    setAdding(false);
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-hidden rounded-md border">
        <DataGrid
          table={table}
          recordCount={rows.length}
          isLoading={false}
          listingKey={null}
          tableLayout={{ width: 'fixed', columnsResizable: true }}
          emptyMessage={emptyMessage}
        >
          <DataGridTable />
        </DataGrid>
      </div>

      {mode === 'edit' &&
        (adding ? (
          <Input
            autoFocus
            value={newWord}
            placeholder={addPlaceholder}
            className="h-8"
            onChange={(e) => setNewWord(e.target.value)}
            onBlur={commitAdd}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitAdd();
              if (e.key === 'Escape') {
                setNewWord('');
                setAdding(false);
              }
            }}
          />
        ) : (
          <button
            type="button"
            className="self-start text-sm text-primary hover:underline"
            onClick={() => setAdding(true)}
          >
            + Add a word
          </button>
        ))}
    </div>
  );
}

export default WordsDataGrid;

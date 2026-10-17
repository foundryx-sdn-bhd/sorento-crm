'use client';

import { useMemo, useState } from 'react';
import { ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { tryInChatbot, type ParserPick } from '../services/productSpecService';

const MAX_MESSAGE = 500;

/**
 * Type a customer message and see what the chatbot's parser picked from it. Kept in
 * component state like `TryPhraseDialog`: closing does not clear the last run.
 */
export function TryInChatbotDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [text, setText] = useState('');
  const [picks, setPicks] = useState<ParserPick[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = text.trim();

  const run = async () => {
    if (!trimmed || loading) return;
    setLoading(true);
    setError(null);
    try {
      const result = await tryInChatbot(trimmed);
      setPicks(result.picks);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'The chatbot reader is not available');
      setPicks(null);
    } finally {
      setLoading(false);
    }
  };

  const columns = useMemo<ColumnDef<ParserPick>[]>(
    () => [
      {
        id: 'raw',
        header: 'Words',
        enableSorting: false,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.raw}>
            {row.original.raw}
          </span>
        ),
        size: 100,
        minSize: 70,
      },
      {
        id: 'read_as',
        header: 'Read as',
        enableSorting: false,
        cell: ({ row }) => {
          const label = row.original.field_label || row.original.kind;
          return (
            <span className="block truncate" title={label}>
              {label}
            </span>
          );
        },
        size: 120,
        minSize: 80,
      },
      {
        id: 'value',
        header: 'Value',
        enableSorting: false,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.value}>
            {row.original.value}
          </span>
        ),
        size: 110,
        minSize: 70,
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: picks ?? [],
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Try in chatbot</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">
          Dry run. Nothing is sent and nothing is saved.
        </p>

        <div className="flex flex-col gap-5">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="min-w-0 flex-1">
              <Input
                aria-label="Customer message"
                value={text}
                maxLength={MAX_MESSAGE}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void run();
                }}
              />
            </div>
            <Button onClick={() => void run()} disabled={!trimmed || loading}>
              Run
            </Button>
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertIcon />
              <AlertTitle>{error}</AlertTitle>
            </Alert>
          )}

          {picks && picks.length === 0 && (
            <div className="rounded-md border border-dashed p-6 text-center text-sm font-medium text-muted-foreground">
              Nothing picked
            </div>
          )}

          {picks && picks.length > 0 && (
            <div className="overflow-hidden rounded-md border">
              <DataGrid
                table={table}
                recordCount={picks.length}
                isLoading={false}
                listingKey={null}
                tableLayout={{ width: 'fixed', columnsResizable: true }}
              >
                <DataGridTable />
              </DataGrid>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default TryInChatbotDialog;

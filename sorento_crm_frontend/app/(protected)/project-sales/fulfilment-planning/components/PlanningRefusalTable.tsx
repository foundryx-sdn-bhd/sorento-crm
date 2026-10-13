'use client';

import * as React from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { SupplyFailingLine } from '../../_shared/types/fulfilmentPlanning.types';

/** Lines whose refusal carries a reason code: the rows an admin reads (PLANNING-WHY AC-W13). */
export function hasReasonCode(line: SupplyFailingLine): boolean {
  return Boolean(line.reason_code);
}

/**
 * Why a Confirm stopped, one row per line: Line, Product, Change, a reason badge and the one
 * action that fixes it. A line that was not decided or was cancelled in the book is fixed on
 * the board; a line that changed again or was confirmed since needs a fresh read.
 */
export function PlanningRefusalTable({
  lines,
  onGoToLine,
  onReload,
}: {
  lines: SupplyFailingLine[];
  onGoToLine: (line: SupplyFailingLine) => void;
  onReload: () => void;
}) {
  return (
    <Table className="text-sm">
      <TableHeader>
        <TableRow>
          <TableHead className="h-8 px-2">Line</TableHead>
          <TableHead className="h-8 px-2">Product</TableHead>
          <TableHead className="h-8 px-2">Change</TableHead>
          <TableHead className="h-8 px-2">Reason</TableHead>
          <TableHead className="h-8 px-2">
            <span className="sr-only">Action</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lines.map((line, index) => {
          const toLine = line.reason_code === 'not_decided' || line.reason_code === 'line_cancelled';
          return (
            <TableRow key={`${line.so_line_no ?? line.line_no ?? 'order'}-${line.item_code ?? ''}-${index}`}>
              <TableCell className="px-2 py-1.5 tabular-nums">{line.so_line_no ?? line.line_no}</TableCell>
              <TableCell className="px-2 py-1.5 break-words">{line.item_code}</TableCell>
              <TableCell className="px-2 py-1.5 break-words">{line.change ?? ''}</TableCell>
              <TableCell className="px-2 py-1.5">
                <Badge
                  variant={line.reason_code === 'not_decided' ? 'warning' : 'secondary'}
                  appearance="light"
                  size="sm"
                >
                  {line.reason}
                </Badge>
              </TableCell>
              <TableCell className="px-2 py-1.5">
                {toLine ? (
                  <Button type="button" variant="link" size="sm" onClick={() => onGoToLine(line)}>
                    Go to line
                  </Button>
                ) : (
                  <Button type="button" variant="link" size="sm" onClick={onReload}>
                    Reload
                  </Button>
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

'use client';

import { Check, ChevronDown, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

/**
 * OI-BULK-RESERVE (AC-BR-01/02): the selection strip's "Decide (N)", Fulfilment Planning's
 * Decide pattern (`BoardDecideControl.tsx`) cut down to the two answers a reserve request
 * has: the line's tick for every counted line, or its Reserve 0 with one reason. N is
 * counted by the caller (ticked lines with an open request); greyed at 0. The caller
 * renders it only while lines are ticked (the selection strip's own button, mock v3).
 */
export function ReserveDecideControl({
  count,
  onReserveAsRequested,
  onReject,
}: {
  count: number;
  onReserveAsRequested: () => void;
  onReject: () => void;
}) {
  const enabled = count > 0;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={!enabled}>
        <Button
          type="button"
          size="sm"
          disabled={!enabled}
          title={enabled ? undefined : 'Tick lines that still need a reserve.'}
          data-testid="reserve-decide-button"
        >
          {`Decide (${count})`}
          <ChevronDown className="size-4" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={onReserveAsRequested}>
          <Check className="size-4" aria-hidden />
          Reserve as requested
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onReject}>
          <X className="size-4" aria-hidden />
          Reject...
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export default ReserveDecideControl;

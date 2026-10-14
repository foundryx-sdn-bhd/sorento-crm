'use client';

import * as React from 'react';
import { AlertTriangle, TriangleAlert } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { useLineVerdict } from '../../_shared/hooks/useProjects';
import type {
  QuotationLine,
  QuotationLineVerdict,
} from '../../_shared/types/project.types';
import { formatMyr } from './QuotationsPanel';
import type { LineDraft } from './quotationLineDraft';

const FLOOR_LEVEL_LABELS: Record<string, string> = {
  product: 'this product',
  category: 'its category',
  category_ancestor: 'a parent category',
  system: 'the company default',
};

/**
 * What is true about the line, ON THE SPOT (the client: "cannot wait until I save").
 *
 * **Non-standard** is one word for two facts: no product and no set is linked (a fact about the
 * row on screen, read off the LIVE draft), or the linked product or set is outside the series
 * (the server's). A line never shows two pills. **Below floor** is the server's too: while the
 * draft matches the saved row the saved verdicts show, and the moment it diverges the server is
 * asked (debounced, via `useLineVerdict`) with the same functions the save runs.
 */
export function LineFlags({
  quotationId,
  line,
  draft,
}: {
  /** Null on a scope not saved yet: the verdict is asked of a saved scope only. */
  quotationId: string | null;
  line: QuotationLine | null;
  draft: LineDraft;
}) {
  const productId = (draft.product_id ?? '').trim();
  const setId = (draft.product_set_id ?? '').trim();
  const price = (draft.unit_price ?? '').trim();
  const pristine =
    line != null &&
    (line.product_id ?? '') === productId &&
    (line.product_set_id ?? '') === setId &&
    (price || '') === (line.unit_price ?? '');
  const verdict = useLineVerdict(
    quotationId ?? '',
    {
      product_id: productId || undefined,
      product_set_id: setId || undefined,
      unit_price: price || undefined,
    },
    !pristine && quotationId !== null,
  );

  const belowFloor = pristine
    ? line.is_below_floor
    : Boolean(verdict.data?.is_below_floor);
  const nonStandard = pristine
    ? line.is_non_standard
    : Boolean(verdict.data?.is_non_standard);
  const floorText = pristine
    ? line
      ? describeFloor(line)
      : ''
    : describeVerdictFloor(verdict.data ?? null);

  const noTarget = !productId && !setId;
  if (!noTarget && !belowFloor && !nonStandard) return null;

  return (
    <div className="space-y-0.5">
      <div className="flex flex-wrap items-center gap-1">

        {belowFloor && (
          <Badge
            variant="destructive"
            className="gap-1 text-2xs"
            title={floorText}
          >
            <AlertTriangle className="size-3" aria-hidden />
            Below floor
          </Badge>
        )}
        {(noTarget || nonStandard) && (
          <Badge
            variant="secondary"
            className="gap-1 text-2xs"
            title={
              noTarget ? 'Not a catalogue product' : 'Outside the series this scope is quoted from'
            }
          >
            <TriangleAlert className="size-3" aria-hidden />
            Non-standard
          </Badge>
        )}
      </div>
      {belowFloor && floorText && (
        <p className="text-xs text-destructive">{floorText}</p>
      )}
    </div>
  );
}

function describeVerdictFloor(verdict: QuotationLineVerdict | null): string {
  if (!verdict?.floor_value) return 'Below the floor for this item.';
  const level = verdict.floor_level
    ? (FLOOR_LEVEL_LABELS[verdict.floor_level] ?? verdict.floor_level)
    : 'this item';
  return `Floor is ${formatMyr(verdict.floor_value)}, set on ${level}.`;
}

/** Says which rule bit, so the salesperson knows whose policy to argue with. */
function describeFloor(line: QuotationLine): string {
  if (!line.floor_value_applied) return 'Below the floor for this item.';
  const level = line.floor_level_applied
    ? (FLOOR_LEVEL_LABELS[line.floor_level_applied] ?? line.floor_level_applied)
    : 'policy';
  return `Floor was ${formatMyr(line.floor_value_applied)}, set on ${level}.`;
}

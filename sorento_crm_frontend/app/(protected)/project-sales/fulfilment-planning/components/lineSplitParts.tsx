'use client';

import * as React from 'react';
import { Input } from '@/components/ui/input';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { OrderInquiryVerbPill } from '../../_shared/components/OrderInquiryVerbPill';
import {
  formatSplitDate,
  targetDateOf,
  type ChangeWord,
  type SplitDraft,
  type SplitLine,
} from '../lib/lineSplitModel';

/** The value the AUTOCOUNT LINE select uses for a new line (not in AutoCount yet) and for Cancel. */
export const SITS_ON_NEW = '__new_line__';
export const SITS_ON_CANCEL = '__cancel__';

/** The change pill, in the verb colours the OI screens already use (UAC A11). */
export function ChangePill({ word }: { word: ChangeWord }) {
  if (word === 'CANCEL')
    return (
      <span
        className={`${STATUS_PILL_BASE} normal-case ${statusPillClass('rejected')}`}
      >
        CANCEL
      </span>
    );
  return <OrderInquiryVerbPill verb={word} />;
}

/** `Line 480`, or `The 417 line` when AutoCount gave no number. */
export function lineTitle(line: SplitLine): string {
  return line.lineNo != null
    ? `Line ${line.lineNo}`
    : `The ${line.baseQty || line.nowQty} line`;
}

export function sitsOnValue(split: SplitDraft): string {
  if (split.isCancel) return SITS_ON_CANCEL;
  return split.sitsOnLineId ?? SITS_ON_NEW;
}

/**
 * AUTOCOUNT LINE options (UAC A7): this line, every other line of the same item and
 * location with the date a split takes there, a new line, Cancel.
 */
export function sitsOnOptions(
  origin: SplitLine,
  groupLines: SplitLine[],
): SearchableSelectOption[] {
  return [
    { value: origin.lineId, label: `${lineTitle(origin)} (this line)` },
    ...groupLines
      .filter((line) => line.lineId !== origin.lineId)
      .map((line) => {
        const date = formatSplitDate(targetDateOf(line));
        return {
          value: line.lineId,
          label: date ? `${lineTitle(line)} · ${date}` : lineTitle(line),
        };
      }),
    { value: SITS_ON_NEW, label: 'New line' },
    { value: SITS_ON_CANCEL, label: 'Cancel' },
  ];
}

/** The AUTOCOUNT LINE cell. Always holds a value (this line at least), so not clearable. */
export function SitsOnSelect({
  split,
  origin,
  groupLines,
  label,
  onChange,
}: {
  split: SplitDraft;
  origin: SplitLine;
  groupLines: SplitLine[];
  label: string;
  onChange: (value: string) => void;
}) {
  return (
    <SearchableSelect
      aria-label={label}
      size="sm"
      value={sitsOnValue(split)}
      onChange={(value) => value && onChange(value)}
      options={sitsOnOptions(origin, groupLines)}
      truncateTriggerLabel
      wrapOptions
      className="min-w-0 w-full"
    />
  );
}

/**
 * A whole-number qty typed as text, `inputMode` numeric for the phone keypad. It may be
 * emptied while typing (NUMBER-FIELDS): nothing rewrites the box, an empty box counts as 0,
 * and a zero split is dropped when focus leaves its line (UAC A4).
 */
export function QtyInput({
  value,
  onValue,
  ...props
}: Omit<
  React.ComponentProps<typeof Input>,
  'value' | 'onChange' | 'type' | 'inputMode'
> & {
  value: number;
  onValue: (qty: number) => void;
}) {
  const [draft, setDraft] = React.useState(() => String(value));
  // The sheet moved the qty itself (a drop, an undo): the figure replaces the typed text.
  if ((draft === '' ? 0 : Number(draft)) !== value) setDraft(String(value));
  return (
    <Input
      {...props}
      type="text"
      inputMode="numeric"
      autoComplete="off"
      value={draft}
      onChange={(event) => {
        const digits = event.target.value.replace(/\D/g, '');
        setDraft(digits);
        onValue(digits === '' ? 0 : Number(digits));
      }}
    />
  );
}

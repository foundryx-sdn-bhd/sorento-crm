'use client';

import * as React from 'react';
import { MoreHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuRadioGroup,
  ContextMenuRadioItem,
  ContextMenuSeparator,
  ContextMenuShortcut,
} from '@/components/ui/context-menu';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { OrderInquiryVerbPill } from '../../_shared/components/OrderInquiryVerbPill';
import {
  lineLabel,
  type ChangeWord,
  type SplitDraft,
  type SplitLine,
} from '../lib/lineSplitModel';

/** The value a "Sits on" control uses for a new line (not in AutoCount yet) and for Cancel. */
export const SITS_ON_NEW = '__new_line__';
export const SITS_ON_CANCEL = '__cancel__';

/** The change pill, in the verb colours the OI screens already use (UAC A9). */
export function ChangePill({ word }: { word: ChangeWord }) {
  return (
    <OrderInquiryVerbPill verb={word === 'CANCEL' ? 'CANCEL_BALANCE' : word} />
  );
}

/** What a split card / zoom row / phone card can be asked to do (UAC A7, A8). */
export interface SplitActions {
  splitInTwo: () => void;
  newDate: () => void;
  cancel: () => void;
  backToOwn: () => void;
  sitsOn: (value: string) => void;
}

export function sitsOnValue(split: SplitDraft): string {
  if (split.isCancel) return SITS_ON_CANCEL;
  return split.sitsOnLineId ?? SITS_ON_NEW;
}

/** The lines a split of `origin` may sit on: its group, a new line, or Cancel. */
export function sitsOnOptions(
  origin: SplitLine,
  groupLines: SplitLine[],
): SearchableSelectOption[] {
  return [
    ...groupLines.map((line) => ({
      value: line.lineId,
      label: `${line.lineId === origin.lineId ? 'Own line, ' : ''}${lineLabel(line)}`,
    })),
    { value: SITS_ON_NEW, label: 'New line (not in AutoCount yet)' },
    { value: SITS_ON_CANCEL, label: 'Cancel' },
  ];
}

export function SitsOnSelect({
  split,
  origin,
  groupLines,
  onChange,
}: {
  split: SplitDraft;
  origin: SplitLine;
  groupLines: SplitLine[];
  onChange: (value: string) => void;
}) {
  return (
    <SearchableSelect
      aria-label="Sits on"
      size="sm"
      value={sitsOnValue(split)}
      onChange={(value) => value && onChange(value)}
      options={sitsOnOptions(origin, groupLines)}
      truncateTriggerLabel
      wrapOptions
      className="min-w-0 flex-1"
    />
  );
}

type MenuKind = 'context' | 'dropdown';

/** The split's menu items, for a right-click menu or the "⋯" button. */
export function SplitMenuItems({
  kind,
  split,
  origin,
  groupLines,
  actions,
}: {
  kind: MenuKind;
  split: SplitDraft;
  origin: SplitLine;
  groupLines: SplitLine[];
  actions: SplitActions;
}) {
  const Item = kind === 'context' ? ContextMenuItem : DropdownMenuItem;
  const Shortcut =
    kind === 'context' ? ContextMenuShortcut : DropdownMenuShortcut;
  const Separator =
    kind === 'context' ? ContextMenuSeparator : DropdownMenuSeparator;
  const Label = kind === 'context' ? ContextMenuLabel : DropdownMenuLabel;
  const RadioGroup =
    kind === 'context' ? ContextMenuRadioGroup : DropdownMenuRadioGroup;
  const Radio =
    kind === 'context' ? ContextMenuRadioItem : DropdownMenuRadioItem;
  return (
    <>
      <Item onSelect={actions.splitInTwo} disabled={split.qty < 2}>
        Split in two
        <Shortcut>S</Shortcut>
      </Item>
      <Item onSelect={actions.newDate} disabled={split.isCancel}>
        Move to new date…
        <Shortcut>N</Shortcut>
      </Item>
      <Item onSelect={actions.cancel} disabled={split.isCancel}>
        Cancel this qty
        <Shortcut>C</Shortcut>
      </Item>
      <Item onSelect={actions.backToOwn}>
        Back to its own line and date
        <Shortcut>Del</Shortcut>
      </Item>
      <Separator />
      <Label>Sits on</Label>
      <RadioGroup
        value={split.isCancel ? '' : sitsOnValue(split)}
        onValueChange={actions.sitsOn}
      >
        {groupLines.map((line) => (
          <Radio key={line.lineId} value={line.lineId}>
            {`${line.lineId === origin.lineId ? 'Own line, ' : ''}${lineLabel(line)}`}
          </Radio>
        ))}
        <Radio value={SITS_ON_NEW}>New line (not in AutoCount yet)</Radio>
      </RadioGroup>
    </>
  );
}

/** The "⋯" button holding the same menu. */
export function SplitMenuButton(props: {
  split: SplitDraft;
  origin: SplitLine;
  groupLines: SplitLine[];
  actions: SplitActions;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          mode="icon"
          size="sm"
          aria-label="More actions"
        >
          <MoreHorizontal aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-60">
        <SplitMenuItems kind="dropdown" {...props} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** A typed whole-number qty, `inputMode` decimal for the phone keypad. */
export function QtyInput({
  value,
  onValue,
  onBlur,
  className,
  splitId,
}: {
  value: number;
  onValue: (qty: number) => void;
  onBlur?: () => void;
  className?: string;
  splitId: string;
}) {
  return (
    <Input
      variant="sm"
      aria-label="Qty"
      inputMode="decimal"
      data-split-qty={splitId}
      value={String(value)}
      onChange={(event) => {
        const digits = event.target.value.replace(/[^0-9]/g, '');
        onValue(digits === '' ? 0 : Number(digits));
      }}
      onBlur={onBlur}
      onFocus={(event) => event.target.select()}
      className={className}
    />
  );
}

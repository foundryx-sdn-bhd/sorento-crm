'use client';

import * as React from 'react';
import { NumberInput, type NumberInputProps } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft, type NumberFieldRules } from '@/lib/number-field';

type CommittedNumberInputProps = Omit<
  NumberInputProps,
  'value' | 'onValueChange' | 'onChange' | 'error'
> & {
  /** The figure in force (the last valid entry), held by the screen; null when there is none. */
  value: number | null;
  /** Called only with a figure that passes `rules`; a bad entry is not passed on. */
  onCommit: (value: number) => void;
  /** Called when the box is emptied and `rules.optional` accepts that as "no value". */
  onClear?: () => void;
  rules?: NumberFieldRules;
};

/**
 * A number box for a screen that applies each valid figure as it is typed (no Save of its
 * own). It keeps exactly what the operator typed, blank and partial entries included, and
 * says in one sentence why a blank or bad entry is not applied; the screen keeps the last
 * valid figure until a valid one is typed. If the screen moves the figure itself, that
 * figure replaces the typed text.
 */
export function CommittedNumberInput({ value, onCommit, onClear, rules, ...props }: CommittedNumberInputProps) {
  // The typed text, and the figure in force when it was typed: while the screen still holds
  // that figure (a bad entry was not applied) or has taken this one, the text stays.
  const [draft, setDraft] = React.useState<{ text: string; base: number | null } | null>(null);
  const showDraft = draft !== null && (draft.base === value || parseNumberDraft(draft.text) === value);
  const error = showDraft ? numberFieldError(draft.text, rules) : undefined;
  return (
    <NumberInput
      {...props}
      value={showDraft ? draft.text : value === null ? '' : String(value)}
      error={error}
      onChange={(e) => {
        const text = e.target.value;
        setDraft({ text, base: showDraft ? draft.base : value });
        if (numberFieldError(text, rules)) return;
        const figure = parseNumberDraft(text);
        if (figure === null) onClear?.();
        else onCommit(figure);
      }}
    />
  );
}

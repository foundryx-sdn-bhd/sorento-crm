'use client';

import * as React from 'react';
import { NumberInput, type NumberInputProps } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft, type NumberFieldRules } from '@/lib/number-field';

type CommittedNumberInputProps = Omit<
  NumberInputProps,
  'value' | 'onValueChange' | 'onChange' | 'error'
> & {
  /** The figure in force (the last valid entry), held by the screen. */
  value: number;
  /** Called only with a figure that passes `rules`; a blank or bad entry is not passed on. */
  onCommit: (value: number) => void;
  rules?: NumberFieldRules;
};

/**
 * A number box for a screen that applies each valid figure as it is typed (no Save of its
 * own). It keeps exactly what the operator typed, blank and partial entries included, and
 * says in one sentence why a blank or bad entry is not applied; the screen keeps the last
 * valid figure until a valid one is typed. If the screen moves the figure itself, that
 * figure replaces the typed text.
 */
export function CommittedNumberInput({ value, onCommit, rules, ...props }: CommittedNumberInputProps) {
  const [draft, setDraft] = React.useState<string | null>(null);
  const parsed = draft === null ? null : parseNumberDraft(draft);
  const showDraft = draft !== null && (parsed === null || parsed === value);
  const error = showDraft ? numberFieldError(draft, rules) : undefined;
  return (
    <NumberInput
      {...props}
      value={showDraft ? draft : String(value)}
      error={error}
      onChange={(e) => {
        const text = e.target.value;
        setDraft(text);
        if (numberFieldError(text, rules)) return;
        onCommit(Number(text));
      }}
    />
  );
}

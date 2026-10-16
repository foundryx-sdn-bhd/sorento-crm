'use client';

/**
 * A number box for a surface that applies each edit as it is made (a canvas inspector).
 * It keeps whatever was typed. A valid figure goes to `onCommit` at once; an empty or
 * invalid one commits nothing and says why under the box.
 */

import { useState, type ComponentProps } from 'react';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft, type NumberFieldRules } from '@/lib/number-field';

type LiveNumberInputProps = Omit<
  ComponentProps<typeof NumberInput>,
  'value' | 'onChange' | 'onValueChange' | 'error' | 'min' | 'max'
> &
  NumberFieldRules & {
    /** Null draws an empty box. */
    value: number | null;
    onCommit: (value: number) => void;
  };

export function LiveNumberInput({
  value,
  onCommit,
  min,
  max,
  integer,
  ...props
}: LiveNumberInputProps) {
  const [draft, setDraft] = useState<string>(value === null ? '' : String(value));
  const [error, setError] = useState<string | undefined>(undefined);
  const [seen, setSeen] = useState<number | null>(value);
  // The layer changed from outside (another selection, an undo): follow it.
  if (value !== seen) {
    setSeen(value);
    if (parseNumberDraft(draft) !== value) {
      setDraft(value === null ? '' : String(value));
      setError(undefined);
    }
  }
  return (
    <NumberInput
      {...props}
      min={min}
      max={max}
      value={draft}
      error={error}
      onChange={(e) => {
        const raw = e.target.value;
        setDraft(raw);
        const message = numberFieldError(raw, { min, max, integer });
        setError(message);
        if (!message) onCommit(parseNumberDraft(raw) as number);
      }}
    />
  );
}

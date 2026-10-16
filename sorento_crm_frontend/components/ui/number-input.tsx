'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { parseNumberDraft } from '@/lib/number-field';
import { Input } from '@/components/ui/input';

type InputProps = React.ComponentProps<typeof Input>;

type NumberInputProps = Omit<InputProps, 'type' | 'value' | 'onChange'> & {
  /** Numeric mode: pass `value` as number | null with `onValueChange`. */
  value?: number | string | null;
  onValueChange?: (value: number | null) => void;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
  /** One-sentence message shown under the field; sets aria-invalid. */
  error?: string;
};

/**
 * The one number field. Holds what the operator typed (empty and partial drafts
 * included) and never rewrites it; the screen checks the final value on save.
 */
function NumberInput({ value, onValueChange, onChange, error, className, id, ...props }: NumberInputProps) {
  const autoId = React.useId();
  const messageId = `${id ?? autoId}-error`;
  const numeric = onValueChange !== undefined;

  const [draft, setDraft] = React.useState<string>(() =>
    value === null || value === undefined ? '' : String(value),
  );
  // Re-sync only when the parent's value differs from what the draft parses to.
  const parsedDraft = parseNumberDraft(draft);
  const external = numeric ? parseNumberDraft(value as number | string | null | undefined) : null;
  if (numeric && external !== parsedDraft) {
    setDraft(external === null ? '' : String(external));
  }

  const shown = numeric ? draft : value === null ? '' : value;
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (numeric) {
      const raw = e.target.value;
      setDraft(raw);
      onValueChange?.(parseNumberDraft(raw));
    }
    onChange?.(e);
  };

  return (
    <>
      <Input
        {...props}
        id={id}
        type="number"
        {...(numeric || value !== undefined ? { value: shown } : {})}
        onChange={numeric || onChange ? handleChange : undefined}
        className={className}
        aria-invalid={error ? true : props['aria-invalid']}
        aria-describedby={error ? messageId : props['aria-describedby']}
      />
      {error ? (
        <div id={messageId} data-slot="number-input-error" className={cn('mt-1 text-xs font-normal text-destructive')}>
          {error}
        </div>
      ) : null}
    </>
  );
}

export { NumberInput };
export type { NumberInputProps };

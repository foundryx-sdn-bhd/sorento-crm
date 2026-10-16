'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';
import { numberFieldError, parseNumberDraft, type NumberFieldRules } from '@/lib/number-field';
import { Input } from '@/components/ui/input';

type InputProps = React.ComponentProps<typeof Input>;

type NumberInputProps = Omit<InputProps, 'type' | 'value' | 'onChange'> & {
  /** Numeric mode: pass `value` as number | null with `onValueChange`. */
  value?: number | string | null;
  /** `null` = nothing typed; `NaN` = text the browser cannot read as a number (e.g. "1e"). */
  onValueChange?: (value: number | null) => void;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
  /** One-sentence message shown under the field; sets aria-invalid. */
  error?: string;
  /** Extra classes for the message line (e.g. to wrap it inside a flex row). */
  errorClassName?: string;
};

/**
 * The one number field. Holds what the operator typed (empty and partial drafts
 * included) and never rewrites it; the screen checks the final value on save.
 */
function NumberInput({ value, onValueChange, onChange, error, errorClassName, className, id, ...props }: NumberInputProps) {
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
      // A type=number box reports '' for bad input ("1e"): that is invalid, not "no value".
      onValueChange?.(e.target.validity?.badInput ? NaN : parseNumberDraft(raw));
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
        <div id={messageId} data-slot="number-input-error" className={cn('mt-1 text-xs font-normal text-destructive whitespace-normal break-words', errorClassName)}>
          {error}
        </div>
      ) : null}
    </>
  );
}

type LiveNumberInputProps = Omit<
  NumberInputProps,
  'value' | 'onValueChange' | 'onChange' | 'error'
> & {
  /** The figure in force (the last valid entry), held by the screen; null when there is none. */
  value: number | null;
  /** Called only with a figure that passes `rules`; a bad entry is never passed on. */
  onCommit: (value: number) => void;
  /** Called when the box is emptied and `rules.optional` accepts that as "no value". */
  onClear?: () => void;
  rules?: NumberFieldRules;
  /** Told after each edit whether the box now holds a usable entry (for a sibling toggle). */
  onValidityChange?: (valid: boolean) => void;
};

/**
 * A number box for a screen that applies each valid figure as it is typed (no Save of its
 * own). Keeps exactly what was typed; a blank or bad entry is never committed and never
 * rewrites the box. The sentence stays hidden while typing, shows at blur, then follows
 * the field live until it is valid. If the screen moves the figure itself, that figure
 * replaces the typed text.
 */
function LiveNumberInput({ value, onCommit, onClear, rules, onBlur, onValidityChange, ...props }: LiveNumberInputProps) {
  const [draft, setDraft] = React.useState<string>(value === null ? '' : String(value));
  const [bad, setBad] = React.useState(false);
  const [show, setShow] = React.useState(false);
  const [edited, setEdited] = React.useState(false);
  const [seen, setSeen] = React.useState<number | null>(value);
  if (value !== seen) {
    setSeen(value);
    if (parseNumberDraft(draft) !== value) {
      setDraft(value === null ? '' : String(value));
      setBad(false);
      setShow(false);
    }
  }
  const check = bad ? NaN : draft;
  const message = numberFieldError(check, rules);
  // Some problems cannot be a prefix of a valid figure, so they show at once: text the
  // browser cannot read, a box emptied by the operator, a figure past the maximum.
  const parsed = parseNumberDraft(draft);
  const final =
    edited && (bad || draft.trim() === '' || (parsed !== null &&
        ((rules?.max !== undefined && parsed > rules.max) ||
          (parsed < 0 && (rules?.min ?? -1) >= 0))));
  return (
    <NumberInput
      {...props}
      value={draft}
      error={show || final ? message : undefined}
      onBlur={(e) => {
        if (message) setShow(true);
        onBlur?.(e);
      }}
      onChange={(e) => {
        const raw = e.target.value;
        const isBad = Boolean(e.target.validity?.badInput);
        setDraft(raw);
        setBad(isBad);
        setEdited(true);
        const problem = numberFieldError(isBad ? NaN : raw, rules);
        onValidityChange?.(!problem);
        if (problem) return;
        setShow(false);
        const figure = parseNumberDraft(raw);
        if (figure === null) onClear?.();
        else onCommit(figure);
      }}
    />
  );
}

export { NumberInput, LiveNumberInput };
export type { NumberInputProps, LiveNumberInputProps };

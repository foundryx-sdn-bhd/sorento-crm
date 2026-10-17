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

// A box holding bad input ("1e") reports value ''; a non-empty value is a real figure.
const isBadInput = (e: React.ChangeEvent<HTMLInputElement>) => Boolean(e.target.validity?.badInput) && e.target.value === '';

function withNaNValue(e: React.ChangeEvent<HTMLInputElement>): React.ChangeEvent<HTMLInputElement> {
  const read = (obj: object, key: string | symbol, special?: unknown) => {
    const v = Reflect.get(obj, key, obj);
    return typeof v === 'function' ? v.bind(obj) : special !== undefined ? special : v;
  };
  const target = new Proxy(e.target, {
    get: (t, key) => read(t, key, key === 'value' ? 'NaN' : undefined),
  });
  return new Proxy(e, {
    get: (t, key) => (key === 'target' || key === 'currentTarget' ? target : read(t, key)),
  });
}

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

  // 'NaN' is what a screen stores for bad input: never write it into the box, so the typed text stays.
  const shown = numeric ? draft : value === null || value === 'NaN' ? '' : value;
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (numeric) {
      const raw = e.target.value;
      setDraft(raw);
      // A type=number box reports '' for bad input ("1e"): that is invalid, not "no value".
      onValueChange?.(isBadInput(e) ? NaN : parseNumberDraft(raw));
    }
    if (!numeric && onChange) {
      // String mode: a box the browser flags as bad input reads '' ("1e"), which a screen would
      // take as "no value". Hand it the string 'NaN' instead (invalid in numberFieldError).
      onChange(isBadInput(e) ? withNaNValue(e) : e);
      return;
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
  /** The screen refused an action because of this box: show its sentence now. */
  showMessage?: boolean;
};

/**
 * A number box for a screen that applies each valid figure as it is typed (no Save of its
 * own). Keeps exactly what was typed; a blank or bad entry is never committed and never
 * rewrites the box. The sentence stays hidden while typing, shows at blur, then follows
 * the field live until it is valid. If the screen moves the figure itself, that figure
 * replaces the typed text.
 */
function LiveNumberInput({ value, onCommit, onClear, rules, onBlur, onValidityChange, showMessage, ...props }: LiveNumberInputProps) {
  const [draft, setDraft] = React.useState<string>(value === null ? '' : String(value));
  const [bad, setBad] = React.useState(false);
  const [show, setShow] = React.useState(false);
  const [touched, setTouched] = React.useState(false);
  const [seen, setSeen] = React.useState<number | null>(value);
  if (value !== seen) {
    setSeen(value);
    if (parseNumberDraft(draft) !== value) {
      setDraft(value === null ? '' : String(value));
      setBad(false);
      setShow(false);
      setTouched(false);
    }
  }
  const check = bad ? NaN : draft;
  const message = numberFieldError(check, rules);
  return (
    <NumberInput
      {...props}
      value={draft}
      error={show || showMessage ? message : undefined}
      onBlur={(e) => {
        if (message && touched) setShow(true);
        onBlur?.(e);
      }}
      onChange={(e) => {
        const raw = e.target.value;
        const isBad = isBadInput(e);
        setDraft(raw);
        setTouched(true);
        setBad(isBad);
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

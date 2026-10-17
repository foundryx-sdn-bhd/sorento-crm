'use client';

import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';

/**
 * One field, read or typed IN THE SAME PLACE.
 *
 * One component rather than two blocks behind a ternary, because the ADR's rule is that
 * nothing moves between view and edit - and the cheapest way to guarantee that is for one
 * piece of code to own both.
 */
export function PackingListField({
  label,
  name,
  view,
  editing,
  draft,
  onChange,
  type = 'text',
  numeric = false,
  step,
  error,
}: {
  label: string;
  name: string;
  view: React.ReactNode;
  editing: boolean;
  draft: Record<string, string>;
  onChange: (name: string, value: string) => void;
  type?: string;
  /** A number box (NumberInput) rather than a text one. */
  numeric?: boolean;
  step?: string;
  /** The sentence under a number box, once a Save has been refused on it. */
  error?: string;
}) {
  return (
    <div className="min-w-0">
      <p className="text-sm text-muted-foreground">{label}</p>
      {editing ? (
        numeric ? (
          <NumberInput
            className="mt-1"
            step={step}
            value={draft[name] ?? ''}
            onChange={(e) => onChange(name, e.target.value)}
            aria-label={label}
            error={error}
          />
        ) : (
          <Input
            className="mt-1"
            type={type}
            step={step}
            value={draft[name] ?? ''}
            onChange={(e) => onChange(name, e.target.value)}
            aria-label={label}
          />
        )
      ) : (
        <p className="font-medium break-words">{view}</p>
      )}
    </div>
  );
}

export default PackingListField;

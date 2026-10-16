'use client';

import { useState } from 'react';
import { Check, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { readable } from '@/lib/spec-readable';
import { cn } from '@/lib/utils';
import type { SpecProposalValue } from '@/components/spec-proposals';

/**
 * One proposal's value, being corrected, in the cell it was being read in.
 *
 * The reader is what makes a flyer batch worth having and it is also what gets a
 * value wrong: a size read off the wrong line, a finish read off the neighbouring
 * card. Before this, the only answer was to untick the row, open the product,
 * type the value there and come back - four screens for one word, which is what
 * makes a reviewer stop trusting the batch and start doing it all by hand.
 *
 * **The widget follows the KEY's registry type, never the value's shape**
 * (AC-F.3): a closed vocabulary gets a dropdown of exactly what the registry
 * allows, a boolean gets Yes/No, a measurement gets a number input with the
 * registry's unit beside it, and everything else gets text. The server validates
 * the same way through `value_for_registry`, so this is a shortcut to the right
 * answer rather than the rule itself.
 *
 * The same shape as `components/spec-table/SpecValueCell`'s editor, deliberately:
 * a merchandiser who has edited a value on the product's Specifications tab is
 * looking at the same three controls here. It is not that component because that
 * one is built around a `SpecTableRow` - a stored value with provenance, a
 * tombstone action and a conflict - and a proposal is none of those things.
 *
 * No clearable: the value is required. A proposal with no value is not a proposal,
 * and taking a key away is what Dismiss does.
 */

const BOOLEAN_OPTIONS = [
  { value: 'true', label: 'Yes' },
  { value: 'false', label: 'No' },
];

/**
 * The editor's string form of a proposed value.
 *
 * A list of one shows as that value. A list of SEVERAL never reaches here - the
 * group offers no pencil for it, because this editor holds one value and saving
 * it would drop the rest (`MULTI_VALUE_HINT` in `ProductProposalGroup`).
 */
export function toDraft(value: SpecProposalValue): string {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.length > 0 ? String(value[0]) : '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

/** Back into the type the registry says the key holds, or null when it is not one. */
export function fromDraft(
  draft: string,
  dataType: string,
): SpecProposalValue | null {
  const text = draft.trim();
  if (!text) return null;
  if (dataType === 'boolean') return text === 'true';
  if (dataType === 'numeric') {
    const number = Number(text);
    return Number.isFinite(number) ? number : null;
  }
  return text;
}

export interface ProposalValueEditorProps {
  label: string;
  dataType: string;
  unit?: string | null;
  /** The key's closed vocabulary, when it has one. Empty means a typed value. */
  allowedValues?: string[] | null;
  draft: string;
  onDraftChange: (draft: string) => void;
  onSave: () => void;
  onCancel: () => void;
  busy?: boolean;
}

export function ProposalValueEditor({
  label,
  dataType,
  unit,
  allowedValues,
  draft,
  onDraftChange,
  onSave,
  onCancel,
  busy = false,
}: ProposalValueEditorProps) {
  const [touched, setTouched] = useState(false);
  const options = allowedValues ?? [];
  const parsed = fromDraft(draft, dataType);
  // A measurement is checked when Save is pressed, then the message follows the box.
  const [checked, setChecked] = useState(false);
  const numeric = dataType === 'numeric';
  const numberError = numeric && checked ? numberFieldError(draft) : undefined;
  const submit = () => {
    if (numeric) {
      setChecked(true);
      if (numberFieldError(draft)) return;
    }
    onSave();
  };

  return (
    <div
      className="flex min-w-0 items-center gap-1.5"
      data-flyer-spec-editor={label}
    >
      <div className="min-w-0 flex-1">
        {dataType === 'boolean' ? (
          <SearchableSelect
            value={draft}
            onChange={onDraftChange}
            options={BOOLEAN_OPTIONS}
            size="sm"
            disabled={busy}
            placeholder="Yes or No"
          />
        ) : options.length > 0 ? (
          <SearchableSelect
            value={draft}
            onChange={onDraftChange}
            options={options.map((option) => ({
              value: option,
              label: readable(option),
            }))}
            size="sm"
            disabled={busy}
            placeholder={`Pick a ${label.toLowerCase()}`}
          />
        ) : (
          <div className="relative">
            {numeric ? (
              <NumberInput
                value={draft}
                onChange={(event) => {
                  setTouched(true);
                  onDraftChange(event.target.value);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') submit();
                  if (event.key === 'Escape') onCancel();
                }}
                disabled={busy}
                aria-label={label}
                autoFocus={!touched}
                className={cn('h-8 text-sm', unit && 'pe-10')}
                error={numberError}
              />
            ) : (
              <Input
                value={draft}
                onChange={(event) => {
                  setTouched(true);
                  onDraftChange(event.target.value);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && parsed !== null) onSave();
                  if (event.key === 'Escape') onCancel();
                }}
                disabled={busy}
                type="text"
                aria-label={label}
                autoFocus={!touched}
                className={cn('h-8 text-sm', unit && 'pe-10')}
              />
            )}
            {/* The unit is the registry's and is never typed: "770mm" into a
                measurement stores a string the ranker cannot compare. */}
            {unit && (
              <span className="pointer-events-none absolute inset-y-0 end-2 flex items-center text-xs text-muted-foreground">
                {unit}
              </span>
            )}
          </div>
        )}
      </div>
      <Button
        size="icon"
        variant="ghost"
        className="size-8 shrink-0"
        aria-label={`Save ${label}`}
        disabled={busy || (!numeric && parsed === null)}
        onClick={submit}
      >
        <Check className="size-4" />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="size-8 shrink-0"
        aria-label={`Cancel editing ${label}`}
        disabled={busy}
        onClick={onCancel}
      >
        <X className="size-4" />
      </Button>
    </div>
  );
}

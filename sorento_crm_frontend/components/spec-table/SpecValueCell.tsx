'use client';

import { useState } from 'react';
import { Check, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { readable, readableValue } from '@/lib/spec-readable';
import { cn } from '@/lib/utils';
import { findVocabularyMatch } from './specVocabulary';
import type { SpecScalar, SpecTableRow } from './types';

/**
 * One spec's value, read or edited, in the same cell.
 *
 * The editor is the renderer that used to live in `AddSpecByHand`: a boolean gets
 * Yes/No, a dropdown key gets a `SearchableSelect`, and anything else gets a typed
 * input. Lifted here rather than copied, so the add-a-spec flow and an edit of an
 * existing row cannot disagree about what a key's editor is.
 *
 * A dropdown key gets the dropdown even while its vocabulary is EMPTY - a fresh key
 * has no words yet, and falling through to free text there stored a value the key's
 * vocabulary had never heard of, which is exactly the split the registry exists to
 * prevent. The one affordance an empty dropdown offers is "Add ... to <key>", so the
 * first word becomes vocabulary and the value in the same motion.
 *
 * Swapping in place is the point (AC-A.1b). An editor that opens below the row, or in
 * a modal, moves every other row on the page: the reader loses the line they were on,
 * and on a phone the value they came to change scrolls out of view as they start.
 *
 * Props-driven with no data layer of its own - no `apiFetch`, no service import, no
 * react-query. Milestone 2's supplier portal renders this same cell against a
 * different API and a different principal, and a component that fetched for itself
 * could not go there.
 */

const BOOLEAN_OPTIONS = [
  { value: 'true', label: 'Yes' },
  { value: 'false', label: 'No' },
];

/** Parse the editor's string back into the type the registry says the key holds. */
function toScalar(draft: string, dataType: string): SpecScalar | null {
  const text = draft.trim();
  if (!text) return null;
  if (dataType === 'boolean') return text === 'true';
  if (dataType === 'numeric') {
    const number = Number(text);
    return Number.isFinite(number) ? number : null;
  }
  return text;
}

/** The editor's string form of a stored value. Exported so the table can seed a draft. */
export function toDraft(value: SpecScalar | null): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return String(value);
}

export function SpecValueCell({
  row,
  editing,
  draft,
  onDraftChange,
  onStartEdit,
  onCancel,
  onSave,
  onAddValueToKey,
  synonyms,
  busy = false,
}: {
  row: SpecTableRow;
  editing: boolean;
  /**
   * The value being edited, owned by the table rather than by this cell. The grid
   * remounts a cell whenever the registry refetches underneath it, and a word added
   * to the vocabulary triggers exactly that refetch - a draft held here was reset to
   * the old value at the very moment it should have become the new word.
   */
  draft: string;
  onDraftChange: (draft: string) => void;
  onStartEdit: () => void;
  onCancel: () => void;
  onSave: (value: SpecScalar) => Promise<void>;
  /** Omitted when the user may not extend a key's vocabulary: the offer disappears. */
  onAddValueToKey?: (value: string) => Promise<void>;
  /** The key's merged synonyms, so "brushed brass" finds "Brushed Brass". */
  synonyms?: Record<string, string[]>;
  busy?: boolean;
}) {
  const [saving, setSaving] = useState(false);
  // Set by a Save that found a blank or invalid number; the sentence then follows the typing.
  const [checked, setChecked] = useState(false);
  const numberError =
    row.dataType === 'numeric' ? numberFieldError(draft, {}) : undefined;

  // A key the registry no longer defines is still stored on the row, and is still worth
  // showing - it just has no editor, because there is no data type, no vocabulary and
  // no unit to write it back with. Crashing on it, or hiding it, both lose the value.
  if (row.unknownKey) {
    return (
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-sm" title={String(row.value ?? '')}>
          {readableValue(row.value, row.unit ?? undefined, row.valueLabels)}
        </span>
        <span className="text-[11px] text-muted-foreground">Not in the registry</span>
      </div>
    );
  }

  if (!editing) {
    const displayed = readableValue(row.value, row.unit ?? undefined, row.valueLabels);
    return (
      <button
        type="button"
        onClick={onStartEdit}
        className="block w-full truncate text-start text-sm hover:underline"
        title={displayed}
        data-spec-value={row.specKey}
      >
        {displayed}
      </button>
    );
  }

  const save = async () => {
    if (numberError) {
      setChecked(true);
      return;
    }
    const value = toScalar(draft, row.dataType);
    if (value === null) return;
    setSaving(true);
    try {
      await onSave(value).catch(() => {});
    } finally {
      setSaving(false);
    }
  };

  const disabled = saving || busy;

  return (
    <div className="flex min-w-0 items-center gap-1.5" data-spec-editor={row.specKey}>
      <div className="min-w-0 flex-1">
        {row.dataType === 'boolean' ? (
          <SearchableSelect
            value={draft}
            onChange={onDraftChange}
            options={BOOLEAN_OPTIONS}
            size="sm"
            disabled={disabled}
            placeholder="Yes or No"
          />
        ) : row.dataType === 'enum' || row.options.length > 0 ? (
          <SearchableSelect
            value={draft}
            onChange={onDraftChange}
            options={row.options.map((option) => ({
              value: option,
              label: row.valueLabels[option] ?? readable(option),
            }))}
            size="sm"
            disabled={disabled}
            placeholder={
              row.options.length > 0
                ? `Pick a ${row.label.toLowerCase()}`
                : `Type the first ${row.label.toLowerCase()}`
            }
            // The last row of the list, and only when the word genuinely is new. The
            // check runs against data already on screen, so the common case - the word
            // is there under another spelling - never costs a round trip. The server
            // runs the same comparison and is what actually decides (D11).
            createOption={
              onAddValueToKey
                ? {
                    label: (query) => {
                      const match = findVocabularyMatch(query, row.options, synonyms);
                      if (match) {
                        return (
                          <span className="text-muted-foreground">
                            &ldquo;{query}&rdquo; is already{' '}
                            <span className="font-medium text-foreground">
                              {row.valueLabels[match.value] ?? readable(match.value)}
                            </span>
                            {match.viaSynonym ? ' - pick it above' : ''}
                          </span>
                        );
                      }
                      return (
                        <span>
                          Add &ldquo;{query}&rdquo; to {row.label}
                        </span>
                      );
                    },
                    onCreate: (query) => {
                      if (findVocabularyMatch(query, row.options, synonyms)) return;
                      void onAddValueToKey(query)
                        .then(() => onDraftChange(query))
                        .catch(() => {});
                    },
                  }
                : undefined
            }
          />
        ) : (
          <div className="relative">
            {row.dataType === 'numeric' ? (
              <NumberInput
                value={draft}
                onChange={(event) => onDraftChange(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void save();
                  if (event.key === 'Escape') onCancel();
                }}
                disabled={disabled}
                aria-label={row.label}
                autoFocus
                error={checked ? numberError : undefined}
                className={cn('h-8 text-sm', row.unit && 'pe-10')}
              />
            ) : (
              <Input
                value={draft}
                onChange={(event) => onDraftChange(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void save();
                  if (event.key === 'Escape') onCancel();
                }}
                disabled={disabled}
                type="text"
                aria-label={row.label}
                autoFocus
                className={cn('h-8 text-sm', row.unit && 'pe-10')}
              />
            )}
            {/* The unit is the registry's and is never typed: a person entering
                "770mm" into a numeric key writes a string the ranker cannot compare. */}
            {row.unit && (
              <span className="pointer-events-none absolute end-2 top-0 flex h-8 items-center text-xs text-muted-foreground">
                {row.unit}
              </span>
            )}
          </div>
        )}
      </div>
      <Button
        size="icon"
        variant="ghost"
        className="size-8 shrink-0"
        aria-label={`Save ${row.label}`}
        disabled={disabled || (row.dataType !== 'numeric' && toScalar(draft, row.dataType) === null)}
        onClick={() => void save()}
      >
        <Check className="size-4" />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="size-8 shrink-0"
        aria-label={`Cancel editing ${row.label}`}
        disabled={disabled}
        onClick={onCancel}
      >
        <X className="size-4" />
      </Button>
    </div>
  );
}

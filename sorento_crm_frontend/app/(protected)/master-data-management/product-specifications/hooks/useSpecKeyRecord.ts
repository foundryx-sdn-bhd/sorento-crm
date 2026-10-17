'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { numberFieldError } from '@/lib/number-field';
import {
  seedValuesFor,
  seedWordsFor,
  valuePayload,
  wordPayload,
} from '../lib/vocabularyEdit';
import { useSpecRegistryMutations } from './useSpecRegistryMutations';
import { SPEC_REGISTRY_QUERY_KEY } from './useSpecRegistryQuery';
import { SPEC_COVERAGE_QUERY_KEY } from './useSpecCoverageQuery';
import { specKeyProductsKey } from './useSpecKeyProductsQuery';
import type { SpecDerivationRule, SpecRegistryKey } from '../types/productSpec.types';

export const dedupe = (list: string[]) => Array.from(new Set(list));

/**
 * The editable projection of a registry row (B.2).
 *
 * Everything a tab renders an input for lives here, seeded from the row on `edit()`
 * and diffed back into the ONE PATCH body on `save()`. Values and words are held as
 * two lists apiece - the live ones and the shipped ones taken away - rather than one
 * list plus a lock, because removing a value means something different depending on
 * who owns it and a lock cannot express that (ported from the retired
 * `SpecKeyEditor`).
 */
export interface SpecKeyDraft {
  label: string;
  /** What this field means to the chatbot; '' when unset. */
  parserHint: string;
  /** D12: the seed-key delete refusal tells the user to switch the key off instead. */
  isActive: boolean;
  /** Input string; '' clears the cap (AC-D.3 of the folded plan). */
  maxValue: string;
  /** Set by a refused Save; follows the field from then on. */
  maxValueError?: string;
  /** `allowed_values` currently in force. */
  liveValues: string[];
  /** Shipped values taken away. Survives a save that was not about them. */
  droppedValues: string[];
  /** value -> the words currently shown. */
  words: Record<string, string[]>;
  /** value -> the shipped words currently struck through. */
  droppedWords: Record<string, string[]>;
  /** value -> the display label a person reads instead of the slug (#423). */
  valueLabels: Record<string, string>;
  rules: SpecDerivationRule[];
}

/** A refused word (409): which one, the value it was added under (`_self` for an other
 *  name, null for a new value) and the server's sentence. */
export interface WordError {
  word: string;
  value: string | null;
  message: string;
}

export interface UseSpecKeyRecordResult {
  mode: 'view' | 'edit';
  wordError: WordError | null;
  draft: SpecKeyDraft | null;
  saving: boolean;
  edit: () => void;
  cancel: () => void;
  save: () => Promise<boolean>;
  setDraft: (updater: (draft: SpecKeyDraft) => SpecKeyDraft) => void;
}

/** Every value that could carry customer wording, merged the way the vocabulary is. */
function wordedValuesFor(row: SpecRegistryKey): string[] {
  const isBoolean = row.data_type === 'boolean';
  return dedupe([
    ...(isBoolean ? ['true'] : row.allowed_values),
    ...Object.keys(row.synonyms ?? {}),
    ...Object.keys(row.user_synonyms ?? {}),
    ...Object.keys(row.suppressed_synonyms ?? {}),
  ]);
}

/**
 * A registry row, projected into the editable shape (B.2, B.3).
 *
 * Exported so `ValuesAndWordsTab`/`RulesTab` render VIEW mode off this SAME
 * projection rather than a second, hand-written reading of the row - the "field
 * labels in both modes are identical" snapshot (G.8) holds by construction because
 * both modes walk the one shape, only swapping the control each field renders.
 */
export function projectSpecKeyDraft(row: SpecRegistryKey): SpecKeyDraft {
  const wordedValues = wordedValuesFor(row);
  const words: Record<string, string[]> = Object.fromEntries(
    wordedValues.map((value) => [
      value,
      dedupe([
        ...(row.synonyms?.[value] ?? []),
        ...(row.user_synonyms?.[value] ?? []),
      ]),
    ]),
  );
  const droppedWords: Record<string, string[]> = Object.fromEntries(
    Object.entries(row.suppressed_synonyms ?? {}).map(([value, list]) => [
      value,
      [...list],
    ]),
  );
  return {
    label: row.label,
    parserHint: row.parser_hint ?? '',
    isActive: row.is_active,
    maxValue:
      row.max_value === null || row.max_value === undefined
        ? ''
        : String(row.max_value),
    liveValues: [...row.allowed_values],
    droppedValues: [...(row.suppressed_values ?? [])],
    words,
    droppedWords,
    valueLabels: { ...(row.value_labels ?? {}) },
    // Given an identity on the way in so dragging (RulesTab) moves a RULE, not a
    // position, and Try it/preview keep reading the same row across an edit.
    rules: (row.effective_rules ?? row.derivation_rules ?? []).map((rule, index) => ({
      ...rule,
      _uid: rule._uid ?? `r${index}`,
    })),
  };
}

/** value_labels trimmed; a blank label drops the key (AC-D.3 of the folded plan). */
function trimmedValueLabels(labels: Record<string, string>): Record<string, string> {
  const next: Record<string, string> = {};
  for (const [value, label] of Object.entries(labels)) {
    const trimmed = label.trim();
    if (trimmed) next[value] = trimmed;
  }
  return next;
}

export function buildPatchBody(row: SpecRegistryKey, draft: SpecKeyDraft) {
  const { user_synonyms, suppressed_synonyms } = wordPayload(row, {
    words: draft.words,
    dropped: draft.droppedWords,
  });
  const { user_values, suppressed_values } = valuePayload(
    row,
    draft.liveValues,
    draft.droppedValues,
  );
  const maxValueTrimmed = draft.maxValue.trim();
  const maxValueNumber = maxValueTrimmed === '' ? null : Number(maxValueTrimmed);
  const maxValuePayload =
    maxValueTrimmed === ''
      ? { max_value: null }
      : Number.isFinite(maxValueNumber)
        ? { max_value: maxValueNumber }
        : {};

  return {
    label: draft.label.trim() || row.label,
    // No unit: it is fixed once saved (changing it would invalidate stored values).
    parser_hint: draft.parserHint.trim() || null,
    is_active: draft.isActive,
    ...maxValuePayload,
    user_synonyms,
    suppressed_synonyms,
    user_values,
    suppressed_values,
    value_labels: trimmedValueLabels(draft.valueLabels),
    derivation_rules: draft.rules,
  };
}

/**
 * Edit-mode state for one registry row (B.2), and the one PATCH `save()` sends.
 *
 * `useSpecKeyRecord(row)` re-derives its draft from whatever `row` is CURRENT the
 * moment `edit()` is called, so a key selected from a freshly-refetched list always
 * starts an edit session from what is on screen.
 */
export function useSpecKeyRecord(row: SpecRegistryKey | undefined): UseSpecKeyRecordResult {
  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [draft, setDraftState] = useState<SpecKeyDraft | null>(null);
  const [wordError, setWordError] = useState<WordError | null>(null);
  // The words the refusal was about, as they stood: any change to them clears it.
  const refusedWords = useRef<unknown>(null);
  const { update } = useSpecRegistryMutations();
  const queryClient = useQueryClient();

  const edit = useCallback(() => {
    if (!row) return;
    setDraftState(projectSpecKeyDraft(row));
    setMode('edit');
  }, [row]);

  const cancel = useCallback(() => {
    setWordError(null);
    setDraftState(null);
    setMode('view');
  }, []);

  const setDraft = useCallback((updater: (current: SpecKeyDraft) => SpecKeyDraft) => {
    setDraftState((current) => (current ? updater(current) : current));
  }, []);

  const wordsOf = useCallback(
    (d: SpecKeyDraft | null, value: string | null) =>
      value === null ? d?.liveValues : d?.words[value],
    [],
  );

  useEffect(() => {
    if (wordError && wordsOf(draft, wordError.value) !== refusedWords.current) {
      setWordError(null);
    }
  }, [draft, wordError, wordsOf]);

  const save = useCallback(async () => {
    if (!row || !draft) return false;
    const maxValueError = numberFieldError(draft.maxValue, { min: 0, optional: true });
    if (maxValueError) {
      setDraftState((current) => (current ? { ...current, maxValueError } : current));
      return false;
    }
    try {
      const body = buildPatchBody(row, draft);
      const updated = await update.mutateAsync({ specKey: row.spec_key, body });
      // Written straight onto the cache rather than via `invalidateQueries` - see
      // `useSpecRegistryMutations.update` for why this hook, not the mutation's own
      // `onSuccess`, owns the write.
      queryClient.setQueryData<{ keys: SpecRegistryKey[] }>(
        SPEC_REGISTRY_QUERY_KEY,
        (old) =>
          old
            ? {
                keys: old.keys.map((key) =>
                  key.spec_key === row.spec_key ? updated : key,
                ),
              }
            : old,
      );
      // S-12: the products it re-read now hold other values, so the Products tab
      // and the Choices counts (one query key) are fetched again, and so are the
      // header's products count and last read time.
      if (updated.products_updated > 0) {
        queryClient.invalidateQueries({ queryKey: specKeyProductsKey(row.spec_key) });
        queryClient.invalidateQueries({ queryKey: SPEC_COVERAGE_QUERY_KEY });
      }
      // AC-S1.16, D10 (fix round 3, D2): the save already re-read exactly the
      // products it changed; there is nothing left to press, so the toast reports
      // the count and stops - every time, "Saved. 0 products updated." included,
      // never a bare "Saved." that leaves the reader guessing whether it re-read.
      toast.success(
        `Saved. ${updated.products_updated} product${updated.products_updated === 1 ? '' : 's'} updated.`,
      );
      setWordError(null);
      setDraftState(null);
      setMode('view');
      return true;
    } catch (error) {
      // Matched by name, not `instanceof`: the service's `SpecWordRefusedError` is the
      // only thing that carries a `word` here.
      const refused = error as { name?: string; word?: unknown; value?: string | null; message?: string };
      if (refused?.name === 'SpecWordRefusedError' && typeof refused.word === 'string') {
        refusedWords.current = wordsOf(draft, refused.value ?? null);
        setWordError({
          word: refused.word,
          value: refused.value ?? null,
          message: refused.message ?? '',
        });
      }
      // useSpecRegistryMutations already toasted the reason; leave the session
      // open so nothing typed is lost and the refused field can be corrected.
      return false;
    }
  }, [row, draft, update, queryClient, wordsOf]);

  return {
    mode,
    wordError,
    draft,
    saving: update.isPending,
    edit,
    cancel,
    save,
    setDraft,
  };
}

// Re-exported so a tab needs one import for both the state hook and the seed
// lookups it hands each row (ported wholesale from `SpecKeyEditor`).
export { seedValuesFor, seedWordsFor };

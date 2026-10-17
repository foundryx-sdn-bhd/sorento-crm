'use client';

import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Switch } from '@/components/ui/switch';
import { WordsDataGrid } from '../WordsDataGrid';
import { dedupe, seedWordsFor, type SpecKeyDraft, type WordError } from '../../hooks/useSpecKeyRecord';
import type { SpecRegistryKey } from '../../types/productSpec.types';

/** The pseudo-value holding the words that name the SPECIFICATION itself ("oz",
 *  "ounce", "ounces" for Capacity (oz)) rather than one of its values (D7). */
const SELF_KEY = '_self';

// Fix round 3 (D1): reused rather than a `?? []` literal, so a spec with no `_self`
// words yet does not hand `WordsDataGrid` a fresh array identity on every render -
// see `ValuesAndWordsTab.tsx`'s own note on why that loops the renderer forever.
const EMPTY_WORDS: string[] = [];

/** The longest chatbot meaning the server stores. */
const HINT_MAX = 200;

/** One labelled control. The label is the only chrome a field needs, present in
 *  both view and edit so a field's identity never moves between the two (B.2). */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

export interface HeaderTabProps {
  row: SpecRegistryKey;
  mode: 'view' | 'edit';
  /** Null in view mode - the tab reads straight off `row` then. */
  draft: SpecKeyDraft | null;
  setDraft: (updater: (draft: SpecKeyDraft) => SpecKeyDraft) => void;
  /** The refused word, shown under its row when it was added as an other name. */
  wordError?: WordError | null;
}

/**
 * Details (AC-S1.12): Name, Unit (not on List specs), In use, Highest believable
 * value, and Other names for this specification as a small data grid (D7, D13) -
 * `_self`'s words, moved here from Choices and words, which never renders it. No
 * code name, no "Built in / Added here", no rule count, no Advanced anywhere on
 * this tab (D8): rules live only on their own tab.
 */
export function HeaderTab({ row, mode, draft, setDraft, wordError }: HeaderTabProps) {
  const isNumeric = row.data_type === 'numeric';
  const isList = row.data_type === 'enum';

  const otherNames = draft ? draft.words[SELF_KEY] ?? EMPTY_WORDS : row.synonyms?.[SELF_KEY] ?? EMPTY_WORDS;

  return (
    <div className="flex max-w-xl flex-col gap-4">
      <Field label="Name">
        {mode === 'edit' && draft ? (
          <Input
            value={draft.label}
            onChange={(event) => setDraft((d) => ({ ...d, label: event.target.value }))}
            className="h-8"
            aria-label="Name"
            maxLength={100}
          />
        ) : (
          <span className="text-sm">{row.label}</span>
        )}
      </Field>

      {!isList && (
        <Field label="Unit">
          {/* Text in both modes: a saved unit cannot change without invalidating
              every stored value. */}
          <div className="flex items-baseline gap-2">
            <span className="text-sm">{row.unit || '-'}</span>
            <span className="text-xs text-muted-foreground">Fixed once saved</span>
          </div>
        </Field>
      )}

      <Field label="In use">
        <Switch
          size="sm"
          aria-label="In use"
          checked={mode === 'edit' && draft ? draft.isActive : row.is_active}
          disabled={mode !== 'edit' || !draft}
          onCheckedChange={(checked) => setDraft((d) => ({ ...d, isActive: checked }))}
        />
      </Field>

      {isNumeric && (
        <Field label="Highest believable value">
          {mode === 'edit' && draft ? (
            <NumberInput
              step="1"
              min="0"
              placeholder="no limit"
              className="h-8 w-40"
              value={draft.maxValue}
              error={draft.maxValueError}
              onChange={(event) => {
                const maxValue = event.target.value;
                setDraft((d) => ({
                  ...d,
                  maxValue,
                  maxValueError: d.maxValueError
                    ? numberFieldError(maxValue, { min: 0, optional: true })
                    : undefined,
                }));
              }}
            />
          ) : (
            <span className="text-sm">
              {row.max_value === null || row.max_value === undefined
                ? 'No limit'
                : row.unit
                  ? `${row.max_value} ${row.unit}`
                  : row.max_value}
            </span>
          )}
        </Field>
      )}

      <Field label="What this means to the chatbot">
        {mode === 'edit' && draft ? (
          <div className="flex flex-col gap-1">
            <Textarea
              value={draft.parserHint}
              onChange={(event) => setDraft((d) => ({ ...d, parserHint: event.target.value }))}
              aria-label="What this means to the chatbot"
              maxLength={HINT_MAX}
              rows={2}
            />
            <span className="self-end text-xs text-muted-foreground">
              {draft.parserHint.length} / {HINT_MAX}
            </span>
          </div>
        ) : (
          <span className="text-sm">{row.parser_hint || '-'}</span>
        )}
      </Field>

      <Field label="Other names for this specification">
        <WordsDataGrid
          words={otherNames}
          mode={mode}
          specKey={row.spec_key}
          value={SELF_KEY}
          savedWords={row.synonyms?.[SELF_KEY] ?? EMPTY_WORDS}
          alsoOn={row.also_on}
          error={wordError?.value === SELF_KEY ? wordError : null}
          emptyMessage="No other names yet."
          addPlaceholder="e.g. oz"
          onAdd={(word) =>
            setDraft((d) => ({
              ...d,
              words: { ...d.words, [SELF_KEY]: dedupe([...(d.words[SELF_KEY] ?? []), word]) },
            }))
          }
          // S-5: a built-in word renamed away is taken away too, or the next Save
          // sends a suppression list without it and it comes back.
          onRename={(oldWord, newWord) =>
            setDraft((d) => {
              const builtIn = seedWordsFor(row, SELF_KEY).includes(oldWord);
              return {
                ...d,
                words: {
                  ...d.words,
                  [SELF_KEY]: dedupe(
                    (d.words[SELF_KEY] ?? []).map((w) => (w === oldWord ? newWord : w)),
                  ),
                },
                droppedWords: builtIn
                  ? {
                      ...d.droppedWords,
                      [SELF_KEY]: dedupe([...(d.droppedWords[SELF_KEY] ?? []), oldWord]),
                    }
                  : d.droppedWords,
              };
            })
          }
          // The server already dropped it (`spec_word.remove`, fix round 1) - this
          // only keeps the OPEN draft in step, so a Save right after does not
          // resurrect it by sending a stale `suppressed_synonyms` that omits it.
          onRemoved={(word) =>
            setDraft((d) => {
              const current = d.words[SELF_KEY] ?? [];
              const seedWords = seedWordsFor(row, SELF_KEY);
              const droppedForSelf = seedWords.includes(word)
                ? dedupe([...(d.droppedWords[SELF_KEY] ?? []), word])
                : (d.droppedWords[SELF_KEY] ?? []);
              return {
                ...d,
                words: { ...d.words, [SELF_KEY]: current.filter((w) => w !== word) },
                droppedWords: { ...d.droppedWords, [SELF_KEY]: droppedForSelf },
              };
            })
          }
        />
      </Field>
    </div>
  );
}

export default HeaderTab;

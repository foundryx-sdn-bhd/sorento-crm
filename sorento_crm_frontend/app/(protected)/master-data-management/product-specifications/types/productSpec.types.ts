import type { VerificationBlock } from '../../spec-verification/types/specVerification.types';

export interface SpecValue {
  value: string | number | boolean;
  unit?: string;
}

export interface SpecProvenance {
  /** derived | rule | human. `human` survives re-derivation. */
  source: string;
  confidence: number;
  /** The exact substring the value was read from. */
  evidence: string;
}

/** One `human_override_conflict` on a product's own Specifications tab. */
export interface SpecException {
  id: string;
  /** Absent when the exception is already scoped to one product. */
  product_code?: string;
  spec_key: string;
  /** shape_mismatch | column_conflict | implausible_dimension */
  reason: string;
  proposed: Record<string, unknown> | null;
  stored: Record<string, unknown> | null;
}

/**
 * Why a product has no derived specs. Reported instead of a blank, because the four
 * silences have four different fixes and only one of them lives in the ranker.
 */
export type SpecDiagnosisReason =
  | 'eligible'
  | 'not_yet_derived'
  | 'class_not_enabled'
  | 'category_non_searchable'
  | 'code_unparsed'
  | 'no_category';

export interface ProductSpecDetail {
  product_id: string;
  product_code: string;
  category_code: string | null;
  /** True only when a spec row exists - i.e. the chatbot can actually find this. */
  searchable: boolean;
  diagnosis: {
    reason: SpecDiagnosisReason;
    class_label: string | null;
    brand_hint: string | null;
    suffix: string | null;
  };
  spec: {
    values: Record<string, SpecValue>;
    provenance: Record<string, SpecProvenance>;
    rendered_text: string | null;
    status: string;
    derived_at: string | null;
  } | null;
  exceptions: SpecException[];
  /** The description the derivation read. Shown so a wrong value can be traced. */
  source_text: string;
  /**
   * Who vouched for this code and when, derived server-side (AC-D.2). Carried on this
   * response rather than fetched separately, so the tab costs no second round trip and
   * both company copies of a code read the same badge (AC-D.14).
   */
  verification: VerificationBlock;
  /**
   * The hash of the values on screen. Echoed back on verify, so a code whose values
   * moved while it was being reviewed is refused rather than silently stamped (AC-D.4).
   */
  values_hash: string;
}

export interface SpecCandidate {
  product_id: string;
  product_code: string;
  summary: string;
  class: string | null;
  matched_specs: string[];
  score: number;
  is_discontinued: boolean;
}

/** A spec the customer asked for that nothing offered can satisfy. */
export interface UnmetSpec {
  key: string;
  value: string | number | boolean;
}

export interface SpecPreviewResult {
  candidates: SpecCandidate[];
  /** True when nothing cleared the floor: the bot would ask for a code instead. */
  floor_missed: boolean;
  top_score: number;
  floor: number;
  /** Null when the caller passed structured specs rather than a raw phrase. */
  understanding: SpecUnderstanding | null;
  unmet: UnmetSpec[];
}

/**
 * A rule's five kinds (plan D5, contract section 1), the only shape a rule may take.
 * `compileBuilder` in `lib/ruleSentence.ts` mirrors the server's `compile_builder`
 * exactly (contract 2, 2.1); nobody types or sees the compiled pattern.
 */
export type SpecRuleKind = 'words' | 'number' | 'size' | 'code' | 'product';

/** Where a rule reads from. Absent means the kind's own default (contract 1.1). */
export type SpecRuleLookIn = 'any' | 'description' | 'flyer' | 'name';

export type SpecRuleWrittenIn = 'centimetres' | 'metres' | null;

export type SpecRuleSizePick = 1 | 2 | 3 | 4 | 'L' | 'W' | 'H';

export type SpecRuleCodeMatch = 'contains' | 'starts_with' | 'ends_with';

export type SpecRuleProductFact = 'class' | 'name' | 'length' | 'width' | 'height';

/** "Only when Shape is not Round, Square" (contract 1.1). One condition per rule. */
export interface SpecRuleOnlyWhen {
  /** Another spec's key. Never `brand`. */
  spec: string;
  /** true = only when the spec holds one of `values`; false = except when it does. */
  is: boolean;
  values: string[];
}

interface SpecRuleBuilderBase {
  only_when?: SpecRuleOnlyWhen | null;
}

export interface SpecRuleWordsBuilder extends SpecRuleBuilderBase {
  kind: 'words';
  look_in?: SpecRuleLookIn;
  words: string[];
  at_end?: boolean;
  skip_after?: string[];
  /** The spec's choice key for a List spec, `true` for Yes or no, a number for Number. */
  value: string | number | boolean;
}

export interface SpecRuleNumberBuilder extends SpecRuleBuilderBase {
  kind: 'number';
  look_in?: SpecRuleLookIn;
  /** At least one of `before` / `after`. */
  before?: string[];
  after?: string[];
  written_in?: SpecRuleWrittenIn;
  ignore_below?: number | null;
  skip_after?: string[];
}

export interface SpecRuleSizeBuilder extends SpecRuleBuilderBase {
  kind: 'size';
  look_in?: SpecRuleLookIn;
  pick: SpecRuleSizePick;
}

export interface SpecRuleCodeBuilder extends SpecRuleBuilderBase {
  kind: 'code';
  code_match: SpecRuleCodeMatch;
  texts: string[];
  value: string | number | boolean;
}

export interface SpecRuleProductBuilder extends SpecRuleBuilderBase {
  kind: 'product';
  fact: SpecRuleProductFact;
}

/** A rule is its builder and nothing else (contract section 1). */
export type SpecRuleBuilder =
  | SpecRuleWordsBuilder
  | SpecRuleNumberBuilder
  | SpecRuleSizeBuilder
  | SpecRuleCodeBuilder
  | SpecRuleProductBuilder;

/** What `compileBuilder` returns (contract 2.1) - what the engine and Try it run. */
export interface SpecCompiledRule {
  kind: SpecRuleKind;
  /** `look_in` (or null when absent), `'code'` for code, `'product'` for product. */
  scope: SpecRuleLookIn | 'code' | 'product' | null;
  /** Null for code and product. */
  pattern: string | null;
  /** 1 for number, null otherwise. */
  capture: number | null;
  skip: string | null;
  scale: number | null;
  min: number | null;
  pick: SpecRuleSizePick | null;
  code_match: SpecRuleCodeMatch | null;
  texts: string[] | null;
  fact: SpecRuleProductFact | null;
}

/** One way of reading a value out of a product's text (contract section 1). */
export interface SpecDerivationRule {
  builder: SpecRuleBuilder;
  /** `compileBuilder(builder)`'s `pattern`, sent alongside the builder so the server
   *  can refuse a save where its own compile disagrees (contract section 3). */
  pattern?: string | null;
  /** Shipped rules carry `_seed: true` internally; never rendered. */
  _seed?: boolean;
  /**
   * Browser-only identity, so dragging a rule moves THAT RULE rather than that
   * position. Not persisted: the API builds each stored rule from `builder` alone.
   */
  _uid?: string;
}

/** One row's try-it read, aligned to `rules` by index. */
export interface SpecTryRuleRead {
  index: number;
  value: string | number | boolean | null;
  /** The exact text the value was read from, or null when nothing matched. */
  evidence: string | null;
}

/** What trying the draft rules against one product or one pasted text answers. */
export interface SpecTryResult {
  description: string;
  reads: SpecTryRuleRead[];
  /** The first row with a value, i.e. the one the engine would keep. Null when none matched. */
  winner_index: number | null;
}

/** One row of the preview's before/after sample. "Before" is what the rules live
 *  today read, not the stored value (fix round 4). */
export interface SpecPreviewSampleRow {
  code: string;
  name?: string | null;
  before: string | number | boolean | null;
  after: string | number | boolean | null;
}

/** What `GET .../preview/{jobId}` answers: still running, done, or the job itself
 *  threw (the derivation over one product raised something `derive()` does not turn
 *  into a flag). */
export interface SpecPreviewJobResult {
  status: 'pending' | 'done' | 'failed';
  changed?: number;
  now_set?: number;
  no_longer_set?: number;
  unchanged?: number;
  /** Products whose stored value already differs from what today's rules read. A
   *  save re-reads them too, so it can update more products than the counts say. */
  drift?: number;
  sample?: SpecPreviewSampleRow[];
  error?: string;
}

export interface SpecRegistryKey {
  /** The row's id: the History tab reads its audit trail with it. Never shown. */
  id: string;
  spec_key: string;
  label: string;
  data_type: string;
  unit: string | null;
  allowed_values: string[];
  /**
   * Values the catalog holds but nobody searches for - the placeholder brands OTHERS
   * and NO LOGO, which record the absence of a brand. Excluded ones are hidden from
   * the understanding model, which was otherwise filing every word it could not place
   * under one.
   */
  excluded_values: string[];
  /** Values staff added to a shipped key - the removable half of `allowed_values`. */
  user_values: string[];
  /** Shipped values this business has taken away. Already subtracted from `allowed_values`. */
  suppressed_values: string[];
  /**
   * A standing preference for particular values of this key ({ SORENTO: 1.5 }) -
   * applied to any product carrying the value, except when the customer named the key
   * themselves.
   */
  value_weights: Record<string, number>;
  /** How this key is read out of a product's text. First match wins, so order matters. */
  derivation_rules: SpecDerivationRule[];
  /**
   * The rules that ACTUALLY run. Equal to `derivation_rules` once a key has been
   * edited; before that it is the set that ships in code, which derivation falls back
   * to - so a key with an empty column is not a key with no rules.
   */
  effective_rules: SpecDerivationRule[];
  /** Seed + user words, already merged. What a customer can actually say. */
  synonyms: Record<string, string[]>;
  applies_when: Record<string, string[]>;
  /**
   * Always `'rules'` now (#425): brand and the dimension columns are rows in the
   * list too - "From the product's brand field", "From the product's
   * `dimensions_length` column" - so there is no longer a second way a key can be
   * read, and no second value this ever carries.
   */
  read_from: 'rules';
  rank_weight: number | null;
  measured_coverage: number | null;
  /**
   * `seed` ships with the product and is repaired on every deploy, so its values
   * cannot be edited here - only extended. `user` keys are owned by whoever made them.
   */
  source: 'seed' | 'user';
  /** Staff-added phrasings only, i.e. the editable half of `synonyms`. */
  user_synonyms: Record<string, string[]>;
  /** Shipped words this business has taken away. Already subtracted from `synonyms`. */
  suppressed_synonyms: Record<string, string[]>;
  match_tolerance: number;
  match_decay: number;
  is_active: boolean;
  /** A number above this is dropped as implausible rather than stored. Null/absent
   *  means no cap. Seeded 5000 on mm keys; editable per key. */
  max_value?: number | null;
  /**
   * How each value reads on screen, when the raw slug does not already read as one
   * (#423 folded into this redesign). `{ pp: "PP" }` overrides `readableValue('pp')`,
   * which would otherwise render "Pp". Staff-owned on seed AND user rows alike -
   * editable regardless of who owns the value itself (D4, AC-D.3).
   *
   * Optional because an older test fixture may not carry it - every reader treats an
   * absent dict as `{}`.
   */
  value_labels?: Record<string, string>;
  /** What this field means to the chatbot: one line the parser sees. Null when unset. */
  parser_hint: string | null;
  /** True once staff edited the Name: the seed repair leaves it alone. */
  label_owned: boolean;
  /** word -> the other fields' labels that also own it. Words with no overlap are absent. */
  also_on?: Record<string, string[]>;
}

/** One tunable number in the ranker's scoring. */
export interface SpecSearchPolicyRow {
  policy_key: string;
  label: string;
  help_text: string;
  value: number;
  default_value: number;
}

/**
 * `PATCH /spec-registry/{key}` (contract section 4): the serialised key, plus how
 * many products the save re-read straight away through `rederive_codes` (D10) -
 * 0 when the save changed neither `derivation_rules`, `applies_when` nor
 * `max_value`. The toast reads this, never a re-read prompt (owner ruling, 27 Sep
 * 2026): "Saved. N products updated."
 */
export interface SpecRegistryKeyUpdateResult extends SpecRegistryKey {
  products_updated: number;
}

/** What the phrase was understood to mean, and whether a model was involved. */
export interface SpecUnderstanding {
  source: 'semantic' | 'deterministic';
  model: string | null;
  elapsed_ms: number | null;
  specs: { key: string; value: string | number | boolean }[];
  /** What the customer RULED OUT. Products known to hold one are removed entirely. */
  exclusions: { key: string; value: string | number | boolean }[];
  free_terms: string[];
  notes: string;
}

/**
 * One product carrying every state the table has to render, for the Phase 1 prototype
 * and for the component tests that outlive it.
 *
 * Every case named in the plan is here on purpose, because each of them is a shape the
 * real catalogue holds and each was got wrong at least once by something upstream:
 *
 * - a derived value, a flyer value, a category value and an authored one
 * - a value PROMOTED from the flyer, which is authored now and carries
 *    `migrated_from: 'flyer'` so a person can tell why something they never typed
 *    reads "Set by hand"
 * - a removed key, which exists ONLY in `provenance` and must produce NO row -
 *    the tombstone is the server's, so re-derivation will not refill it
 * - an open `human_override_conflict`
 * - an enum key, a numeric key with a unit, a boolean, and a free-text key,
 *    which are the four editors
 * - a stored key the registry no longer defines, which has no data type, no
 *    vocabulary and no unit to write it back with
 */
import type { SpecKeyDefinition } from '../types';
import type { SpecConflict, StoredSpecProvenance, StoredSpecValue } from '../specTableModel';

export const MOCK_REGISTRY: SpecKeyDefinition[] = [
  {
    spec_key: 'finish',
    label: 'Finish or colour',
    data_type: 'text',
    unit: null,
    allowed_values: ['chrome', 'matt_black', 'brushed_nickel', 'white'],
    synonyms: { matt_black: ['matte black', 'black matt'], chrome: ['chromed', 'polished chrome'] },
  },
  {
    spec_key: 'material',
    label: 'Material',
    data_type: 'text',
    unit: null,
    allowed_values: ['stainless_steel', 'ceramic', 'brass'],
    synonyms: { stainless_steel: ['ss', 'inox'] },
  },
  {
    spec_key: 'dim_height',
    label: 'Height',
    data_type: 'numeric',
    unit: 'mm',
    allowed_values: [],
  },
  {
    spec_key: 'dim_length',
    label: 'Length',
    data_type: 'numeric',
    unit: 'mm',
    allowed_values: [],
  },
  {
    spec_key: 'is_smart',
    label: 'Intelligent / smart',
    data_type: 'boolean',
    unit: null,
    allowed_values: [],
  },
  {
    spec_key: 'flush_type',
    label: 'Flush type',
    data_type: 'text',
    unit: null,
    allowed_values: ['washdown', 'siphonic'],
  },
  {
    spec_key: 'shape',
    label: 'Shape',
    data_type: 'text',
    unit: null,
    allowed_values: ['round', 'square', 'rectangular'],
  },
  {
    spec_key: 'class',
    label: 'Product class',
    data_type: 'text',
    unit: null,
    allowed_values: ['water_closet', 'kitchen_sink', 'basin'],
  },
  {
    spec_key: 'model_note',
    label: 'Model note',
    data_type: 'text',
    unit: null,
    // No vocabulary at all: this is the free-text editor.
    allowed_values: [],
  },
  {
    spec_key: 'has_overflow',
    label: 'Overflow',
    data_type: 'boolean',
    unit: null,
    allowed_values: [],
  },
  {
    spec_key: 'seat_material',
    label: 'Seat cover material',
    data_type: 'text',
    unit: null,
    allowed_values: ['pp', 'uf', 'duroplast'],
  },
];

export const MOCK_VALUES: Record<string, StoredSpecValue> = {
  material: { value: 'stainless_steel' },
  finish: { value: 'matt_black' },
  dim_length: { value: 680, unit: 'mm' },
  dim_height: { value: 770, unit: 'mm' },
  is_smart: { value: true },
  flush_type: { value: 'washdown' },
  shape: { value: 'square' },
  class: { value: 'water_closet' },
  model_note: { value: 'Second batch, revised trap' },
  // Stored, and no longer in MOCK_REGISTRY: the registry dropped the key while
  // 22,805 rows kept carrying it.
  gloss_level: { value: 'semi_gloss' },
};

export const MOCK_PROVENANCE: Record<string, StoredSpecProvenance> = {
  material: { source: 'derived', confidence: 0.9, evidence: 'S/STEEL KITCHEN SINK' },
  finish: {
    source: 'human',
    confidence: 1,
    evidence: 'set by person26@example.com',
  },
  dim_length: {
    source: 'flyer',
    confidence: 0.8,
    evidence: 'Washdown With Rimless. D: L680xW375xH770mm. *PP Seat Cover',
  },
  dim_height: {
    source: 'flyer',
    confidence: 0.8,
    evidence: 'Washdown With Rimless. D: L680xW375xH770mm. *PP Seat Cover',
  },
  is_smart: { source: 'derived', confidence: 0.7, evidence: 'AUTO INDUCTION TOILET' },
  // Promoted by the flyer migration: authored now, and the evidence keeps the words
  // it was read from behind the "flyer: " prefix the migration wrote.
  flush_type: {
    source: 'human',
    confidence: 0.8,
    evidence: 'flyer: Washdown With Rimless',
    migrated_from: 'flyer',
  },
  // Authored, and derivation now reads something else: the conflict below.
  shape: { source: 'human', confidence: 1, evidence: 'set by person26@example.com' },
  class: { source: 'category', confidence: 0.5, evidence: 'SRT-WC' },
  model_note: { source: 'human', confidence: 1, evidence: 'set by person26@example.com' },
  gloss_level: { source: 'derived', confidence: 0.6, evidence: 'SEMI GLOSS FINISH' },
  // The tombstone. No entry in MOCK_VALUES at all - this row exists only here.
  has_overflow: {
    source: 'human',
    confidence: 1,
    evidence: 'set by person26@example.com',
    absent: true,
  },
};

export const MOCK_EXCEPTIONS: SpecConflict[] = [
  {
    spec_key: 'shape',
    reason: 'human_override_conflict',
    proposed: { value: 'round' },
  },
];

/** A product with nothing read from it yet, for the empty state. */
export const MOCK_EMPTY = {
  values: {} as Record<string, StoredSpecValue>,
  provenance: {} as Record<string, StoredSpecProvenance>,
  exceptions: [] as SpecConflict[],
};

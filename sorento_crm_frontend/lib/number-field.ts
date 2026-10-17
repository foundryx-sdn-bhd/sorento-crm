/**
 * Shared rules for operator-typed numbers. A field may be empty or partial while
 * the operator types; only the final value is checked, on save or confirm.
 */
export function parseNumberDraft(raw: string | number | null | undefined): number | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null;
  const trimmed = raw.trim();
  if (trimmed === '' || trimmed === '-' || trimmed === '.') return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

export type NumberFieldRules = {
  min?: number;
  /** With `min`: the value must be above it, not equal ("above 0"). */
  minExclusive?: boolean;
  max?: number;
  integer?: boolean;
  optional?: boolean;
};

export function numberFieldError(
  raw: string | number | null | undefined,
  rules: NumberFieldRules = {},
): string | undefined {
  const { min, max, integer, optional, minExclusive } = rules;
  const value = parseNumberDraft(raw);
  if (value === null) {
    const blank = raw === null || raw === undefined || (typeof raw === 'string' && raw.trim() === '');
    if (blank && optional) return undefined;
  } else if (
    (min === undefined || (minExclusive ? value > min : value >= min)) &&
    (max === undefined || value <= max) &&
    (!integer || Number.isInteger(value))
  ) {
    return undefined;
  }
  const noun = integer ? 'a whole number' : 'a number';
  let sentence: string;
  if (min !== undefined && minExclusive) {
    sentence = `Enter ${noun} above ${min}`;
    if (max !== undefined) sentence += ` and up to ${max}`;
  } else if (min !== undefined && max !== undefined) sentence = `Enter ${noun} between ${min} and ${max}`;
  else if (min !== undefined) sentence = `Enter ${noun} of at least ${min}`;
  else if (max !== undefined) sentence = `Enter ${noun} up to ${max}`;
  else sentence = `Enter ${noun}`;
  return optional ? `${sentence}, or leave it blank.` : `${sentence}.`;
}

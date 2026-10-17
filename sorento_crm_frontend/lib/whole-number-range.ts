import { numberFieldError } from '@/lib/number-field';

/**
 * One validator for every operator-typed whole number the media settings surfaces
 * hold to the backend's bounds (system settings and the per-contact overrides).
 * Two copies drifted their copy the first time; one helper keeps the sentence and
 * the rule identical everywhere the same bound is enforced.
 */
export function wholeNumberRangeError(
  raw: string,
  min: number,
  max: number,
  options: { allowBlank?: boolean } = {},
): string | undefined {
  const trimmed = raw.trim();
  if (trimmed === '' && options.allowBlank) return undefined;
  // NaN is never valid, so this returns the shared sentence for the bounds.
  const message =
    numberFieldError(NaN, { min, max, integer: true, optional: options.allowBlank }) ?? '';
  if (!/^\d+$/.test(trimmed)) return message;
  const value = Number(trimmed);
  return value < min || value > max ? message : undefined;
}

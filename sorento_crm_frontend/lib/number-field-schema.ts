import { z } from 'zod';
import { numberFieldError, parseNumberDraft, type NumberFieldRules } from '@/lib/number-field';

// A required number the form holds as typed text: empty is refused here (one sentence on
// the field), never coerced to 0.
// Typed as number in and out so the form's field types stay what they were.
export function requiredNumber(rules: NumberFieldRules) {
  const schema = z
    .unknown()
    .superRefine((value, ctx) => {
      const message = numberFieldError(value as string | number | null | undefined, rules);
      if (message) ctx.addIssue({ code: z.ZodIssueCode.custom, message });
    })
    .transform((value) => parseNumberDraft(value as string | number | null | undefined) as number);
  return schema as unknown as z.ZodType<number, z.ZodTypeDef, number>;
}

// An optional number: blank, null and undefined pass through as they came; anything typed
// is checked with the same sentence, and bad input (NaN) reads as a sentence too.
export function optionalNumber(rules: NumberFieldRules) {
  const schema = z
    .unknown()
    .superRefine((value, ctx) => {
      const message = numberFieldError(value as string | number | null | undefined, { ...rules, optional: true });
      if (message) ctx.addIssue({ code: z.ZodIssueCode.custom, message });
    })
    .transform((value) => {
      if (value === null || value === undefined) return value;
      return parseNumberDraft(value as string | number);
    });
  return schema as unknown as z.ZodType<number | null | undefined, z.ZodTypeDef, number | null | undefined>;
}

// Same as optionalNumber for a form field typed number | null (blank is null, never undefined).
export function nullableNumber(rules: NumberFieldRules) {
  return optionalNumber(rules) as unknown as z.ZodType<number | null, z.ZodTypeDef, number | null>;
}

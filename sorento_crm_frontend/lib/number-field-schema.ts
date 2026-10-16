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

import { z } from 'zod';
import { requiredNumber } from '@/lib/number-field-schema';

export const PromotionTypeSchema = z.object({
  type_code: z
    .string()
    .min(1, 'Code is required')
    .max(50, 'Code must be 50 characters or fewer')
    .regex(/^[a-zA-Z0-9_-]+$/, 'Use letters, numbers, hyphen or underscore only'),
  type_name: z.string().min(1, 'Name is required').max(150, 'Name must be 150 characters or fewer'),
  description: z.string().optional(),
  show_expired: z.boolean(),
  expired_valid_until_year_end: z.boolean(),
  // Blank means no age cap.
  expired_max_age_days: requiredNumber({ min: 0, integer: true, optional: true }) as unknown as z.ZodType<
    number | null | undefined,
    z.ZodTypeDef,
    number | null | undefined
  >,
  markers_text: z.string().optional(),
  match_priority: requiredNumber({ min: 0, integer: true }),
  is_default: z.boolean(),
  sort_order: requiredNumber({ min: 0, integer: true }),
});

export type PromotionTypeSchemaType = z.infer<typeof PromotionTypeSchema>;

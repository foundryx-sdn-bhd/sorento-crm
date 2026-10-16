import { z } from 'zod';
import { optionalNumber, requiredNumber } from '@/lib/number-field-schema';

export const UOMSchema = z.object({
  uom_code: z
    .string()
    .min(1, { message: 'UOM code is required.' })
    .max(50, { message: 'UOM code must not exceed 50 characters.' })
    .regex(/^[A-Za-z0-9_-]+$/, {
      message: 'UOM code can only contain alphanumeric characters, dashes, and underscores.',
    }),
  uom_name: z
    .string()
    .min(1, { message: 'UOM name is required.' })
    .max(150, { message: 'UOM name must not exceed 150 characters.' }),
  base_uom_id: z.string().uuid().optional().nullable(),
  conversion_factor: optionalNumber({ min: 0, minExclusive: true, max: 999999.9999 }),
  decimal_places: requiredNumber({ min: 0, max: 4, integer: true }),
  description: z.string().max(2000, { message: 'Description must not exceed 2000 characters.' }).optional().nullable(),
  is_active: z.boolean(),
});

export type UOMSchemaType = z.infer<typeof UOMSchema>;

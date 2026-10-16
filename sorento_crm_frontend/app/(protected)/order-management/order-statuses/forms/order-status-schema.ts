import { z } from 'zod';
import { requiredNumber } from '@/lib/number-field-schema';

export const OrderStatusSchema = z.object({
  status_code: z
    .string()
    .min(1, { message: 'Status code is required.' })
    .max(50, { message: 'Status code must not exceed 50 characters.' })
    .regex(/^[A-Za-z0-9_-]+$/, {
      message: 'Status code can only contain alphanumeric characters, dashes, and underscores.',
    }),
  status_name: z
    .string()
    .min(1, { message: 'Status name is required.' })
    .max(100, { message: 'Status name must not exceed 100 characters.' }),
  description: z.string().max(2000, { message: 'Description must not exceed 2000 characters.' }).optional().nullable(),
  sequence: requiredNumber({ min: 0, integer: true }),
  is_final_status: z.boolean(),
});

export type OrderStatusSchemaType = z.infer<typeof OrderStatusSchema>;

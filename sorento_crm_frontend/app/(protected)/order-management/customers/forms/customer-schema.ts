import { z } from 'zod';

export const CustomerSchema = z.object({
  customer_code: z
    .string()
    .min(1, { message: 'Customer code is required.' })
    .max(50, { message: 'Customer code must not exceed 50 characters.' })
    .regex(/^[A-Za-z0-9_-]+$/, {
      message: 'Customer code can only contain alphanumeric characters, dashes, and underscores.',
    }),
  customer_name: z
    .string()
    .min(3, { message: 'Customer name must be at least 3 characters long.' })
    .max(255, { message: 'Customer name must not exceed 255 characters.' }),
  email: z.string().email({ message: 'Invalid email address.' }).optional().nullable().or(z.literal('')),
  phone_number: z.string().max(50, { message: 'Phone number must not exceed 50 characters.' }).optional().nullable(),
  is_active: z.boolean(),
  sales_agent_id: z.string().optional().nullable(),
  account_level: z.number().int().min(1).max(9).nullable().optional(),
  customer_group_id: z.string().nullable().optional(),
});

export type CustomerSchemaType = z.infer<typeof CustomerSchema>;

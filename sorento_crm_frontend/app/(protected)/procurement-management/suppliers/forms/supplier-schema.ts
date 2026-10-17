import { z } from 'zod';
import { requiredNumber } from '@/lib/number-field-schema';

export const SupplierSchema = z.object({
  supplier_code: z
    .string()
    .min(1, { message: 'Supplier code is required.' })
    .max(100, { message: 'Supplier code must not exceed 100 characters.' })
    .regex(/^[A-Za-z0-9_-]+$/, {
      message: 'Supplier code can only contain alphanumeric characters, dashes, and underscores.',
    }),
  supplier_name: z
    .string()
    .min(3, { message: 'Supplier name must be at least 3 characters long.' })
    .max(255, { message: 'Supplier name must not exceed 255 characters.' }),
  contact_name: z.string().max(255, { message: 'Contact name must not exceed 255 characters.' }).optional().nullable(),
  email: z.string().email({ message: 'Invalid email address.' }).optional().nullable().or(z.literal('')),
  phone_number: z.string().max(50, { message: 'Phone number must not exceed 50 characters.' }).optional().nullable(),
  website: z.string().url({ message: 'Invalid website URL.' }).optional().nullable().or(z.literal('')),
  address_line1: z.string().max(255, { message: 'Address line 1 must not exceed 255 characters.' }).optional().nullable(),
  address_line2: z.string().max(255, { message: 'Address line 2 must not exceed 255 characters.' }).optional().nullable(),
  city: z.string().max(100, { message: 'City must not exceed 100 characters.' }).optional().nullable(),
  state: z.string().max(100, { message: 'State must not exceed 100 characters.' }).optional().nullable(),
  postal_code: z.string().max(20, { message: 'Postal code must not exceed 20 characters.' }).optional().nullable(),
  country_id: z.string().uuid().optional().nullable(),
  payment_terms_days: requiredNumber({ min: 0, max: 365, integer: true }),
  is_active: z.boolean(),
});

export type SupplierSchemaType = z.infer<typeof SupplierSchema>;

import { z } from 'zod';
import { requiredNumber } from '@/lib/number-field-schema';

export const ProductSchema = z.object({
  // Tab 1: Basic Information
  product_code: z
    .string()
    .min(1, { message: 'Product code is required.' })
    .max(100, { message: 'Product code must not exceed 100 characters.' })
    .regex(/^[A-Za-z0-9 _\-./()+#*]+$/, {
      message:
        'Product code may contain letters, numbers, spaces, and the symbols - _ . / ( ) + # *.',
    }),
  product_name: z
    .string()
    .min(3, { message: 'Product name must be at least 3 characters long.' })
    .max(255, { message: 'Product name must not exceed 255 characters.' }),
  description: z.string().max(2000, { message: 'Description must not exceed 2000 characters.' }).optional().nullable(),
  category_id: z.string().uuid({ message: 'Category is required.' }),
  brand_id: z.string().uuid().optional().nullable(),
  barcode: z
    .string()
    .max(100, { message: 'Barcode must not exceed 100 characters.' })
    .optional()
    .nullable(),
  item_type: z.enum(['product', 'bundle', 'service', 'other']).optional().nullable(),
  is_active: z.boolean(),
  is_searchable: z.boolean(),
  // S5 (reorder-feedback-9sep.md, 9 Sep 2026): the buyer's own opt-out of reorder
  // planning, alongside the other two status switches.
  exclude_from_planning: z.boolean(),

  // Tab 2: Pricing (coerce strings from API/inputs to numbers)
  list_price: requiredNumber({ min: 0, max: 999999999999.99 }),
  cost_price: z
    .union([z.coerce.number(), z.null()])
    .optional()
    .nullable()
    .refine((v) => v === null || v === undefined || (typeof v === 'number' && v >= 0 && v <= 999999999999.99), {
      message: 'Cost price must be between 0 and 999999999999.99.',
    }),
  invoice_price: z
    .union([z.coerce.number(), z.null()])
    .optional()
    .nullable()
    .refine((v) => v === null || v === undefined || (typeof v === 'number' && v >= 0 && v <= 999999999999.99), {
      message: 'Invoice price must be between 0 and 999999999999.99.',
    }),

  // Tab 3: Specifications
  weight: z
    .union([z.coerce.number(), z.null()])
    .optional()
    .nullable()
    .refine((v) => v === null || v === undefined || (typeof v === 'number' && v > 0), {
      message: 'Weight must be a positive number.',
    }),
  dimensions_length: z
    .union([z.coerce.number(), z.null()])
    .optional()
    .nullable()
    .refine((v) => v === null || v === undefined || (typeof v === 'number' && v > 0), {
      message: 'Length must be a positive number.',
    }),
  dimensions_width: z
    .union([z.coerce.number(), z.null()])
    .optional()
    .nullable()
    .refine((v) => v === null || v === undefined || (typeof v === 'number' && v > 0), {
      message: 'Width must be a positive number.',
    }),
  dimensions_height: z
    .union([z.coerce.number(), z.null()])
    .optional()
    .nullable()
    .refine((v) => v === null || v === undefined || (typeof v === 'number' && v > 0), {
      message: 'Height must be a positive number.',
    }),
  warranty_months: z
    .union([z.coerce.number().int(), z.null()])
    .optional()
    .nullable()
    .refine((v) => v === null || v === undefined || (typeof v === 'number' && v >= 0), {
      message: 'Warranty months cannot be negative.',
    }),
  has_serial_tracking: z.boolean(),
  has_batch_tracking: z.boolean(),
  reorder_level: requiredNumber({ min: 0, integer: true }),
  reorder_quantity: requiredNumber({ min: 0, integer: true }),
  // Chatbot stock ask v2 (PLAN-chatbot-stock-ask-v2-24sep.md S1): X and Y,
  // beside the reorder fields since both drive the same stock-ask journey.
  // .nullable() checks for null BEFORE z.coerce.number() runs, so a cleared
  // input (which ProductForm's onChange already turns into null, never '')
  // stays null instead of coercing to 0 (R2: unset must stay unset so the
  // category can inherit).
  chatbot_max_qty: z.coerce.number().int().min(0, { message: 'Max quantity cannot be negative.' }).nullable(),
  chatbot_eta_offset_days: z.coerce.number().int().min(0, { message: 'ETA offset cannot be negative.' }).nullable(),

  // Tab 4: Unit of Measure
  base_uom_id: z.string().uuid({ message: 'Base unit of measure is required.' }),
});

export type ProductSchemaType = z.infer<typeof ProductSchema>;

import { z } from 'zod';
import { nullableNumber, requiredNumber } from '@/lib/number-field-schema';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';

// An optional number: blank is "not set" (null), never 0. A typed figure outside the range
// keeps `message`; anything else unreadable gets the shared sentence.
function optionalNumber(check: (n: number) => boolean, message: string, integer = false) {
  const schema = z
    .unknown()
    .superRefine((value, ctx) => {
      const parsed = parseNumberDraft(value as string | number | null | undefined);
      if (parsed !== null && !check(parsed)) {
        ctx.addIssue({ code: z.ZodIssueCode.custom, message });
        return;
      }
      const sentence = numberFieldError(value as string | number | null | undefined, {
        integer,
        optional: true,
      });
      if (sentence) ctx.addIssue({ code: z.ZodIssueCode.custom, message: sentence });
    })
    .transform((value) => parseNumberDraft(value as string | number | null | undefined));
  return schema as unknown as z.ZodType<number | null | undefined, z.ZodTypeDef, number | null | undefined>;
}

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
  cost_price: optionalNumber((n) => n >= 0 && n <= 999999999999.99, 'Cost price must be between 0 and 999999999999.99.'),
  invoice_price: optionalNumber((n) => n >= 0 && n <= 999999999999.99, 'Invoice price must be between 0 and 999999999999.99.'),

  // Tab 3: Specifications
  weight: optionalNumber((n) => n > 0, 'Weight must be a positive number.'),
  dimensions_length: optionalNumber((n) => n > 0, 'Length must be a positive number.'),
  dimensions_width: optionalNumber((n) => n > 0, 'Width must be a positive number.'),
  dimensions_height: optionalNumber((n) => n > 0, 'Height must be a positive number.'),
  warranty_months: optionalNumber((n) => n >= 0, 'Warranty months cannot be negative.', true),
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
  chatbot_max_qty: nullableNumber({ min: 0, integer: true }),
  chatbot_eta_offset_days: nullableNumber({ min: 0, integer: true }),

  // Tab 4: Unit of Measure
  base_uom_id: z.string().uuid({ message: 'Base unit of measure is required.' }),
});

export type ProductSchemaType = z.infer<typeof ProductSchema>;

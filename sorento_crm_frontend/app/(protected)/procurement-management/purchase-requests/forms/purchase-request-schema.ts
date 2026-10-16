import { z } from 'zod';
import { numberFieldError } from '@/lib/number-field';

export const QUANTITY_RULES = { min: 0, minExclusive: true } as const;

/** Accepts number or string (e.g. from API or input) so "Expected number, received string" is avoided. */
const quantitySchema = z
  .union([z.number(), z.nan(), z.string()])
  .optional()
  .nullable();

/** Total Project Value: strict numeric, must fit Numeric(15,2) (abs < 10^13).
 *  Mirrors the backend validators.validate_project_value guard rail. */
const projectValueSchema = z
  .union([z.number(), z.string()])
  .optional()
  .nullable()
  .refine((v) => v == null || v === '' || !Number.isNaN(Number(v)), {
    message: 'Total project value must be a number.',
  })
  .refine((v) => v == null || v === '' || Number.isNaN(Number(v)) || Math.abs(Number(v)) < 1e13, {
    message: 'Total project value is too large (max 9,999,999,999,999.99).',
  });

const lineSchema = z.object({
  item_code: z.string().max(500).optional().nullable(),
  quantity: quantitySchema,
  remark: z.string().max(2000).optional().nullable(),
  unit_price: quantitySchema,
  total: quantitySchema,
});

export const PurchaseRequestSchema = z.object({
  request_type: z.enum(['purchase_request', 'sponsorship_form']),
  request_number: z.string().max(50).optional().nullable(),
  request_date: z.string().optional().nullable(),
  customer_name: z.string().max(500).optional().nullable(),
  // Optional by design: many forms have no named site contact.
  pic: z.string().max(500).optional().nullable(),
  project_title: z.string().max(500).optional().nullable(),
  // AC-L3. The portal already offers this picker to contacts (AC-F4); this is the office-side
  // half, so a form typed in by CS can carry the same reportable link.
  project_id: z.string().uuid().optional().nullable(),
  customer_id: z.string().optional().nullable(),
  purpose: z.string().max(500).optional().nullable(),
  delivery_address: z.string().max(2000).optional().nullable(),
  total_project_value: projectValueSchema,
  total_project_value_text: z.string().max(2000).optional().nullable(),
  // R2: sales type (project / cash_sales), lookup-bound. Required for purchase
  // requests (see superRefine below); not applicable to sponsorship forms.
  sales_type: z.string().max(50).optional().nullable(),
  sponsor_subject: z.string().max(500).optional().nullable(),
  sponsor_subject_other: z.string().max(2000).optional().nullable(),
  expected_delivery_date: z.string().optional().nullable(),
  expected_po_date: z.string().optional().nullable(),
  expected_po_date_text: z.string().max(500).optional().nullable(),
  requested_by: z.string().max(255).optional().nullable(),
  requested_by_contact_id: z.string().max(255).optional().nullable(),
  requested_at: z.string().optional().nullable(),
  contact_id: z.string().max(500).optional().nullable(),
  space_id: z.string().max(500).optional().nullable(),
  products: z.array(lineSchema),
});

/** A value cleared back to '' (e.g. quantity typed then erased) is not content -
 *  matches the portal twin's own `lineHasContent` (`SubmissionForm.tsx`), which trims
 *  strings rather than treating a non-null empty string as "has something in it". */
function hasValue(v: number | string | null | undefined): boolean {
  if (v == null) return false;
  return typeof v === 'string' ? v.trim().length > 0 : true;
}

/** A line the user has actually touched - mirrors the portal's own
 *  `cleanLineItems` keep-predicate (`line-items.ts`), so a blank filler row from
 *  "Add item" never gets refused for a unit price it was never asked to carry. */
export function lineHasContent(line: {
  item_code?: string | null;
  quantity?: number | string | null;
  unit_price?: number | string | null;
  total?: number | string | null;
}): boolean {
  return (
    hasValue(line.item_code) ||
    hasValue(line.quantity) ||
    hasValue(line.unit_price) ||
    hasValue(line.total)
  );
}

/**
 * Form-resolver schema: the base object + a conditional rule making `sales_type`
 * mandatory on a Purchase Request (it drives CS routing) and `unit_price` mandatory
 * on every real Sponsorship Form line (#1227). Kept separate from `PurchaseRequestSchema`
 * so the base object still exposes `.shape` for field-level unit tests.
 */
export const PurchaseRequestFormSchema = PurchaseRequestSchema.superRefine(
  (data, ctx) => {
    if (
      data.request_type === 'purchase_request' &&
      !(data.sales_type ?? '').trim()
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['sales_type'],
        message: 'Sales type is required.',
      });
    }
    // Quantity is required, above 0, on every line that has anything in it (the server
    // refuses a null, missing or <= 0 line quantity). Sentence per the shared helper.
    data.products.forEach((line, index) => {
      if (!lineHasContent(line)) return;
      const sentence = numberFieldError(line.quantity, QUANTITY_RULES);
      if (sentence) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['products', index, 'quantity'],
          message: sentence,
        });
      }
    });
    // Bad input in a price or total box (NaN) is told with the shared sentence, never saved as blank.
    data.products.forEach((line, index) => {
      const fields = data.request_type === 'sponsorship_form' ? (['total'] as const) : (['unit_price', 'total'] as const);
      for (const key of fields) {
        const value = line[key];
        if (typeof value === 'number' && Number.isNaN(value)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['products', index, key],
            message: numberFieldError(value, { min: 0, optional: true }) as string,
          });
        }
      }
    });
    // #1227: sponsorship forms only, mandatory unit price on every real line - purchase
    // requests are unchanged. Same idiom as the sales_type rule above.
    if (data.request_type === 'sponsorship_form') {
      data.products.forEach((line, index) => {
        if (!lineHasContent(line)) return;
        const num = line.unit_price == null || line.unit_price === '' ? NaN : Number(line.unit_price);
        if (Number.isNaN(num) || num < 0) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['products', index, 'unit_price'],
            message: 'Unit price is required.',
          });
        }
      });
    }
  },
);

export type PurchaseRequestSchemaType = z.infer<typeof PurchaseRequestSchema>;

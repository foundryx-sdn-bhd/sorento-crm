import { z } from 'zod';
import { numberFieldError } from '@/lib/number-field';

export const ComplaintSchema = z.object({
  delivery_order_number: z
    .string()
    .max(255, {
      message: 'Delivery order number must not exceed 255 characters.',
    })
    .optional()
    .nullable(),
  complaint_date: z.date().optional().nullable(),
  customer_type: z.string().max(255).optional().nullable(),
  customer_type_others: z.string().max(255).optional().nullable(),
  within_warranty: z.string().max(255).optional().nullable(),
  product_type: z.string().max(255).optional().nullable(),
  defects_discovered: z.string().max(255).optional().nullable(),
  complaint_type: z.string().max(255).optional().nullable(),
  defect_description: z.string().max(5000).optional().nullable(),
  product_code: z.string().max(255).optional().nullable(),
  quantity: z.string().max(255).optional().nullable(),
  product_lines: z
    .array(
      z.object({
        product_code: z.string().min(1, { message: 'Product code is required.' }),
        quantity: z
          .string()
          .max(255)
          .optional()
          .nullable()
          .superRefine((value, ctx) => {
            const message = numberFieldError(value, { min: 0, optional: true });
            if (message) ctx.addIssue({ code: z.ZodIssueCode.custom, message });
          }),
        product_type: z.string().max(255).optional().nullable(),
      }),
    )
    .optional(),
  salesperson: z.string().max(255).optional().nullable(),
  customer_name: z.string().max(255).optional().nullable(),
  contact_person: z.string().max(255).optional().nullable(),
  contact_number: z.string().max(255).optional().nullable(),
  customer_address: z.string().max(2000).optional().nullable(),
  project_title: z.string().max(255).optional().nullable(),
  // AC-L3. Optional forever: a complaint about a retail delivery has no project.
  project_id: z.string().uuid().optional().nullable(),
  contact_id: z.string().max(500).optional().nullable(),
  space_id: z.string().max(500).optional().nullable(),
  technical_team_response: z.string().max(5000).optional().nullable(),
  status: z.string().max(50).optional().nullable(),
  last_responded_by: z.string().max(255).optional().nullable(),
  last_responded_at: z.string().optional().nullable(),
  root_cause_id: z.string().optional().nullable(),
  resolution_id: z.string().optional().nullable(),
  required_on_site_support: z.boolean().optional().nullable(),
  attachments: z.array(
    z.object({
      id: z.string().optional(),
      complaint_id: z.string().optional(),
      file_name: z.string().optional().nullable(),
      file_url: z.string().optional().nullable(),
      file_size_bytes: z.number().optional().nullable(),
      uploaded_at: z.date().optional(),
      created_at: z.date().optional(),
    }),
  ),
});

export type ComplaintSchemaType = z.infer<typeof ComplaintSchema>;

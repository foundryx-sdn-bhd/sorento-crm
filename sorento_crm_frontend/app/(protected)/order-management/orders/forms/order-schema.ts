import { z } from 'zod';
import { optionalNumber } from '@/lib/number-field-schema';

export const OrderSchema = z.object({
  order_number: z
    .string()
    .min(1, { message: 'Order number is required.' })
    .max(100, { message: 'Order number must not exceed 100 characters.' }),
  order_date: z.date({ required_error: 'Order date is required.' }),
  estimated_delivery_date: z.date().optional().nullable(),
  actual_delivery_date: z.date().optional().nullable(),
  customer_id: z.string().optional().nullable(),
  order_status_id: z.string().min(1, { message: 'Order status is required.' }),
  billing_address_id: z.string().optional().nullable(),
  shipping_address_id: z.string().optional().nullable(),
  created_time: z.date().optional().nullable(),
  debtor_code: z.string().optional().nullable(),
  debtor_name: z.string().optional().nullable(),
  agent: z.string().optional().nullable(),
  is_cancelled: z.boolean().optional(),
  remarks_cs: z.string().optional().nullable(),
  order_type: z.string().optional().nullable(),
  pickup_time: z.string().optional().nullable(),
  checker: z.string().optional().nullable(),
  transporter: z.string().optional().nullable(),
  driver_name: z.string().optional().nullable(),
  lorry_plate: z.string().optional().nullable(),
  customer_ref: z.string().optional().nullable(),
  delivery_remarks_cs: z.string().optional().nullable(),
  delivery_remarks: z.string().optional().nullable(),
  salesman: z.string().optional().nullable(),
  trips: optionalNumber({ min: 0, integer: true }),
  warehouse: z.string().optional().nullable(),
  delivery_days: z.number().int().optional().nullable(),
  kpi_warning: z.boolean().optional(),
  subtotal_amount: z
    .number()
    .min(0, { message: 'Subtotal amount cannot be negative.' }),
  discount_amount: z
    .number()
    .min(0, { message: 'Discount amount cannot be negative.' })
    .optional()
    .nullable(),
  tax_amount: z
    .number()
    .min(0, { message: 'Tax amount cannot be negative.' })
    .optional()
    .nullable(),
  total_amount: z
    .number()
    .min(0, { message: 'Total amount cannot be negative.' }),
  remarks: z.string().max(2000, { message: 'Remarks must not exceed 2000 characters.' }).optional().nullable(),
}).refine((data) => {
  // Calculate total if not provided
  if (data.total_amount === 0) {
    const calculatedTotal = data.subtotal_amount - (data.discount_amount || 0) + (data.tax_amount || 0);
    return calculatedTotal >= 0;
  }
  return true;
}, {
  message: 'Total amount calculation error.',
  path: ['total_amount'],
});

export type OrderSchemaType = z.infer<typeof OrderSchema>;

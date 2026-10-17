import { z } from 'zod';
import { requiredNumber } from '@/lib/number-field-schema';

export const spoAllocationSchema = z.object({
  spo_number: z.string().min(1, 'SPO number is required'),
  product_id: z.string().min(1, 'Product is required'),
  warehouse_id: z.string().min(1, 'Warehouse is required'),
  allocated_quantity: requiredNumber({ min: 1, integer: true }),
  inbound_shipment_id: z.string().min(1, 'Packing list / Inbound shipment is required'),
});

export type SPOAllocationSchemaType = z.infer<typeof spoAllocationSchema>;

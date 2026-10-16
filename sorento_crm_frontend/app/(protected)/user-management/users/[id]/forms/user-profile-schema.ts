import { z } from 'zod';
import { requiredNumber } from '@/lib/number-field-schema';

export const UserProfileSchema = z.object({
  name: z.string().min(1, { message: 'Name is required.' }),
  email: z.string().min(1, { message: 'Email is required.' }).email({ message: 'Enter a valid email address.' }),
  roleIds: z.array(z.string()).min(1, { message: 'At least one role is required.' }),
  status: z.string().min(1, { message: 'Status is required.' }),
  respond_user_id: z.string().optional().nullable(),
  contact_number: z.string().optional().nullable(),
  // Blank clears the tier (null); a typed value must be a whole number of at least 1.
  tier: requiredNumber({ min: 1, integer: true, optional: true }) as unknown as z.ZodType<
    number | null | undefined,
    z.ZodTypeDef,
    number | null | undefined
  >,
  agent_ids: z.array(z.string()).optional(),
  superior_id: z.string().optional().nullable(),
  companyIds: z.array(z.string()).default([]),
  respond_contact_id: z.string().optional().nullable(),
  notify_whatsapp: z.boolean().optional(),
  notify_whatsapp_summary: z.boolean().optional(),
  notify_email_on_assignment: z.boolean().optional(),
  notify_email_on_escalation: z.boolean().optional(),
  notify_whatsapp_on_assignment: z.boolean().optional(),
  notify_whatsapp_on_escalation: z.boolean().optional(),
  notify_email_on_deadline_extended: z.boolean().optional(),
  notify_whatsapp_on_deadline_extended: z.boolean().optional(),
  notify_email_on_handling: z.boolean().optional(),
  notify_whatsapp_on_handling: z.boolean().optional(),
  notify_email_on_mention: z.boolean().optional(),
  notify_email_on_product_discontinued: z.boolean().optional(),
  notify_whatsapp_on_product_discontinued: z.boolean().optional(),
});

export type UserProfileSchemaType = z.infer<typeof UserProfileSchema>;

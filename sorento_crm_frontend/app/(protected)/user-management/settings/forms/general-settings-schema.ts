import { z } from 'zod';
import { requiredNumber } from '@/lib/number-field-schema';

/** Select value when no explicit default supplier is chosen (backend: null → oldest supplier). */
export const NO_DEFAULT_SUPPLIER_VALUE = '__none__';

/** Select value when no default approver is configured. */
export const NO_DEFAULT_APPROVER_VALUE = '__none__';

/** Select value when no default unit of measure is chosen (backend: null -> the built-in
 *  `EA` fallback). */
export const NO_DEFAULT_UOM_VALUE = '__none__';

/** Select value when no reserve default pool is configured (backend: null -> the row's
 *  own site pool, `PLAN-oi-request-cs-reserve.md` section 6c F1). */
export const NO_DEFAULT_RESERVE_POOL_VALUE = '__none__';

export const GeneralSettingsSchema = z.object({
  name: z.string().min(1, 'Company name is required'),
  logoFile: z
    .instanceof(File)
    .nullable()
    .optional()
    .refine(
      (file) => !file || file.size <= 1024 * 1024, // Check if file is not present or <= 1MB
      { message: 'Logo file must be smaller than 1MB' },
    ),
  logoAction: z.string().optional(),
  active: z.boolean(),
  address: z.string().nullable().optional(),
  websiteURL: z
    .string()
    .url('Must be a valid URL')
    .or(z.literal(''))
    .optional(),
  supportEmail: z.string().email('Must be a valid email'),
  supportPhone: z.string().nullable().optional(),
  language: z.string(),
  timezone: z.string(),
  currency: z.string(),
  currencyFormat: z.string(),
  defaultProductSupplierId: z.string(),
  defaultProductStandardLeadTimeDays: requiredNumber({ min: 0, max: 10950, integer: true }),
  /** The unit a product gets when the source states none (product import included). */
  defaultUomId: z.string(),
  /** The reserve dialog's own default Location (`PLAN-oi-request-cs-reserve.md`
   *  section 6c F1). */
  oiReserveDefaultPoolWarehouseId: z.string(),
  takeoverCooldownSeconds: requiredNumber({ min: 0, max: 3600, integer: true }),
  formSlaGraceSeconds: requiredNumber({ min: 0, max: 600, integer: true }),
  /**
   * The two deferred-action windows (D16). Minimum ONE second, not zero: a window of
   * zero applies the action with no way back, which is the confirmation dialog's
   * failure mode wearing the new model's clothes.
   */
  deferredDeleteSeconds: requiredNumber({ min: 1, max: 600, integer: true }),
  deferredActionSeconds: requiredNumber({ min: 1, max: 600, integer: true }),
  /**
   * The auto-collect sweep for office-printed price tags (r9 D10). Zero is
   * meaningful here, unlike the countdowns above: it turns the sweep off, and
   * a hand-over then waits for a person forever, which is a legitimate way to
   * run a counter.
   */
  priceTagAutoCollectDays: requiredNumber({ min: 0, max: 90, integer: true }),
  /** How long a chatbot file link opens, in days. Null = the link never expires. */
  chatbotFileLinkExpiryDays: requiredNumber({ min: 1, max: 3650, integer: true, optional: true }) as unknown as z.ZodType<
    number | null,
    z.ZodTypeDef,
    number | null
  >,
  /** SCM front planning: the grain new plans are decided at (AC-F01). */
  planGrain: z.enum(['product', 'location']),
  /** Local-supplier Buy routing (PLAN-local-buy-routing-toggle.md), off by default. Off
   *  means the whole local rule does not run: no pill, every Buy reaches Order Inquiries. */
  localBuyRoutingEnabled: z.boolean(),
  /** "Borrow from another order" (PLAN-borrow-location-only-8oct.md D1), off by default. */
  borrowFromOrderEnabled: z.boolean(),
  /** Sample requests (SR-CORE): need a registered project / a quotation to submit. Off by default. */
  sampleRequestRequireProject: z.boolean(),
  /** Price tag packages (D2): the product classes a line is warned about when it
   *  reaches marketing without its catalogue package. Empty warns nobody, which
   *  is a legitimate answer, so there is no minimum. */
  priceTagGuardedClasses: z.array(z.string()),
  purchaseRequestDefaultApproverUserId: z.string(),
  sponsorshipFormDefaultApproverUserId: z.string(),
});

export type GeneralSettingsSchemaType = z.infer<typeof GeneralSettingsSchema>;

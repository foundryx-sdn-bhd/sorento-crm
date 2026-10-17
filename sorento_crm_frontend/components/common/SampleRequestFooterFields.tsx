'use client';

/**
 * The paper form's footer fields (Delivery, Loan Period, Goods Taken From, Purpose), shared by the
 * staff page and the portal.
 */

import { Checkbox } from '@/components/ui/checkbox';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Textarea } from '@/components/ui/textarea';
import { WHOLE_AT_LEAST_ONE, type FormState } from '@/lib/sample-request-form';
import {
  DELIVERY_MODE_LABELS,
  GOODS_FROM_LABELS,
  type SampleRequestDeliveryMode,
  type SampleRequestLoanUnit,
} from '@/types/sampleRequest';

export function SampleRequestFooterFields({
  state,
  set,
  errors = {},
  disabled,
}: {
  state: FormState;
  set: <K extends keyof FormState>(key: K, value: FormState[K]) => void;
  errors?: Record<string, string>;
  disabled?: boolean;
}) {
  return (
    <div className="grid grid-cols-1 gap-x-10 gap-y-5 sm:grid-cols-2">
      <div className="space-y-1.5 sm:col-span-2">
        <p className="text-sm text-muted-foreground">Delivery *</p>
        <RadioGroup
          aria-label="Delivery"
          className="flex flex-wrap gap-x-6 gap-y-2"
          value={state.delivery_mode}
          disabled={disabled}
          onValueChange={(v) => set('delivery_mode', v as SampleRequestDeliveryMode)}
        >
          {(Object.keys(DELIVERY_MODE_LABELS) as SampleRequestDeliveryMode[]).map((mode) => (
            <div key={mode} className="flex items-center gap-2">
              <RadioGroupItem id={`sr-delivery-${mode}`} value={mode} />
              <Label htmlFor={`sr-delivery-${mode}`}>{DELIVERY_MODE_LABELS[mode]}</Label>
            </div>
          ))}
        </RadioGroup>
        {errors.delivery_mode && <p className="text-xs text-destructive">{errors.delivery_mode}</p>}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="sr-loan">Loan Period</Label>
        <div className="flex flex-wrap items-center gap-3">
          <NumberInput
            id="sr-loan"
            inputMode="numeric"
            min={1}
            className="w-24"
            disabled={disabled}
            value={state.loan_value}
            error={errors.loan_value ? numberFieldError(state.loan_value, WHOLE_AT_LEAST_ONE) : undefined}
            errorClassName="w-full"
            onChange={(e) => set('loan_value', e.target.value)}
          />
          <RadioGroup
            aria-label="Loan unit"
            className="flex items-center gap-4"
            value={state.loan_unit}
            disabled={disabled}
            onValueChange={(v) => set('loan_unit', v as SampleRequestLoanUnit)}
          >
            {(['days', 'months'] as const).map((unit) => (
              <div key={unit} className="flex items-center gap-2">
                <RadioGroupItem id={`sr-unit-${unit}`} value={unit} />
                <Label htmlFor={`sr-unit-${unit}`}>{unit === 'days' ? 'Days' : 'Months'}</Label>
              </div>
            ))}
          </RadioGroup>
        </div>
      </div>

      <div className="space-y-1.5">
        <p className="text-sm text-muted-foreground">Goods Taken From</p>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <div className="flex items-center gap-2">
            <Checkbox
              id="sr-goods-demo"
              checked={state.goods_demo}
              disabled={disabled}
              onCheckedChange={(on) => set('goods_demo', on === true)}
            />
            <Label htmlFor="sr-goods-demo">{GOODS_FROM_LABELS.demo}</Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="sr-goods-new"
              checked={state.goods_new}
              disabled={disabled}
              onCheckedChange={(on) => set('goods_new', on === true)}
            />
            <Label htmlFor="sr-goods-new">{GOODS_FROM_LABELS.new}</Label>
          </div>
        </div>
      </div>

      <div className="space-y-1.5 sm:col-span-2">
        <Label htmlFor="sr-purpose">Purpose</Label>
        <Textarea
          id="sr-purpose"
          rows={3}
          disabled={disabled}
          value={state.purpose}
          onChange={(e) => set('purpose', e.target.value)}
        />
      </div>
    </div>
  );
}

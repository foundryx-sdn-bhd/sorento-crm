import { describe, expect, it } from 'vitest';
import { UOMSchema } from './uom-schema';

describe('UOMSchema bad input (round 2, S-A)', () => {
  it('conversion_factor NaN reads as a number sentence, never raw zod text', () => {
    const result = UOMSchema.safeParse({
      uom_code: 'PCS',
      uom_name: 'Pieces',
      conversion_factor: NaN,
      decimal_places: 0,
      is_active: true,
    });
    expect(result.success).toBe(false);
    if (result.success) return;
    const issue = result.error.issues.find((i) => i.path[0] === 'conversion_factor')!;
    expect(issue.message).toMatch(/^Enter a number/);
  });
});

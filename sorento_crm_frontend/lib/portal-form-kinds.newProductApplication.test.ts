/**
 * `new_product_application` joins the portal form kinds: labelled "New Product Application", a
 * landing kind, grantable beyond the base four, and placed before the trailing four kinds.
 */
import { describe, expect, it } from 'vitest';
import {
  ADDITIONAL_LANDING_KINDS,
  LANDING_KINDS,
  isLandingKind,
  portalFormKindLabel,
} from './portal-form-kinds';

describe('new_product_application portal form kind', () => {
  it('labels it "New Product Application"', () => {
    expect(portalFormKindLabel('new_product_application')).toBe(
      'New Product Application',
    );
  });

  it('is a landing kind and a grantable additional kind', () => {
    expect(isLandingKind('new_product_application')).toBe(true);
    expect(ADDITIONAL_LANDING_KINDS).toContain('new_product_application');
  });

  it('sits next to sample_request, ahead of the trailing kinds', () => {
    const at = LANDING_KINDS.indexOf('new_product_application');
    expect(at).toBe(LANDING_KINDS.indexOf('sample_request') + 1);
  });
});

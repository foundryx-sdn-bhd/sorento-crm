/**
 * `sample_request` joins the portal form kinds (AC-SR-40): labelled "Sample Request", a landing
 * kind, and grantable beyond the base four like price_tag_request.
 */
import { describe, expect, it } from 'vitest';
import {
  ADDITIONAL_LANDING_KINDS,
  isLandingKind,
  portalFormKindLabel,
} from './portal-form-kinds';

describe('sample_request portal form kind', () => {
  it('labels it "Sample Request"', () => {
    expect(portalFormKindLabel('sample_request')).toBe('Sample Request');
  });

  it('is a landing kind', () => {
    expect(isLandingKind('sample_request')).toBe(true);
  });

  it('joins the grantable additional kinds', () => {
    expect(ADDITIONAL_LANDING_KINDS).toContain('sample_request');
  });
});

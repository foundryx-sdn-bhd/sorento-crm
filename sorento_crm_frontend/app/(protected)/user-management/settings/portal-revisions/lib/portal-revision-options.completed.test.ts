/** RED test - NC-7: an admin can add Completed to the statuses a PR / SF may be revised in. */
import { describe, expect, it } from 'vitest';
import {
  PORTAL_REVISION_STATUS_OPTIONS,
  portalRevisionStatusLabel,
} from './portal-revision-options';

describe('portal revision status options', () => {
  it.each(['purchase_request', 'sponsorship_form'])('%s offers Completed', (type) => {
    expect(PORTAL_REVISION_STATUS_OPTIONS[type]).toContainEqual({
      value: 'completed',
      label: 'Completed',
    });
    expect(portalRevisionStatusLabel(type, 'completed')).toBe('Completed');
  });

  it('does not add Completed to types that cannot reach it', () => {
    for (const type of ['stock_inquiry', 'complaint', 'price_tag_request']) {
      expect(PORTAL_REVISION_STATUS_OPTIONS[type].map((o) => o.value)).not.toContain('completed');
    }
  });
});

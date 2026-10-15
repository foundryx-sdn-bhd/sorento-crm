/** RED test - NC-5 (owner ruling 14 Oct 22:02): there is no Completed status; the PR and SF
 *  revision status option lists must not offer one. */
import { describe, expect, it } from 'vitest';
import { PORTAL_REVISION_STATUS_OPTIONS } from './portal-revision-options';

describe('portal revision status options', () => {
  it.each(['purchase_request', 'sponsorship_form'])('%s offers no completed value', (type) => {
    expect(PORTAL_REVISION_STATUS_OPTIONS[type].map((o) => o.value)).not.toContain('completed');
  });
});

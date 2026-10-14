/** NC-7 guard: the portal reads `completed` as "Completed". Likely green already. */
import { describe, expect, it } from 'vitest';
import { statusLabel } from './portal-client';

describe('portal statusLabel completed', () => {
  it('labels completed', () => {
    expect(statusLabel('completed')).toBe('Completed');
  });
});

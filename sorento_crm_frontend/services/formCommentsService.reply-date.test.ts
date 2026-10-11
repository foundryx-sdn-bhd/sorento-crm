/**
 * FORM-COMMENTS review round (red), B5: the purchasing reply entry can have no recorded time
 * (`created_at: null` from the API). The mapper keeps that as null instead of turning it into
 * the empty string that the thread then formats as an invalid date.
 */
import { describe, expect, it } from 'vitest';
import { toFormComment } from './formCommentsService';

describe('toFormComment created_at', () => {
  it('maps a null created_at to a null createdAt', () => {
    const c = toFormComment({
      id: 'response',
      kind: 'response',
      body: 'brand?',
      author_name: 'Rita',
      created_at: null,
    });
    expect(c.createdAt).toBeNull();
  });

  it('keeps a present created_at as is', () => {
    const c = toFormComment({ id: 'x', body: 'b', created_at: '2026-10-02T08:30:00' });
    expect(c.createdAt).toBe('2026-10-02T08:30:00');
  });
});

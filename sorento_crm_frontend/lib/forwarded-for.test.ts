import { describe, expect, it } from 'vitest';
import { forwardedForHeader } from './forwarded-for';

describe('forwardedForHeader', () => {
  it('reads a Headers instance', () => {
    const h = new Headers({ 'x-forwarded-for': '6.6.6.6, 203.0.113.7' });
    expect(forwardedForHeader(h)).toEqual({ 'X-Forwarded-For': '6.6.6.6, 203.0.113.7' });
  });

  it('reads the plain object NextAuth hands authorize()', () => {
    expect(forwardedForHeader({ 'x-forwarded-for': '203.0.113.7' })).toEqual({
      'X-Forwarded-For': '203.0.113.7',
    });
    expect(forwardedForHeader({ 'x-forwarded-for': ['6.6.6.6', '203.0.113.7'] })).toEqual({
      'X-Forwarded-For': '6.6.6.6, 203.0.113.7',
    });
  });

  it('adds nothing when there is no header', () => {
    expect(forwardedForHeader(undefined)).toEqual({});
    expect(forwardedForHeader({})).toEqual({});
    expect(forwardedForHeader(new Headers())).toEqual({});
  });
});

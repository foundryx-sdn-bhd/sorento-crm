import { describe, expect, it } from 'vitest';

import { linkifySegments } from './linkifySegments';

describe('linkifySegments', () => {
  it('returns nothing for empty text', () => {
    expect(linkifySegments('')).toEqual([]);
  });

  it('leaves text with no url as a single plain segment', () => {
    expect(linkifySegments('Waiting on the warehouse.')).toEqual([
      { text: 'Waiting on the warehouse.' },
    ]);
  });

  it('splits a url out of the middle of a sentence', () => {
    expect(linkifySegments('see https://x.test/a now')).toEqual([
      { text: 'see ' },
      { text: 'https://x.test/a', href: 'https://x.test/a' },
      { text: ' now' },
    ]);
  });

  it('keeps a hash fragment inside the link', () => {
    const url = 'https://app.respond.io/space/364817/inbox/900000108#1787975493000000';
    expect(linkifySegments(url)).toEqual([{ text: url, href: url }]);
  });

  it('leaves a trailing full stop out of the link', () => {
    expect(linkifySegments('see https://x.test/a.')).toEqual([
      { text: 'see ' },
      { text: 'https://x.test/a', href: 'https://x.test/a' },
      { text: '.' },
    ]);
  });

  it('leaves a trailing closing bracket out of the link', () => {
    expect(linkifySegments('(https://x.test/a)')).toEqual([
      { text: '(' },
      { text: 'https://x.test/a', href: 'https://x.test/a' },
      { text: ')' },
    ]);
  });

  it('links every url in the text', () => {
    expect(linkifySegments('a https://x.test b http://y.test c')).toEqual([
      { text: 'a ' },
      { text: 'https://x.test', href: 'https://x.test' },
      { text: ' b ' },
      { text: 'http://y.test', href: 'http://y.test' },
      { text: ' c' },
    ]);
  });

  it('gives a bare www host an https scheme', () => {
    expect(linkifySegments('www.sorento.com.my')).toEqual([
      { text: 'www.sorento.com.my', href: 'https://www.sorento.com.my' },
    ]);
  });

  it('never links a javascript: url', () => {
    const text = 'javascript:alert(1) and javascript:void(0)';
    expect(linkifySegments(text)).toEqual([{ text }]);
  });

  it('links only the http part when a scheme is prefixed to one', () => {
    const segments = linkifySegments('javascript:https://x.test/a');
    expect(segments.every((segment) => !segment.href?.startsWith('javascript'))).toBe(true);
    expect(segments.some((segment) => segment.href === 'https://x.test/a')).toBe(true);
  });

  it('preserves the original text when the segments are joined back up', () => {
    const text = 'open https://x.test/a. then www.y.test, thanks';
    expect(linkifySegments(text).map((segment) => segment.text).join('')).toBe(text);
  });
});

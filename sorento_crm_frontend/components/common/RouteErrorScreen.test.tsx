/**
 * L8 (NEVER-STUCK-UI S5, audit rows 33 and 40): every route group's error
 * boundary renders this one screen. Fixed copy (never `error.message`), the
 * digest as a reference, Try again calling `reset`, and for a chunk-load failure
 * (deploy skew: the old build's chunks are gone) a Reload that does a full page
 * load, because `reset()` cannot fetch a build that no longer exists.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import RouteErrorScreen, { isChunkLoadError } from './RouteErrorScreen';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function chunkError(): Error {
  const e = new Error('Loading chunk 4512 failed.\n(error: https://x/_next/static/chunks/4512.js)');
  e.name = 'ChunkLoadError';
  return e;
}

describe('isChunkLoadError', () => {
  it('recognises webpack, turbopack and native dynamic-import failures', () => {
    expect(isChunkLoadError(chunkError())).toBe(true);
    expect(isChunkLoadError(new Error('Loading CSS chunk 12 failed'))).toBe(true);
    expect(
      isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: https://x/a.js')),
    ).toBe(true);
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true);
    expect(isChunkLoadError(new Error('Failed to load chunk /_next/static/chunks/a.js'))).toBe(true);
  });

  it('does not treat an ordinary render error as a chunk failure', () => {
    expect(isChunkLoadError(new TypeError("Cannot read properties of undefined (reading 'x')"))).toBe(
      false,
    );
    expect(isChunkLoadError(undefined)).toBe(false);
  });
});

describe('RouteErrorScreen', () => {
  it('shows fixed copy, never the raw message, plus the digest and a Try again that calls reset', () => {
    const reset = vi.fn();
    const error = Object.assign(new Error('secret 11111111-1111-4111-8111-111111111111'), {
      digest: 'abc123',
    });
    render(<RouteErrorScreen error={error} reset={reset} />);

    expect(screen.getByRole('heading', { name: 'Something went wrong' })).toBeTruthy();
    expect(screen.queryByText(/secret/)).toBeNull();
    expect(screen.getByText(/abc123/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it('offers a full Reload, not Try again, for a chunk-load failure', () => {
    const reset = vi.fn();
    const reload = vi.fn();
    render(<RouteErrorScreen error={chunkError()} reset={reset} onReload={reload} />);

    expect(screen.getByRole('heading', { name: 'A new version is available' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(reload).toHaveBeenCalledTimes(1);
    expect(reset).not.toHaveBeenCalled();
  });

  it('renders the way out the route group passes, and none when it passes none', () => {
    const { rerender } = render(
      <RouteErrorScreen
        error={new Error('x')}
        reset={vi.fn()}
        exit={{ href: '/signin', label: 'Back to sign in' }}
      />,
    );
    expect(screen.getByRole('link', { name: /Back to sign in/ }).getAttribute('href')).toBe('/signin');

    rerender(<RouteErrorScreen error={new Error('x')} reset={vi.fn()} />);
    expect(screen.queryByRole('link')).toBeNull();
  });
});

/**
 * L8 (NEVER-STUCK-UI S5.1-S5.2, audit rows 33 and 40). Every route group has an
 * error boundary, and the app has a global one for what escapes the root layout
 * (a failed `ssr:false` chunk load of the client providers lands there).
 * Without them Next shows a blank page or a bare "Application error".
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { renderToStaticMarkup } from 'react-dom/server';

import GlobalError from './global-error';
import GlobalErrorView from '@/components/common/GlobalErrorView';
import AuthError from './(auth)/error';
import PublicError from './(public)/error';
import UnsubscribeError from './unsubscribe/error';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const APP = __dirname;

describe('route group error boundaries', () => {
  it.each(['(protected)', '(auth)', '(public)', 'unsubscribe'])(
    '%s has an error.tsx',
    (group) => {
      expect(fs.existsSync(path.join(APP, group, 'error.tsx'))).toBe(true);
    },
  );

  it('the app has a global-error.tsx', () => {
    expect(fs.existsSync(path.join(APP, 'global-error.tsx'))).toBe(true);
  });

  it('sign-in and customer-link errors show fixed copy and Try again, no staff link', () => {
    const reset = vi.fn();
    render(<AuthError error={new Error('boom')} reset={reset} />);
    expect(screen.getByRole('heading', { name: 'Something went wrong' })).toBeTruthy();
    expect(screen.queryByText('boom')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(reset).toHaveBeenCalled();
    // `(auth)` also holds customer pages (portal, quotation-sign, view), whose
    // readers have no staff account to sign in to.
    expect(screen.queryByRole('link')).toBeNull();
  });

  it.each([
    ['(public)', PublicError],
    ['unsubscribe', UnsubscribeError],
  ])('%s errors show fixed copy and Try again, no staff link', (_g, Boundary) => {
    const reset = vi.fn();
    render(<Boundary error={new Error('boom')} reset={reset} />);
    expect(screen.getByRole('heading', { name: 'Something went wrong' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(reset).toHaveBeenCalled();
    expect(screen.queryByRole('link')).toBeNull();
  });
});

describe('global-error', () => {
  it('renders its own html and body, since the root layout is what failed', () => {
    const html = renderToStaticMarkup(<GlobalError error={new Error('x')} reset={vi.fn()} />);
    expect(html.startsWith('<html')).toBe(true);
    expect(html).toContain('<body');
    expect(html).toContain('Something went wrong');
    expect(html).toContain('Reload');
    expect(html).not.toContain('>x<');
  });

  it('Reload does a full page load, the only thing that fetches a new build', () => {
    const reload = vi.fn();
    render(<GlobalErrorView digest="d1" onReload={reload} />);
    expect(screen.getByText(/d1/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

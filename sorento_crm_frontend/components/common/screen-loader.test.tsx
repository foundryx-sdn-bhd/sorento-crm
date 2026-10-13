/**
 * LOADER-CENTER - the wordmark and spinner share one centre line. The logo SVG
 * draws its word from x=0 of a wider box, so centring the <img> put the spinner
 * ~20px right of the word; the splash now draws the word as text.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { ScreenLoader } from './screen-loader';

describe('ScreenLoader (LOADER-CENTER)', () => {
  it('draws the wordmark as text, not the left-anchored logo image', () => {
    const { container } = render(<ScreenLoader />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('Sorento')).toBeInTheDocument();
  });

  it('stacks wordmark then spinner in one centred column over the viewport', () => {
    const { container } = render(<ScreenLoader />);
    const root = container.querySelector(
      '[data-slot="screen-loader"]',
    ) as HTMLElement;
    for (const cls of [
      'fixed',
      'inset-0',
      'flex',
      'flex-col',
      'items-center',
      'justify-center',
    ]) {
      expect(root).toHaveClass(cls);
    }
    const [word, spinner] = Array.from(root.children);
    expect(word).toHaveTextContent('Sorento');
    expect(spinner).toBe(screen.getByRole('status', { name: /loading/i }));
  });
});

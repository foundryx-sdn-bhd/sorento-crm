/** PROMPT-DYNAMIC R5b: diff next/previous change, counter, keys, changes only. Red first. */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DiffView } from './DiffView';

if (!(HTMLElement.prototype as unknown as { scrollIntoView?: unknown }).scrollIntoView) {
  (HTMLElement.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = () => {};
}

const A = ['h1', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'x', 'y', 'z'].join('\n');
const B = ['h1', 'a2', 'b', 'c', 'd', 'e', 'f', 'g', 'x', 'y2', 'z'].join('\n');

afterEach(() => cleanup());

function current() {
  return Array.from(document.querySelectorAll('[data-current="true"]')).map((e) => e.textContent);
}

describe('DiffView navigation', () => {
  it('counts the changes and steps through them with the buttons', () => {
    render(<DiffView a={A} b={B} aLabel="v42" bLabel="draft" />);
    expect(screen.getByTestId('diff-change-counter')).toHaveTextContent('Change 1 of 2');
    expect(current().join('|')).toContain('a2');
    fireEvent.click(screen.getByTestId('diff-next'));
    expect(screen.getByTestId('diff-change-counter')).toHaveTextContent('Change 2 of 2');
    expect(current().join('|')).toContain('y2');
    fireEvent.click(screen.getByTestId('diff-next')); // wraps
    expect(screen.getByTestId('diff-change-counter')).toHaveTextContent('Change 1 of 2');
    fireEvent.click(screen.getByTestId('diff-prev'));
    expect(screen.getByTestId('diff-change-counter')).toHaveTextContent('Change 2 of 2');
  });

  it('Alt+ArrowDown and Alt+ArrowUp step too', () => {
    render(<DiffView a={A} b={B} aLabel="v42" bLabel="draft" />);
    fireEvent.keyDown(screen.getByTestId('diff-view'), { key: 'ArrowDown', altKey: true });
    expect(screen.getByTestId('diff-change-counter')).toHaveTextContent('Change 2 of 2');
    fireEvent.keyDown(screen.getByTestId('diff-view'), { key: 'ArrowUp', altKey: true });
    expect(screen.getByTestId('diff-change-counter')).toHaveTextContent('Change 1 of 2');
  });

  it('changes only folds unchanged runs and a gap opens on click', () => {
    render(<DiffView a={A} b={B} aLabel="v42" bLabel="draft" />);
    fireEvent.click(screen.getByTestId('diff-changes-only'));
    const gap = screen.getByTestId('diff-gap');
    expect(gap).toHaveTextContent('3 unchanged lines');
    expect(screen.queryByText('e')).toBeNull();
    fireEvent.click(gap);
    expect(screen.queryByTestId('diff-gap')).toBeNull();
    expect(screen.getByText('e')).toBeInTheDocument();
  });

  it('says so when there is nothing to step through', () => {
    render(<DiffView a="same" b="same" aLabel="v1" bLabel="v1" />);
    expect(screen.getByTestId('diff-change-counter')).toHaveTextContent('No changes');
    expect(screen.getByTestId('diff-next')).toBeDisabled();
  });
});

describe('DiffView, reviewer pass 2', () => {
  it('typing in the draft keeps the current change and does not scroll', () => {
    const spy = vi.spyOn(HTMLElement.prototype, 'scrollIntoView');
    const { rerender } = render(<DiffView a={A} b={B} aLabel="v42" bLabel="draft" />);
    fireEvent.click(screen.getByTestId('diff-next'));
    expect(screen.getByTestId('diff-change-counter')).toHaveTextContent('Change 2 of 2');
    spy.mockClear();
    rerender(<DiffView a={A} b={B.replace('y2', 'y22')} aLabel="v42" bLabel="draft" />);
    expect(screen.getByTestId('diff-change-counter')).toHaveTextContent('Change 2 of 2');
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

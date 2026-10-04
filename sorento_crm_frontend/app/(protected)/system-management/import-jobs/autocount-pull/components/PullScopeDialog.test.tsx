/**
 * PullScopeDialog - the DocDate window a delivery-orders pull starts with (owner Q1: the last
 * 31 days prefilled; Q2: no per-document field). Mock section 1.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { PullScopeDialog, defaultWindow, todayInMalaysia } from './PullScopeDialog';

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  // 30 Sep 2026 23:30 UTC is already 1 Oct 07:30 in Malaysia.
  vi.setSystemTime(new Date('2026-09-30T23:30:00Z'));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('defaultWindow', () => {
  it('is the 31 Malaysian days ending today', () => {
    expect(todayInMalaysia()).toBe('2026-10-01');
    expect(defaultWindow()).toEqual({ fromDay: '2026-09-01', toDay: '2026-10-01' });
  });
});

describe('PullScopeDialog', () => {
  it('opens prefilled with the last 31 days and posts them on Pull', () => {
    const onPull = vi.fn();
    const onOpenChange = vi.fn();
    render(<PullScopeDialog open onOpenChange={onOpenChange} onPull={onPull} />);

    expect(screen.getByRole('heading', { name: 'Pull delivery orders from AutoCount' })).toBeInTheDocument();
    expect(screen.getByLabelText('From day')).toHaveValue('01/09/2026');
    expect(screen.getByLabelText('To day')).toHaveValue('01/10/2026');
    expect(screen.queryByLabelText(/doc/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Pull' }));

    expect(onPull).toHaveBeenCalledWith({ fromDay: '2026-09-01', toDay: '2026-10-01' });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('Cancel closes without pulling', () => {
    const onPull = vi.fn();
    const onOpenChange = vi.fn();
    render(<PullScopeDialog open onOpenChange={onOpenChange} onPull={onPull} />);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onPull).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('both days cleared pulls the gateway default (no scope); one day cleared holds Pull', () => {
    const onPull = vi.fn();
    render(<PullScopeDialog open onOpenChange={vi.fn()} onPull={onPull} />);

    fireEvent.change(screen.getByLabelText('From day'), { target: { value: '' } });
    expect(screen.getByRole('button', { name: 'Pull' })).toBeDisabled();
    expect(screen.getByText('Set both days, or clear both.')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('To day'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Pull' }));

    expect(onPull).toHaveBeenCalledWith(null);
  });

  it('with requireWindow (goods receipt notes, ss#107) both days cleared holds Pull', () => {
    const onPull = vi.fn();
    render(
      <PullScopeDialog
        open
        onOpenChange={vi.fn()}
        onPull={onPull}
        documentLabel="goods receipt notes"
        requireWindow
      />,
    );

    expect(screen.getByRole('heading', { name: 'Pull goods receipt notes from AutoCount' })).toBeInTheDocument();
    expect(screen.queryByText(/Leave both empty/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('From day'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('To day'), { target: { value: '' } });
    expect(screen.getByRole('button', { name: 'Pull' })).toBeDisabled();
    expect(screen.getByText('Set both days.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Pull' }));
    expect(onPull).not.toHaveBeenCalled();
  });
});

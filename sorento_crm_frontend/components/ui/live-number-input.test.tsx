import React, { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LiveNumberInput } from './number-input';

function Harness({
  initial,
  rules,
  spy,
}: {
  initial: number | null;
  rules?: { min?: number; max?: number; integer?: boolean; optional?: boolean; minExclusive?: boolean };
  spy: (n: number) => void;
}) {
  const [v, setV] = useState<number | null>(initial);
  return (
    <>
      <LiveNumberInput
        aria-label="Qty"
        value={v}
        rules={rules}
        onCommit={(n) => {
          spy(n);
          setV(n);
        }}
      />
      <button type="button">elsewhere</button>
    </>
  );
}

const rules = { min: 1, max: 50, integer: true };

describe('LiveNumberInput', () => {
  it('B1: a two-step clear 12 -> 1 -> blank leaves the box blank, commits 1 only, shows the message on blur', async () => {
    const spy = vi.fn();
    render(<Harness initial={12} rules={rules} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.type(input, '{Backspace}');
    expect(input.value).toBe('1');
    expect(spy).toHaveBeenLastCalledWith(1);
    await userEvent.type(input, '{Backspace}');
    expect(input.value).toBe('');
    expect(spy).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByText('elsewhere'));
    expect(input.value).toBe('');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Enter a whole number between 1 and 50.')).toBeVisible();
  });

  it('over max after a valid figure stays as typed, no commit of the bad figure', async () => {
    const spy = vi.fn();
    render(<Harness initial={3} rules={rules} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, '4');
    expect(spy).toHaveBeenLastCalledWith(4);
    await userEvent.type(input, '0');
    expect(input.value).toBe('40');
    expect(spy).toHaveBeenLastCalledWith(40);
    await userEvent.type(input, '0');
    expect(input.value).toBe('400');
    expect(spy).not.toHaveBeenCalledWith(400);
    await userEvent.click(screen.getByText('elsewhere'));
    expect(input.value).toBe('400');
    expect(screen.getByText('Enter a whole number between 1 and 50.')).toBeVisible();
  });

  it('S2: no message while typing a valid figure through invalid prefixes', async () => {
    const spy = vi.fn();
    render(<Harness initial={null} rules={{ min: 1000, max: 6000 }} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.type(input, '25');
    expect(screen.queryByText(/Enter a/)).not.toBeInTheDocument();
    expect(input).not.toHaveAttribute('aria-invalid', 'true');
    expect(spy).not.toHaveBeenCalled();
    await userEvent.type(input, '00');
    expect(spy).toHaveBeenLastCalledWith(2500);
    expect(screen.queryByText(/Enter a/)).not.toBeInTheDocument();
  });

  it('once shown at blur the message updates live and a valid value clears it', async () => {
    const spy = vi.fn();
    render(<Harness initial={null} rules={rules} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.type(input, '99');
    await userEvent.click(screen.getByText('elsewhere'));
    expect(screen.getByText('Enter a whole number between 1 and 50.')).toBeVisible();
    await userEvent.clear(input);
    expect(screen.getByText('Enter a whole number between 1 and 50.')).toBeVisible();
    await userEvent.type(input, '7');
    expect(spy).toHaveBeenLastCalledWith(7);
    expect(screen.queryByText(/Enter a/)).not.toBeInTheDocument();
    expect(input).not.toHaveAttribute('aria-invalid', 'true');
  });

  it('S4: minExclusive refuses 0 with the above sentence', async () => {
    const spy = vi.fn();
    render(<Harness initial={null} rules={{ min: 0, minExclusive: true }} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.type(input, '0');
    expect(spy).not.toHaveBeenCalled();
    await userEvent.click(screen.getByText('elsewhere'));
    expect(screen.getByText('Enter a number above 0.')).toBeVisible();
  });

  it('Q1a: a blank after an edit shows nothing while focused, the sentence at blur, clears on a valid figure', async () => {
    const spy = vi.fn();
    render(<Harness initial={12} rules={rules} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.clear(input);
    expect(input).toHaveFocus();
    expect(screen.queryByText(/Enter a/)).not.toBeInTheDocument();
    expect(input).not.toHaveAttribute('aria-invalid', 'true');
    await userEvent.click(screen.getByText('elsewhere'));
    expect(screen.getByText('Enter a whole number between 1 and 50.')).toBeInTheDocument();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    await userEvent.type(input, '5');
    expect(screen.queryByText(/Enter a/)).not.toBeInTheDocument();
    expect(spy).toHaveBeenLastCalledWith(5);
  });

  it('Q1b: a figure over max shows nothing while focused, the sentence at blur', async () => {
    const spy = vi.fn();
    render(<Harness initial={3} rules={rules} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, '400');
    expect(input).toHaveFocus();
    expect(screen.queryByText(/Enter a/)).not.toBeInTheDocument();
    expect(input).not.toHaveAttribute('aria-invalid', 'true');
    await userEvent.click(screen.getByText('elsewhere'));
    expect(screen.getByText('Enter a whole number between 1 and 50.')).toBeInTheDocument();
  });

  it('Q1c: a negative with min 0 shows nothing while focused, the sentence at blur', async () => {
    const spy = vi.fn();
    render(<Harness initial={null} rules={{ min: 0 }} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.type(input, '-5');
    expect(input).toHaveFocus();
    expect(screen.queryByText(/Enter a/)).not.toBeInTheDocument();
    expect(input).not.toHaveAttribute('aria-invalid', 'true');
    expect(spy).not.toHaveBeenCalled();
    await userEvent.click(screen.getByText('elsewhere'));
    expect(screen.getByText(/Enter a/)).toBeInTheDocument();
  });

  it('S-D: a null-valued non-optional box focused then blurred without typing shows no sentence', async () => {
    const spy = vi.fn();
    render(<Harness initial={null} rules={{ min: 4 }} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.click(input);
    await userEvent.click(screen.getByText('elsewhere'));
    expect(input).not.toHaveAttribute('aria-invalid', 'true');
    expect(screen.queryByText('Enter a number of at least 4.')).toBeNull();
  });
});

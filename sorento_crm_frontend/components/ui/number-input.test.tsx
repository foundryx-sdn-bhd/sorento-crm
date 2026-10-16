import React, { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useForm } from 'react-hook-form';
import { NumberInput } from './number-input';

function Harness({ initial, spy }: { initial: number | null; spy?: (v: number | null) => void }) {
  const [v, setV] = useState<number | null>(initial);
  return (
    <NumberInput
      aria-label="Qty"
      value={v}
      onValueChange={(n) => {
        spy?.(n);
        setV(n);
      }}
    />
  );
}

describe('NumberInput numeric mode', () => {
  it('renders a number input', () => {
    render(<Harness initial={1} />);
    expect(screen.getByLabelText('Qty')).toHaveAttribute('type', 'number');
  });

  it('UAC1.1: deleting the 1 leaves the field empty and reports null', async () => {
    const spy = vi.fn();
    render(<Harness initial={1} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.clear(input);
    expect(input.value).toBe('');
    expect(spy).toHaveBeenLastCalledWith(null);
  });

  it('UAC1.2: typing 2 into the empty field shows 2 and reports 2', async () => {
    const spy = vi.fn();
    render(<Harness initial={1} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, '2');
    expect(input.value).toBe('2');
    expect(spy).toHaveBeenLastCalledWith(2);
  });

  it('UAC1.3: partial input is never rewritten by the parent echo', async () => {
    const spy = vi.fn();
    render(<Harness initial={null} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.type(input, '1.');
    // jsdom's number input reports '1.' as 1 or ''; either way the draft is not clobbered
    // to something the user did not type.
    expect(['1.', '1', '']).toContain(input.value);
    await userEvent.type(input, '5');
    expect(input.value).toBe('1.5');
    expect(spy).toHaveBeenLastCalledWith(1.5);
  });

  it('UAC1.4: a value over a max attribute is kept as typed', async () => {
    const spy = vi.fn();
    function Capped() {
      const [v, setV] = useState<number | null>(1);
      return (
        <NumberInput aria-label="Qty" min={1} max={212} value={v} onValueChange={(n) => { spy(n); setV(n); }} />
      );
    }
    render(<Capped />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, '300');
    expect(input.value).toBe('300');
    expect(spy).toHaveBeenLastCalledWith(300);
  });

  it('UAC1.6: a value changed from outside re-syncs the shown text', () => {
    const { rerender } = render(
      <NumberInput aria-label="Qty" value={1} onValueChange={() => {}} />,
    );
    expect((screen.getByLabelText('Qty') as HTMLInputElement).value).toBe('1');
    rerender(<NumberInput aria-label="Qty" value={9} onValueChange={() => {}} />);
    expect((screen.getByLabelText('Qty') as HTMLInputElement).value).toBe('9');
    rerender(<NumberInput aria-label="Qty" value={null} onValueChange={() => {}} />);
    expect((screen.getByLabelText('Qty') as HTMLInputElement).value).toBe('');
  });
});

describe('NumberInput error prop', () => {
  it('marks the input invalid and shows the message, described by it', () => {
    render(
      <NumberInput aria-label="Qty" value={null} onValueChange={() => {}} error="Enter a number." />,
    );
    const input = screen.getByLabelText('Qty');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    const msg = screen.getByText('Enter a number.');
    expect(msg).toBeVisible();
    const id = input.getAttribute('aria-describedby');
    expect(id).toBeTruthy();
    expect(document.getElementById(id as string)).toContainElement(msg);
  });

  it('has neither aria-invalid nor a message when error is unset', () => {
    render(<NumberInput aria-label="Qty" value={3} onValueChange={() => {}} />);
    const input = screen.getByLabelText('Qty');
    expect(input).not.toHaveAttribute('aria-invalid', 'true');
    expect(screen.queryByText(/Enter a/)).not.toBeInTheDocument();
  });
});

describe('NumberInput pass-through', () => {
  it('works as a plain controlled input with a string value and onChange', async () => {
    const onChange = vi.fn();
    function Plain() {
      const [s, setS] = useState('5');
      return (
        <NumberInput
          aria-label="Plain"
          value={s}
          onChange={(e) => {
            onChange(e.target.value);
            setS(e.target.value);
          }}
        />
      );
    }
    render(<Plain />);
    const input = screen.getByLabelText('Plain') as HTMLInputElement;
    await userEvent.clear(input);
    expect(input.value).toBe('');
    await userEvent.type(input, '8');
    expect(input.value).toBe('8');
    expect(onChange).toHaveBeenLastCalledWith('8');
  });

  it('works with a react-hook-form register spread', async () => {
    const submitted = vi.fn();
    function Form() {
      const { register, handleSubmit } = useForm<{ qty: string }>({ defaultValues: { qty: '3' } });
      return (
        <form onSubmit={handleSubmit((d) => submitted(d))}>
          <NumberInput aria-label="Rhf" {...register('qty')} />
          <button type="submit">go</button>
        </form>
      );
    }
    render(<Form />);
    const input = screen.getByLabelText('Rhf') as HTMLInputElement;
    expect(input.value).toBe('3');
    await userEvent.clear(input);
    await userEvent.type(input, '12');
    await userEvent.click(screen.getByRole('button', { name: 'go' }));
    expect(submitted).toHaveBeenCalledWith({ qty: '12' });
  });
});

describe('NumberInput message wrapping (fix round 1, finding A)', () => {
  it('wraps the full sentence under the field even inside a truncating narrow cell', () => {
    const msg = 'Enter a whole number between 1 and 212.';
    render(
      <div className="truncate" style={{ width: 110 }}>
        <NumberInput aria-label="Qty" error={msg} />
      </div>,
    );
    const el = screen.getByText(msg);
    expect(el).toHaveClass('whitespace-normal');
    const cls = el.className;
    expect(/break-words|wrap-break-word|\[overflow-wrap:anywhere\]/.test(cls)).toBe(true);
  });
});

describe('NumberInput round 1b', () => {
  it('S3: browser bad input (empty value, validity.badInput) reports NaN, not null', () => {
    const spy = vi.fn();
    render(<Harness initial={5} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    Object.defineProperty(input, 'validity', { value: { badInput: true }, configurable: true });
    fireEvent.change(input, { target: { value: '' } });
    expect(spy).toHaveBeenLastCalledWith(NaN);
  });

  it('S3: a plain empty box (no badInput) still reports null', () => {
    const spy = vi.fn();
    render(<Harness initial={5} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '' } });
    expect(spy).toHaveBeenLastCalledWith(null);
  });

  it('S10 UAC1.5: an arrow-key / spinner step reports the stepped value', () => {
    const spy = vi.fn();
    render(<Harness initial={5} spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    input.stepUp();
    fireEvent.input(input);
    expect(spy).toHaveBeenLastCalledWith(6);
    expect(input.value).toBe('6');
  });
});

describe('NumberInput string mode bad input (round 2, S-B)', () => {
  function StringHarness({ spy }: { spy: (v: string) => void }) {
    const [v, setV] = useState<string>('5');
    return (
      <NumberInput
        aria-label="Qty"
        value={v}
        onChange={(e) => {
          spy(e.target.value);
          setV(e.target.value);
        }}
      />
    );
  }

  it('reports the string NaN to the parent when the browser flags bad input, and keeps the typed text', () => {
    const spy = vi.fn();
    render(<StringHarness spy={spy} />);
    const input = screen.getByLabelText('Qty') as HTMLInputElement;
    Object.defineProperty(input, 'validity', { value: { badInput: true }, configurable: true });
    fireEvent.change(input, { target: { value: '' } });
    expect(spy).toHaveBeenLastCalledWith('NaN');
  });

  it("given the value 'NaN' it never writes the literal into the box (which would wipe the typed text)", () => {
    const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!;
    const writes: string[] = [];
    Object.defineProperty(HTMLInputElement.prototype, 'value', {
      configurable: true,
      get: desc.get,
      set(v) {
        writes.push(String(v));
        desc.set!.call(this, v);
      },
    });
    try {
      render(<NumberInput aria-label="Qty" value="NaN" onChange={() => {}} />);
    } finally {
      Object.defineProperty(HTMLInputElement.prototype, 'value', desc);
    }
    expect(writes).not.toContain('NaN');
  });
});

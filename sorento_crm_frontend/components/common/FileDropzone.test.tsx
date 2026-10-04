import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { FileDropzone } from './FileDropzone';

function makeFile(name: string, sizeBytes = 10): File {
  const file = new File(['x'], name, { type: 'application/octet-stream' });
  Object.defineProperty(file, 'size', { value: sizeBytes });
  return file;
}

function dropOn(zone: HTMLElement, files: File[]) {
  fireEvent.drop(zone, { dataTransfer: { files } });
}

const zone = () => screen.getByRole('button', { name: /drop|browse|choose/i });

describe('FileDropzone', () => {
  it('hands a dropped file to the caller', () => {
    const onFilesChange = vi.fn();
    render(<FileDropzone files={[]} onFilesChange={onFilesChange} />);

    dropOn(zone(), [makeFile('po.pdf')]);

    expect(onFilesChange).toHaveBeenCalledTimes(1);
    expect(onFilesChange.mock.calls[0][0].map((f: File) => f.name)).toEqual(['po.pdf']);
  });

  it('replaces the held file when single, appends when multiple', () => {
    const single = vi.fn();
    const { rerender } = render(
      <FileDropzone files={[makeFile('first.pdf')]} onFilesChange={single} />,
    );
    dropOn(zone(), [makeFile('second.pdf')]);
    expect(single.mock.calls[0][0].map((f: File) => f.name)).toEqual(['second.pdf']);

    const many = vi.fn();
    rerender(<FileDropzone multiple files={[makeFile('first.pdf')]} onFilesChange={many} />);
    dropOn(zone(), [makeFile('second.pdf')]);
    expect(many.mock.calls[0][0].map((f: File) => f.name)).toEqual([
      'first.pdf',
      'second.pdf',
    ]);
  });

  it('refuses an extension outside accept and says why', () => {
    const onFilesChange = vi.fn();
    const onReject = vi.fn();
    render(
      <FileDropzone
        accept=".pdf,.jpg"
        files={[]}
        onFilesChange={onFilesChange}
        onReject={onReject}
      />,
    );

    dropOn(zone(), [makeFile('sheet.xlsx')]);

    expect(onFilesChange).not.toHaveBeenCalled();
    expect(onReject).toHaveBeenCalledWith(expect.objectContaining({ name: 'sheet.xlsx' }), 'type');
  });

  it('refuses a file over the size ceiling and says why', () => {
    const onFilesChange = vi.fn();
    const onReject = vi.fn();
    render(
      <FileDropzone
        maxSizeMb={1}
        files={[]}
        onFilesChange={onFilesChange}
        onReject={onReject}
      />,
    );

    dropOn(zone(), [makeFile('huge.pdf', 2 * 1024 * 1024)]);

    expect(onFilesChange).not.toHaveBeenCalled();
    expect(onReject).toHaveBeenCalledWith(expect.objectContaining({ name: 'huge.pdf' }), 'size');
  });

  it('silently drops macOS resource-fork siblings', () => {
    const onFilesChange = vi.fn();
    const onReject = vi.fn();
    render(
      <FileDropzone
        multiple
        files={[]}
        onFilesChange={onFilesChange}
        onReject={onReject}
      />,
    );

    dropOn(zone(), [makeFile('._po.pdf'), makeFile('po.pdf')]);

    expect(onReject).not.toHaveBeenCalled();
    expect(onFilesChange.mock.calls[0][0].map((f: File) => f.name)).toEqual(['po.pdf']);
  });

  it('lists what is held and removes the one the user picks', () => {
    const onFilesChange = vi.fn();
    render(
      <FileDropzone
        multiple
        files={[makeFile('a.pdf'), makeFile('b.pdf')]}
        onFilesChange={onFilesChange}
      />,
    );

    expect(screen.getByText('a.pdf')).toBeInTheDocument();
    expect(screen.getByText('b.pdf')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Remove a.pdf' }));

    expect(onFilesChange.mock.calls[0][0].map((f: File) => f.name)).toEqual(['b.pdf']);
  });

  it('says so when a single-file zone is handed more than one', () => {
    const onFilesChange = vi.fn();
    const onReject = vi.fn();
    render(<FileDropzone files={[]} onFilesChange={onFilesChange} onReject={onReject} />);

    dropOn(zone(), [makeFile('first.pdf'), makeFile('second.pdf'), makeFile('third.pdf')]);

    expect(onFilesChange.mock.calls[0][0].map((f: File) => f.name)).toEqual(['first.pdf']);
    expect(onReject).toHaveBeenCalledTimes(2);
    expect(onReject).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'second.pdf' }),
      'extra',
    );
  });

  it('lets a label bind to the file input', () => {
    render(
      <>
        <label htmlFor="doc">Document</label>
        <FileDropzone id="doc" files={[]} onFilesChange={vi.fn()} />
      </>,
    );

    expect(screen.getByLabelText('Document')).toHaveAttribute('type', 'file');
  });

  it('takes nothing while disabled', () => {
    const onFilesChange = vi.fn();
    render(<FileDropzone disabled files={[]} onFilesChange={onFilesChange} />);

    dropOn(zone(), [makeFile('po.pdf')]);

    expect(onFilesChange).not.toHaveBeenCalled();
  });

  it('opens the picker from the keyboard', () => {
    render(<FileDropzone files={[]} onFilesChange={vi.fn()} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const click = vi.spyOn(input, 'click');

    fireEvent.keyDown(zone(), { key: 'Enter' });

    expect(click).toHaveBeenCalled();
  });

  it('a long title wraps instead of truncating', () => {
    const long = 'Order Listing (macro), sheet Master with a very long configured sheet name';
    render(<FileDropzone files={[]} onFilesChange={vi.fn()} title={long} />);
    const title = screen.getByText(long);
    expect(title.className).toMatch(/\bbreak-words\b/);
    expect(title.className).not.toMatch(/\btruncate\b/);
  });
});

/**
 * FORM-COMMENTS: the shared thread (staff CRM page and contact portal).
 * States: loading / empty / data, composer rules, submitter badge, attachment buttons.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: ({
    open,
    items,
    startIndex,
  }: {
    open: boolean;
    items: { id: string; name: string }[];
    startIndex: number;
  }) =>
    open ? (
      <div data-testid="preview">
        {items.map((i) => i.name).join('|')}@{startIndex}
      </div>
    ) : null,
}));

import { FormCommentThread } from './FormCommentThread';
import type { FormComment } from '@/types/formComments';

const staff: FormComment = {
  id: 'c1',
  body: 'Please confirm the colour.',
  authorName: 'Alice Staff',
  authorKind: 'staff',
  postedByName: null,
  createdAt: '2026-10-01T09:00:00',
  attachments: [],
};
const submitter: FormComment = {
  id: 'c2',
  body: 'Blue, thanks.',
  authorName: 'Dan Dealer',
  authorKind: 'submitter',
  postedByName: null,
  createdAt: '2026-10-01T10:00:00',
  attachments: [
    {
      attachmentId: 'a1',
      filename: 'photo.jpg',
      size: 10,
      contentType: 'image/jpeg',
    },
    {
      attachmentId: 'a2',
      filename: 'quote.pdf',
      size: 20,
      contentType: 'application/pdf',
    },
  ],
};

function mount(
  over: Partial<React.ComponentProps<typeof FormCommentThread>> = {},
) {
  const onAdd = vi.fn().mockResolvedValue(undefined);
  const utils = render(
    <FormCommentThread
      comments={[]}
      isLoading={false}
      authorName="Pat Staff"
      readOnly={false}
      pending={false}
      emptyHint="Questions appear here."
      previewItem={(a) => ({ id: a.attachmentId, name: a.filename, url: '' })}
      onAdd={onAdd}
      {...over}
    />,
  );
  return { onAdd, ...utils };
}

describe('FormCommentThread', () => {
  it('shows the Comments header with the count badge', () => {
    mount({ comments: [staff, submitter] });
    expect(screen.getByRole('heading', { name: /Comments/ })).toHaveTextContent(
      'Comments2',
    );
  });

  it('shows the empty state with its hint', () => {
    mount();
    expect(screen.getByText('No comments yet')).toBeInTheDocument();
    expect(screen.getByText('Questions appear here.')).toBeInTheDocument();
  });

  it('shows skeletons, not the empty state, while loading', () => {
    mount({ isLoading: true });
    expect(screen.queryByText('No comments yet')).toBeNull();
  });

  it('renders each comment with author, body and a Submitter badge only on submitter comments', () => {
    mount({ comments: [staff, submitter] });
    expect(screen.getByText('Alice Staff')).toBeInTheDocument();
    expect(screen.getByText('Please confirm the colour.')).toBeInTheDocument();
    expect(screen.getByText('Dan Dealer')).toBeInTheDocument();
    expect(screen.getAllByText('Submitter')).toHaveLength(1);
  });

  it('offers a Write a comment composer whose Comment button needs text or a file', () => {
    mount();
    const button = screen.getByRole('button', { name: 'Comment' });
    expect(screen.getByPlaceholderText('Write a comment')).toBeInTheDocument();
    expect(button).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText('Write a comment'), {
      target: { value: '   ' },
    });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText('Write a comment'), {
      target: { value: 'hello' },
    });
    expect(button).toBeEnabled();
  });

  it('enables Comment for a file alone', async () => {
    mount();
    const input = screen.getByLabelText('Attach files to the comment');
    fireEvent.change(input, {
      target: {
        files: [new File(['x'], 'a.pdf', { type: 'application/pdf' })],
      },
    });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Comment' })).toBeEnabled(),
    );
  });

  it('hides the composer when readOnly but still lists the thread', () => {
    mount({ readOnly: true, comments: [staff] });
    expect(screen.queryByPlaceholderText('Write a comment')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Comment' })).toBeNull();
    expect(screen.getByText('Please confirm the colour.')).toBeInTheDocument();
  });

  it('submits the typed body and clears the composer on success', async () => {
    const { onAdd } = mount();
    const box = screen.getByPlaceholderText(
      'Write a comment',
    ) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'Thanks' } });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));
    await waitFor(() => expect(onAdd).toHaveBeenCalledWith('Thanks', []));
    await waitFor(() => expect(box.value).toBe(''));
  });

  it('keeps the typed text when the post fails', async () => {
    const onAdd = vi.fn().mockRejectedValue(new Error('nope'));
    mount({ onAdd });
    const box = screen.getByPlaceholderText(
      'Write a comment',
    ) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'Keep me' } });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));
    await waitFor(() => expect(onAdd).toHaveBeenCalled());
    expect(box.value).toBe('Keep me');
  });

  it('renders attachments as buttons with the filename and opens the preview at that file', () => {
    mount({ comments: [submitter] });
    expect(screen.queryByTestId('preview')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /quote\.pdf/ }));
    expect(screen.getByTestId('preview')).toHaveTextContent(
      'photo.jpg|quote.pdf@1',
    );
  });

  it('shows "Posted by <admin> on behalf" only on a comment posted on the contact\'s behalf', () => {
    mount({
      comments: [staff, { ...submitter, id: 'c3', postedByName: 'Admin Ann' }],
    });
    expect(
      screen.getByText('Posted by Admin Ann on behalf'),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/on behalf/)).toHaveLength(1);
  });
});

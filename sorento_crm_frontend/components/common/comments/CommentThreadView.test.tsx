/**
 * FORM-COMMENTS round 2 (red): the shared presentational thread, ideation's shape.
 * Contract: PLAN-form-comments-09oct.md, "Owner hand test round 2". The component does not exist
 * yet; the import below fails until it does.
 *
 * Props pinned: comments, isLoading, authorName, composerPosition ('top' | 'bottom'), readOnly,
 * onAdd(body, files, parentId?), onEdit(id, body), renderDelete(comment), previewItem,
 * fetchBytes, emptyHint, maxFiles. Labels follow IdeaComments: "Write a comment" / "Comment",
 * "Reply" / "Write a reply", "Edit" / "Edit comment" / "Save", "edited", "Comment deleted".
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: () => null,
}));

import { CommentThreadView } from './CommentThreadView';

type C = Record<string, unknown>;

function comment(over: C): C {
  return {
    id: 'x',
    kind: 'comment',
    parentId: null,
    body: 'body',
    authorName: 'Alice Staff',
    authorKind: 'staff',
    postedByName: null,
    createdAt: '2026-10-01T09:00:00',
    editedAt: null,
    deleted: false,
    canEdit: true,
    canDelete: true,
    canReply: true,
    attachments: [],
    ...over,
  };
}

const response = comment({
  id: 'response',
  kind: 'response',
  authorName: 'Rita Responder',
  body: 'brand?',
  createdAt: '2026-10-01T08:00:00',
  canEdit: false,
  canDelete: false,
  canReply: false,
});
const first = comment({ id: 'c1', body: 'First comment' });
const reply = comment({
  id: 'c2',
  parentId: 'c1',
  body: 'A reply',
  authorName: 'Dan Dealer',
  authorKind: 'submitter',
  canReply: false,
});

function mount(over: Record<string, unknown> = {}) {
  const onAdd = vi.fn().mockResolvedValue(undefined);
  const onEdit = vi.fn().mockResolvedValue(undefined);
  const renderDelete = vi.fn((c: { id: string }) => (
    <button type="button">Delete-{c.id}</button>
  ));
  const utils = render(
    <CommentThreadView
      comments={[response, first, reply] as never}
      isLoading={false}
      authorName="Pat Staff"
      composerPosition="bottom"
      readOnly={false}
      emptyHint="Nothing yet."
      previewItem={(a: { attachmentId: string; filename: string }) => ({
        id: a.attachmentId,
        name: a.filename,
        url: '',
      })}
      onAdd={onAdd}
      onEdit={onEdit}
      renderDelete={renderDelete}
      {...over}
    />,
  );
  return { onAdd, onEdit, renderDelete, ...utils };
}

describe('CommentThreadView', () => {
  it('renders the response entry first, then comments oldest first, replies under their parent', () => {
    mount();
    const order = ['brand?', 'First comment', 'A reply'].map((t) =>
      screen.getByText(t),
    );
    for (let i = 0; i < order.length - 1; i++) {
      expect(
        order[i].compareDocumentPosition(order[i + 1]) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    expect(screen.getByText('Rita Responder')).toBeInTheDocument();
  });

  it('puts the composer after the last comment when composerPosition is bottom', () => {
    mount();
    const lastComment = screen.getByText('A reply');
    const box = screen.getByPlaceholderText('Write a comment');
    expect(
      lastComment.compareDocumentPosition(box) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('puts the composer before the first comment when composerPosition is top', () => {
    mount({ composerPosition: 'top' });
    const box = screen.getByPlaceholderText('Write a comment');
    expect(
      box.compareDocumentPosition(screen.getByText('First comment')) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('hides the composer when readOnly', () => {
    mount({ readOnly: true });
    expect(screen.queryByPlaceholderText('Write a comment')).toBeNull();
  });

  it('shows the empty state when there are no comments', () => {
    mount({ comments: [] });
    expect(screen.getByText('No comments yet')).toBeInTheDocument();
    expect(screen.getByText('Nothing yet.')).toBeInTheDocument();
  });

  it('Reply opens an inline composer and posts with the parent id', async () => {
    const { onAdd } = mount();
    fireEvent.click(screen.getAllByRole('button', { name: 'Reply' })[0]);
    const box = screen.getByPlaceholderText('Write a reply');
    fireEvent.change(box, { target: { value: 'my answer' } });
    const buttons = screen.getAllByRole('button', { name: 'Reply' });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() =>
      expect(onAdd).toHaveBeenCalledWith('my answer', [], 'c1'),
    );
  });

  it('offers Reply only where canReply is true', () => {
    mount({ comments: [response, first, reply] });
    // response: no; first: yes; reply (second level): no.
    expect(screen.getAllByRole('button', { name: 'Reply' })).toHaveLength(1);
  });

  it('Edit swaps in a prefilled composer and Save calls onEdit', async () => {
    const { onEdit } = mount({ comments: [first] });
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const box = screen.getByPlaceholderText(
      'Edit comment',
    ) as HTMLTextAreaElement;
    expect(box.value).toBe('First comment');
    fireEvent.change(box, { target: { value: 'First comment, fixed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(onEdit).toHaveBeenCalledWith('c1', 'First comment, fixed'),
    );
  });

  it('shows no Edit button when canEdit is false', () => {
    mount({ comments: [comment({ id: 'c9', canEdit: false })] });
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
  });

  it('shows an edited badge only on edited comments', () => {
    mount({
      comments: [
        first,
        comment({ id: 'c5', body: 'tweaked', editedAt: '2026-10-02T09:00:00' }),
      ],
    });
    expect(screen.getAllByText('edited')).toHaveLength(1);
  });

  it('shows a Comment deleted placeholder for a deleted comment that still has replies', () => {
    mount({
      comments: [
        comment({
          id: 'c1',
          deleted: true,
          body: '',
          canEdit: false,
          canDelete: false,
          canReply: false,
        }),
        reply,
      ],
    });
    expect(screen.getByText('Comment deleted')).toBeInTheDocument();
    expect(screen.getByText('A reply')).toBeInTheDocument();
  });

  it('renders the response entry without Reply, Edit or Delete', () => {
    const { renderDelete } = mount({ comments: [response] });
    expect(screen.queryByRole('button', { name: 'Reply' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.queryByText('Delete-response')).toBeNull();
    expect(renderDelete).not.toHaveBeenCalledWith(
      expect.objectContaining({ id: 'response' }),
    );
  });

  it('renders the delete control the host supplies for deletable comments', () => {
    mount({ comments: [first] });
    expect(screen.getByText('Delete-c1')).toBeInTheDocument();
  });

  it('still shows "Posted by X on behalf" on a comment posted for a contact', () => {
    mount({
      comments: [
        comment({
          id: 'c7',
          authorKind: 'submitter',
          authorName: 'Dan Dealer',
          postedByName: 'Admin Ann',
        }),
      ],
    });
    expect(
      screen.getByText('Posted by Admin Ann on behalf'),
    ).toBeInTheDocument();
    expect(screen.getByText('Submitter')).toBeInTheDocument();
  });

  it('renders attachments as filename buttons on a comment', () => {
    mount({
      comments: [
        comment({
          id: 'c8',
          attachments: [
            {
              attachmentId: 'a1',
              filename: 'quote.pdf',
              size: 1,
              contentType: 'application/pdf',
            },
          ],
        }),
      ],
    });
    expect(
      screen.getByRole('button', { name: /quote\.pdf/ }),
    ).toBeInTheDocument();
  });
});

/**
 * FORM-COMMENTS review round (red). Assumed API, using the props that already exist:
 *  - `isDeleting(comment) => boolean` is true while that comment's delete countdown runs. For that
 *    row Reply and Edit are hidden and the row is dimmed (`opacity-60`), exactly as IdeaComments
 *    behaved before the shared thread. `renderDelete` still renders (it carries the countdown).
 *  - The header count ("Comments N") excludes the synthetic `kind: 'response'` entry.
 *  - A null `createdAt` (a reply with no recorded time) renders no date text at all.
 */
describe('CommentThreadView review round', () => {
  it('F1: while a delete countdown runs, Reply and Edit are hidden for that row and it is dimmed', () => {
    mount({ isDeleting: (c: { id: string }) => c.id === 'c1' });
    // c1 is the only row with canReply; c2 can still be edited, c1 cannot.
    expect(screen.queryAllByRole('button', { name: 'Reply' })).toHaveLength(0);
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(1);
    expect(screen.getByText('Delete-c1')).toBeInTheDocument();
    const row = screen.getByText('First comment').closest('.opacity-60');
    expect(row).not.toBeNull();
    expect(screen.getByText('A reply').closest('.opacity-60')).toBeNull();
  });

  it('F1: with no countdown running Reply and Edit show for the row', () => {
    mount({ isDeleting: () => false });
    expect(screen.getAllByRole('button', { name: 'Reply' })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(2);
  });

  it('F2: the header count leaves out the synthetic response entry', () => {
    mount({ comments: [response, first] as never });
    const heading = screen.getByRole('heading', { name: /Comments/ });
    expect(heading.textContent?.replace(/\s+/g, '')).toBe('Comments1');
  });

  it('B5: an entry with a null createdAt renders no date text', () => {
    mount({
      comments: [{ ...response, createdAt: null }] as never,
    });
    expect(screen.getByText('brand?')).toBeInTheDocument();
    const author = screen.getByText('Rita Responder');
    expect(author.parentElement?.textContent).toBe('Rita Responder');
    expect(screen.queryByText(/Invalid|NaN|1970/)).toBeNull();
  });
  it('COMMENT-TZ: a naive UTC createdAt from the API renders in Malaysia time (08:26 UTC = 4:26 pm)', () => {
    mount({
      comments: [{ ...first, createdAt: '2026-10-09T08:26:20' }] as never,
    });
    const author = screen.getByText('Alice Staff');
    expect(author.parentElement?.textContent).toContain('09/10/2026, 4:26 pm');
    expect(author.parentElement?.textContent).not.toMatch(/8:26/);
  });

  it('COMMENT-TZ: an explicit +00:00 offset renders the same Malaysia time', () => {
    mount({
      comments: [{ ...first, createdAt: '2026-10-09T08:26:20+00:00' }] as never,
    });
    expect(screen.getByText('Alice Staff').parentElement?.textContent).toContain('09/10/2026, 4:26 pm');
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

import {
  addFormComment,
  formCommentFormData,
  listFormComments,
  toFormComment,
} from './formCommentsService';

const raw = {
  id: 'c1',
  body: 'hi',
  author_name: 'Alice',
  author_kind: 'submitter',
  created_at: '2026-10-01T09:00:00',
  attachments: [
    {
      attachment_id: 'a1',
      filename: 'x.pdf',
      size: 12,
      content_type: 'application/pdf',
    },
  ],
};

function ok(json: unknown, status = 200) {
  return { ok: true, status, json: async () => json } as unknown as Response;
}

beforeEach(() => apiFetch.mockReset());

describe('toFormComment', () => {
  it('maps snake_case to the FE shape', () => {
    expect(toFormComment(raw)).toEqual({
      id: 'c1',
      kind: 'comment',
      parentId: null,
      body: 'hi',
      authorName: 'Alice',
      authorKind: 'submitter',
      postedByName: null,
      createdAt: '2026-10-01T09:00:00',
      editedAt: null,
      deleted: false,
      canEdit: false,
      canDelete: false,
      canReply: false,
      attachments: [
        {
          attachmentId: 'a1',
          filename: 'x.pdf',
          size: 12,
          contentType: 'application/pdf',
        },
      ],
    });
  });

  it('maps the round-2 thread fields (response entry, reply, edited, deleted, viewer flags)', () => {
    const c = toFormComment({
      ...raw,
      kind: 'response',
      parent_id: 'p1',
      edited_at: '2026-10-02T09:00:00',
      deleted: true,
      can_edit: true,
      can_delete: true,
      can_reply: true,
    });
    expect(c).toMatchObject({
      kind: 'response',
      parentId: 'p1',
      editedAt: '2026-10-02T09:00:00',
      deleted: true,
      canEdit: true,
      canDelete: true,
      canReply: true,
    });
  });

  it("maps posted_by_name for a comment an admin posted on the contact's behalf", () => {
    expect(
      toFormComment({ ...raw, posted_by_name: 'Admin Ann' }).postedByName,
    ).toBe('Admin Ann');
  });

  it('defaults a missing kind to staff and missing attachments to []', () => {
    const c = toFormComment({
      id: 'c2',
      body: 'b',
      author_name: 'A',
      created_at: 't',
    });
    expect(c.authorKind).toBe('staff');
    expect(c.attachments).toEqual([]);
  });
});

describe('formCommentFormData', () => {
  it('appends body and every file under the repeated key "files"', () => {
    const a = new File(['a'], 'a.pdf');
    const b = new File(['b'], 'b.pdf');
    const form = formCommentFormData('text', [a, b]);
    expect(form.get('body')).toBe('text');
    expect(form.getAll('files')).toHaveLength(2);
    expect((form.getAll('files')[1] as File).name).toBe('b.pdf');
  });
});

describe('listFormComments / addFormComment', () => {
  it('lists from GET /api/v1/form-comments/{kind}/{id} and maps items', async () => {
    apiFetch.mockResolvedValue(ok({ items: [raw] }));
    const items = await listFormComments('stock_inquiry', 'abc');
    expect(apiFetch).toHaveBeenCalledWith(
      '/api/v1/form-comments/stock_inquiry/abc',
    );
    expect(items.map((c) => c.id)).toEqual(['c1']);
  });

  it('posts multipart FormData (no JSON body) and maps {comment, delivery}', async () => {
    apiFetch.mockResolvedValue(
      ok({ comment: raw, delivery: 'no_whatsapp' }, 201),
    );
    const result = await addFormComment('complaint', 'z', 'hello', [
      new File(['x'], 'x.pdf'),
    ]);
    const [url, init] = apiFetch.mock.calls[0];
    expect(url).toBe('/api/v1/form-comments/complaint/z');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get('body')).toBe('hello');
    expect(result.delivery).toBe('no_whatsapp');
    expect(result.comment.authorName).toBe('Alice');
  });

  it('throws the server message on a failed post', async () => {
    apiFetch.mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: { message: 'Comment is empty' } }),
    } as unknown as Response);
    await expect(addFormComment('complaint', 'z', '')).rejects.toThrow();
  });
});

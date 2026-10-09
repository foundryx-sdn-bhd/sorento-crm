/**
 * Public file link reader, for the short links the chatbot sends instead of attachments.
 *
 * Deliberately NOT `apiFetch`: whoever opens a link has no session to resolve.
 *
 * ---------------------------------------------------------------------------
 * CONTRACT
 *
 * GET /api/v1/public/file-links/{code}
 *     200 -> { filename, mime_type, kind: "sheet" | "file", url }
 *            `url` is a one hour signed link for "file", null for "sheet".
 *     404 -> { detail: "This link has expired." }
 *     429 -> too many requests
 * GET /api/v1/public/file-links/{code}/content
 *     -> the file bytes, only for "sheet".
 * ---------------------------------------------------------------------------
 */

export interface FileLink {
  filename: string;
  mime_type: string | null;
  kind: 'sheet' | 'file';
  url: string | null;
}

export type FileLinkResult =
  | { status: 'ok'; link: FileLink }
  | { status: 'expired' }
  | { status: 'error' };

function apiBase(): string {
  const env = process.env.NEXT_PUBLIC_API_URL;
  return env ? env.replace(/\/$/, '') : '';
}

function linkPath(code: string): string {
  return `/api/v1/public/file-links/${encodeURIComponent(code)}`;
}

/**
 * The bytes route goes to the API host directly: the Next.js `/api/v1/public` catch-all
 * re-serializes every response as JSON and would hand back `{}` for a spreadsheet
 * (same reason as `app/(auth)/view/lib/attachmentDownload.ts`).
 */
function absoluteApiUrl(path: string): string {
  const base = apiBase();
  if (base) return `${base}${path}`;
  if (typeof window !== 'undefined') {
    const port = window.location.port;
    if (port === '3000' || port === '3001') {
      return `${window.location.protocol}//${window.location.hostname}:8000${path}`;
    }
  }
  return path;
}

export async function readFileLink(code: string): Promise<FileLinkResult> {
  try {
    // Direct to the API host, like the bytes route: the Next `/api/v1/public` proxy does not
    // forward the client address, so every opener would share one rate-limit bucket.
    const response = await fetch(absoluteApiUrl(linkPath(code)));
    if (response.status === 404) return { status: 'expired' };
    if (!response.ok) return { status: 'error' };
    return { status: 'ok', link: (await response.json()) as FileLink };
  } catch {
    return { status: 'error' };
  }
}

/** `fetchBytes` for AttachmentPreviewModal and the Download button on a sheet link. */
export function fetchFileLinkContent(code: string): Promise<Response> {
  return fetch(absoluteApiUrl(`${linkPath(code)}/content`));
}

export function fileLinkContentPath(code: string): string {
  return `${linkPath(code)}/content`;
}

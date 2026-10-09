'use client';

/**
 * A chatbot file link, at `/f/{code}`. No login, no CRM chrome.
 *
 * A "file" link hands the browser straight to the signed file. A "sheet" link opens the
 * spreadsheet read-only in the browser, with a Download button.
 */

import { use, useCallback, useEffect, useState } from 'react';
import { Download, Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import {
  fetchFileLinkContent,
  fileLinkContentPath,
  readFileLink,
  type FileLink,
} from './fileLinkService';

type State =
  | { status: 'loading' }
  | { status: 'expired' }
  | { status: 'error' }
  | { status: 'ok'; link: FileLink };

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-screen w-full flex-col items-center justify-center gap-4 px-4 py-10 text-center">
      {children}
    </main>
  );
}

export default function FileLinkPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const [state, setState] = useState<State>({ status: 'loading' });
  const [previewOpen, setPreviewOpen] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [downloadFailed, setDownloadFailed] = useState(false);

  const load = useCallback(async () => {
    setState({ status: 'loading' });
    const result = await readFileLink(code);
    if (result.status === 'ok') {
      if (result.link.kind === 'file' && result.link.url) {
        window.location.replace(result.link.url);
      }
      setState({ status: 'ok', link: result.link });
    } else {
      setState({ status: result.status });
    }
  }, [code]);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.status === 'loading') {
    return (
      <Centered>
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-label="Loading" />
      </Centered>
    );
  }

  if (state.status === 'expired') {
    return (
      <Centered>
        <p className="max-w-sm text-base font-medium">
          This link has expired. Ask us on WhatsApp for a new one.
        </p>
      </Centered>
    );
  }

  if (state.status === 'error') {
    return (
      <Centered>
        <p className="max-w-sm text-base font-medium">Something went wrong. Please try again.</p>
        <Button onClick={() => void load()}>Retry</Button>
      </Centered>
    );
  }

  const { link } = state;

  if (link.kind === 'file') {
    return (
      <Centered>
        <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden="true" />
        <p className="max-w-sm break-words text-sm text-muted-foreground">
          Opening {link.filename}...
        </p>
      </Centered>
    );
  }

  const item: AttachmentPreviewItem = {
    id: code,
    name: link.filename,
    url: '',
    downloadUrl: fileLinkContentPath(code),
  };
  const fetchBytes = () => fetchFileLinkContent(code);

  const download = async () => {
    setDownloading(true);
    setDownloadFailed(false);
    try {
      const response = await fetchFileLinkContent(code);
      if (!response.ok) throw new Error('Download failed');
      const objectUrl = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = link.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch {
      setDownloadFailed(true);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Centered>
      <p className="max-w-sm break-words text-base font-medium">{link.filename}</p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button onClick={() => setPreviewOpen(true)}>View</Button>
        <Button variant="outline" disabled={downloading} onClick={() => void download()}>
          {downloading ? <Loader2 className="animate-spin" /> : <Download />}
          Download
        </Button>
      </div>
      {downloadFailed ? (
        <p className="text-sm text-destructive">Something went wrong. Please try again.</p>
      ) : null}
      <AttachmentPreviewModal
        open={previewOpen}
        onOpenChange={setPreviewOpen}
        items={[item]}
        fetchBytes={fetchBytes}
      />
    </Centered>
  );
}

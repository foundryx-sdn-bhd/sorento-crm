'use client';

/**
 * A chatbot file link, at `/f/{code}`. No login, no CRM chrome.
 *
 * A "file" link hands the browser straight to the signed file. A "sheet" link opens the
 * spreadsheet read-only in the browser, with a Download button.
 */

import { use, useCallback, useEffect, useRef, useState } from 'react';
import { Download, Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { SpreadsheetViewer } from '@/components/common/SpreadsheetViewer';
import { parseWorkbookInWorker } from '@/lib/sheet-viewer/parseWorkbookInWorker';
import type { SpreadsheetWorkbook } from '@/lib/sheet-viewer/types';
import {
  fetchFileLinkContent,
  fetchFileLinkDownload,
  readFileLink,
  type FileLink,
} from './fileLinkService';

type State =
  | { status: 'loading' }
  | { status: 'expired' }
  | { status: 'error' }
  | { status: 'ok'; link: FileLink };

function isHttpsUrl(url: string | null): boolean {
  if (!url) return false;
  try {
    return new URL(url).protocol === 'https:';
  } catch {
    return false;
  }
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <main className="flex min-h-dvh w-full flex-col items-center justify-center gap-4 px-4 py-10 text-center">
      {children}
    </main>
  );
}

type SheetState =
  | { status: 'loading' }
  | { status: 'tooBig' }
  | { status: 'cannot' }
  | { status: 'ready'; workbook: SpreadsheetWorkbook };

/** A sheet link: the file opens straight into the viewer, with Download in the top bar. */
function SheetLinkView({ code, filename }: { code: string; filename: string }) {
  const [state, setState] = useState<SheetState>({ status: 'loading' });
  const [activeTitle, setActiveTitle] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadFailed, setDownloadFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetchFileLinkContent(code);
        if (cancelled) return;
        if (response.status === 413) return setState({ status: 'tooBig' });
        if (!response.ok) return setState({ status: 'cannot' });
        const workbook = await parseWorkbookInWorker(await response.arrayBuffer(), filename);
        if (!cancelled) setState({ status: 'ready', workbook });
      } catch {
        if (!cancelled) setState({ status: 'cannot' });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, filename]);

  const download = async () => {
    setDownloading(true);
    setDownloadFailed(false);
    try {
      const response = await fetchFileLinkDownload(code);
      if (!response.ok) throw new Error('Download failed');
      const objectUrl = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = filename;
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

  const shown =
    state.status === 'ready'
      ? (state.workbook.sheets.find((s) => s.title === activeTitle) ?? state.workbook.sheets[0])
      : null;
  const note = shown?.truncatedRows
    ? 'Showing first 5,000 rows. Download for the full sheet.'
    : shown?.truncatedCols
      ? 'Showing first 100 columns. Download for the full sheet.'
      : null;

  return (
    <main className="flex h-dvh w-full min-w-0 flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-border px-3 py-2">
        <h1 className="min-w-0 flex-1 truncate text-sm font-medium" title={filename}>
          {filename}
        </h1>
        <Button
          variant="outline"
          size="sm"
          aria-label="Download"
          disabled={downloading}
          onClick={() => void download()}
        >
          {downloading ? <Loader2 className="animate-spin" /> : <Download />}
          <span className="hidden sm:inline">Download</span>
        </Button>
      </header>
      {downloadFailed ? (
        <p className="px-3 py-1.5 text-sm text-destructive">Something went wrong. Please try again.</p>
      ) : null}
      {state.status === 'loading' ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="size-6 animate-spin text-muted-foreground" aria-label="Loading" />
        </div>
      ) : null}
      {state.status === 'tooBig' || state.status === 'cannot' ? (
        <div className="flex flex-1 items-center justify-center px-4 text-center">
          <p className="max-w-sm text-base font-medium">
            {state.status === 'tooBig'
              ? 'This file is too big to show here.'
              : 'This file cannot be shown here.'}
          </p>
        </div>
      ) : null}
      {state.status === 'ready' ? (
        <>
          <SpreadsheetViewer
            workbook={state.workbook}
            activeSheet={shown?.title}
            onActiveSheetChange={setActiveTitle}
            searchMode="highlight"
            tabsPosition="bottom"
            className="min-h-0 flex-1"
          />
          {note ? (
            <p className="shrink-0 border-t border-border px-3 py-1.5 text-xs text-muted-foreground">
              {note}
            </p>
          ) : null}
        </>
      ) : null}
    </main>
  );
}

export default function FileLinkPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const [state, setState] = useState<State>({ status: 'loading' });

  const load = useCallback(async () => {
    setState({ status: 'loading' });
    const result = await readFileLink(code);
    if (result.status === 'ok') {
      if (result.link.kind === 'file') {
        // Only ever hand the browser an https file url (security review L2).
        if (!isHttpsUrl(result.link.url)) {
          setState({ status: 'error' });
          return;
        }
        window.location.replace(result.link.url as string);
      }
      setState({ status: 'ok', link: result.link });
    } else {
      setState({ status: result.status });
    }
  }, [code]);

  // Once per page: every read counts a click, and a double-run effect would count two.
  const loaded = useRef(false);
  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
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

  return <SheetLinkView code={code} filename={link.filename} />;
}

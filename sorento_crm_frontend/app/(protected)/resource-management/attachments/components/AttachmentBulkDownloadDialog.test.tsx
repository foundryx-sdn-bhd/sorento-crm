/**
 * AttachmentBulkDownloadDialog (FOLDER-BULK-DOWNLOAD, UAC AC-D8, AC-D9, AC-F3, AC-F4).
 * Opening starts the job at once; the 2 s poll drives processing / finished / failed /
 * cancelled renders. Service modules are mocked; timers are fake.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../services/attachmentService', () => ({
  startBulkDownload: vi.fn(),
  getJobStatus: vi.fn(),
  cancelJob: vi.fn(),
}));
vi.mock('@/services/myDownloadsService', () => ({
  fetchDownloadUrl: vi.fn(),
  downloadFilePath: (id: string) => `/api/v1/downloads/${id}/file`,
}));

import { toast } from '@/lib/toast';
import { fetchDownloadUrl } from '@/services/myDownloadsService';
import { cancelJob, getJobStatus, startBulkDownload } from '../services/attachmentService';
import AttachmentBulkDownloadDialog from './AttachmentBulkDownloadDialog';

const start = startBulkDownload as unknown as ReturnType<typeof vi.fn>;
const status = getJobStatus as unknown as ReturnType<typeof vi.fn>;
const cancel = cancelJob as unknown as ReturnType<typeof vi.fn>;
const fetchUrl = fetchDownloadUrl as unknown as ReturnType<typeof vi.fn>;

const FINISHED = {
  status: 'finished',
  progress: { processed: 1378, total: 1378, percentage: 100 },
  result: {
    archive_filename: 'Marketing-20261007-1412.zip',
    files_included: 1376,
    folders: 130,
    bytes: 1717986918,
    files_failed: 2,
    files_not_visible: 7,
    download_id: 'd1',
  },
};

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function open(poll: unknown) {
  status.mockResolvedValue(poll);
  render(
    <AttachmentBulkDownloadDialog
      open
      onOpenChange={vi.fn()}
      directoryId="dir-1"
      folderIds={['f1', 'f2']}
      attachmentIds={['a1']}
    />,
  );
  await flush(0);
  await flush(2100);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.stubGlobal('open', vi.fn());
  start.mockResolvedValue({
    job_id: 'job-rq-1',
    id: 'job-db-1',
    download_id: 'd1',
    message: 'started',
  });
  cancel.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('AttachmentBulkDownloadDialog', () => {
  it('D8: opening starts the job once with the three ids and shows Preparing archive', async () => {
    await open({ status: 'queued' });
    expect(start).toHaveBeenCalledTimes(1);
    expect(start).toHaveBeenCalledWith({
      directoryId: 'dir-1',
      folderIds: ['f1', 'f2'],
      attachmentIds: ['a1'],
    });
    expect(screen.getByRole('heading', { name: 'Download selected' })).toBeInTheDocument();
    expect(screen.getByText(/Preparing archive/)).toBeInTheDocument();
  });

  it('D2/D8: processing shows files done / total, a percentage and a progressbar', async () => {
    await open({ status: 'started', progress: { processed: 412, total: 1378, percentage: 29 } });
    expect(screen.getByText(/Files: 412 \/ 1,378/)).toBeInTheDocument();
    expect(screen.getByText(/29%/)).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('D8/F3: finished shows the summary and Download ZIP opens the signed URL', async () => {
    fetchUrl.mockResolvedValue({ url: 'https://cdn.test/signed.zip', filename: 'x.zip' });
    await open(FINISHED);
    expect(screen.getByText(/Archive ready/)).toBeInTheDocument();
    expect(screen.getByText(/1,376 files in 130 folders/)).toBeInTheDocument();
    expect(screen.getByText(/2 files could not be read/)).toBeInTheDocument();
    expect(screen.getByText(/7 files not included \(outside your companies\)/)).toBeInTheDocument();
    expect(screen.getByText(/Also listed in My downloads/)).toBeInTheDocument();
    expect(document.querySelector('[class*="animate-spin"]')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Download ZIP' }));
    await flush(0);
    expect(fetchUrl).toHaveBeenCalledWith('d1');
    const openMock = window.open as unknown as ReturnType<typeof vi.fn>;
    expect(openMock).toHaveBeenCalled();
    expect(openMock.mock.calls[0][0]).toBe('https://cdn.test/signed.zip');
  });

  it('D8: finished with nothing skipped shows neither warning line', async () => {
    await open({
      ...FINISHED,
      result: { ...FINISHED.result, files_failed: 0, files_not_visible: 0 },
    });
    expect(screen.getByText(/Archive ready/)).toBeInTheDocument();
    expect(screen.queryByText(/could not be read/)).toBeNull();
    expect(screen.queryByText(/not included/)).toBeNull();
  });

  it('D8: failed shows the error and only Close (no Download ZIP, no Cancel)', async () => {
    await open({ status: 'failed', error: 'No file could be read' });
    expect(screen.getByText(/Archive failed/)).toBeInTheDocument();
    expect(screen.getByText(/No file could be read/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Download ZIP/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Cancel/ })).toBeNull();
    expect(screen.getAllByRole('button', { name: /^Close$/i }).length).toBeGreaterThan(0);
  });

  it('D8: cancelled shows Download cancelled', async () => {
    await open({ status: 'cancelled' });
    expect(screen.getByText(/Download cancelled/)).toBeInTheDocument();
  });

  it('D6: while processing, Cancel download calls cancelJob with the job id', async () => {
    await open({ status: 'started', progress: { processed: 1, total: 10, percentage: 10 } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel download' }));
    await flush(0);
    expect(cancel).toHaveBeenCalledWith('job-rq-1');
  });

  it('D9: a poll rejection keeps the processing render and the next tick polls again', async () => {
    status.mockRejectedValueOnce(new Error('network'));
    status.mockResolvedValue({
      status: 'started',
      progress: { processed: 412, total: 1378, percentage: 29 },
    });
    render(
      <AttachmentBulkDownloadDialog
        open
        onOpenChange={vi.fn()}
        directoryId={null}
        folderIds={['f1']}
        attachmentIds={[]}
      />,
    );
    await flush(0);
    await flush(2100);
    expect(screen.getByText(/Preparing archive/)).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
    const callsAfterFirst = status.mock.calls.length;
    await flush(2100);
    expect(status.mock.calls.length).toBeGreaterThan(callsAfterFirst);
    expect(screen.getByText(/Files: 412 \/ 1,378/)).toBeInTheDocument();
  });

  it('D8/D9: finished without a result renders the failed state', async () => {
    await open({
      status: 'finished',
      progress: { processed: 0, total: 0, percentage: 100 },
      result: null,
      error: null,
    });
    expect(screen.getByText(/Archive failed/)).toBeInTheDocument();
    expect(screen.getByText(/The archive could not be built/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Download ZIP/ })).toBeNull();
    expect(document.querySelector('[class*="animate-spin"]')).toBeNull();
  });
});

'use client';

import { useEffect, useRef, useState } from 'react';
import { CheckCircle, Loader2, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Progress } from '@/components/ui/progress';
import { toast } from '@/lib/toast';
import { fetchDownloadUrl } from '@/services/myDownloadsService';
import { cancelJob, getJobStatus, startBulkDownload } from '../services/attachmentService';
import type { JobStatusResponse } from '../services/attachmentService';

interface AttachmentBulkDownloadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The folder being viewed (null = drive root); archive paths are relative to it. */
  directoryId: string | null;
  folderIds: string[];
  attachmentIds: string[];
}

const POLL_MS = 2000;
const TERMINAL = ['finished', 'failed', 'cancelled'];

function formatArchiveSize(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
}

const n = (value: number) => value.toLocaleString('en-US');

export default function AttachmentBulkDownloadDialog({
  open,
  onOpenChange,
  directoryId,
  folderIds,
  attachmentIds,
}: AttachmentBulkDownloadDialogProps) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatusResponse | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [isOpeningUrl, setIsOpeningUrl] = useState(false);
  const startedRef = useRef(false);

  // Start once per open; the ref keeps StrictMode's double effect from creating two jobs.
  useEffect(() => {
    if (!open) {
      startedRef.current = false;
      setJobId(null);
      setJobStatus(null);
      setStartError(null);
      setIsOpeningUrl(false);
      return;
    }
    if (startedRef.current) return;
    startedRef.current = true;
    startBulkDownload({ directoryId, folderIds, attachmentIds })
      .then((res) => setJobId(res.job_id))
      .catch((err: Error) => {
        setStartError(err.message || 'Failed to start download');
        toast.error(err.message || 'Failed to start download');
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the selection is captured at open
  }, [open]);

  // Poll every 2 s until a terminal state; a rejected poll is ignored and the next tick retries.
  useEffect(() => {
    if (!open || !jobId) return;
    let stopped = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    const poll = async () => {
      try {
        const data = await getJobStatus(jobId);
        if (stopped) return;
        setJobStatus(data);
        if (TERMINAL.includes(data.status) && timer) {
          clearInterval(timer);
          timer = null;
        }
      } catch {
        // retried on the next tick
      }
    };
    poll();
    timer = setInterval(poll, POLL_MS);
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
    };
  }, [open, jobId]);

  const handleCancel = async () => {
    if (!jobId) return;
    try {
      await cancelJob(jobId);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to cancel download');
    }
  };

  const handleDownloadZip = async () => {
    const downloadId = jobStatus?.result?.download_id;
    if (!downloadId) return;
    setIsOpeningUrl(true);
    try {
      const { url } = await fetchDownloadUrl(downloadId);
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to open the archive');
    } finally {
      setIsOpeningUrl(false);
    }
  };

  const result = jobStatus?.result;
  // A task that failed and returned normally is reported by RQ as finished: no download id, no archive.
  const finishedWithoutArchive = jobStatus?.status === 'finished' && !result?.download_id;
  const status = startError || finishedWithoutArchive ? 'failed' : (jobStatus?.status ?? 'pending');
  const progress = jobStatus?.progress;
  const failedLine = startError ?? (
    finishedWithoutArchive
      ? ['The archive could not be built.', jobStatus?.error].filter(Boolean).join(' ')
      : jobStatus?.error
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Download selected</DialogTitle>
          {result?.archive_filename && (
            <DialogDescription className="truncate" title={result.archive_filename}>
              {result.archive_filename}
            </DialogDescription>
          )}
        </DialogHeader>

        {!TERMINAL.includes(status) && !startError && (
          <div className="space-y-4 py-2">
            <div className="flex items-center gap-3">
              <Loader2 className="size-5 animate-spin text-primary" />
              <span className="text-sm font-medium">Preparing archive in the background…</span>
            </div>
            {progress && (
              <div className="space-y-2">
                <div className="flex justify-between text-sm text-muted-foreground">
                  <span>
                    Files: {n(progress.processed)} / {n(progress.total)}
                  </span>
                  <span>{progress.percentage}%</span>
                </div>
                <Progress value={progress.percentage} className="h-2" />
              </div>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={handleCancel} disabled={!jobId}>
                Cancel download
              </Button>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
            </DialogFooter>
          </div>
        )}

        {status === 'finished' && (
          <div className="space-y-4 py-2">
            <div className="flex items-center gap-3 text-green-600">
              <CheckCircle className="size-5" />
              <span className="font-medium">Archive ready</span>
            </div>
            {result && (
              <div className="space-y-1 text-sm text-muted-foreground">
                <p>
                  {n(result.files_included ?? 0)} files in {n(result.folders ?? 0)} folders,{' '}
                  {formatArchiveSize(result.bytes ?? 0)}
                </p>
                {(result.files_failed ?? 0) > 0 && (
                  <p className="text-amber-600">
                    {n(result.files_failed ?? 0)} files could not be read and were skipped
                  </p>
                )}
                {(result.files_not_visible ?? 0) > 0 && (
                  <p className="text-amber-600">
                    {n(result.files_not_visible ?? 0)} files not included (outside your companies)
                  </p>
                )}
                <p>
                  Also listed in My downloads (top bar). Link valid for 1 hour; the archive is
                  removed after 24 hours.
                </p>
              </div>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
              <Button onClick={handleDownloadZip} disabled={isOpeningUrl || !result?.download_id}>
                Download ZIP
              </Button>
            </DialogFooter>
          </div>
        )}

        {status === 'failed' && (
          <div className="space-y-4 py-2">
            <div className="flex items-center gap-3 text-destructive">
              <XCircle className="size-5" />
              <span className="font-medium">Archive failed</span>
            </div>
            {failedLine && <p className="text-sm text-muted-foreground">{failedLine}</p>}
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)}>Close</Button>
            </DialogFooter>
          </div>
        )}

        {status === 'cancelled' && (
          <div className="space-y-4 py-2">
            <div className="flex items-center gap-3 text-muted-foreground">
              <XCircle className="size-5" />
              <span className="font-medium">Download cancelled</span>
            </div>
            <p className="text-sm text-muted-foreground">
              Nothing was saved. Select the folders again to start a new download.
            </p>
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)}>Close</Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

'use client';

import { useEffect, useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { FileDropzone } from '@/components/common/FileDropzone';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useIdeaMutations } from '@/hooks/useIdeas';

/**
 * Capture idea: the CRM modal over ss `IdeaCreateIn`. No product picker (the connection is scoped
 * to the workspace's product); files go up after the idea exists. Proposed solution, Impact and
 * Department are retired from capture (IDEA-CORE); `problem` is labelled Business problem.
 */
export function IdeaCaptureModal({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { create } = useIdeaMutations();
  const [problem, setProblem] = useState('');
  const [rawText, setRawText] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (!open) return;
    setProblem('');
    setRawText('');
    setFiles([]);
    setTouched(false);
  }, [open]);

  const problemError = touched && problem.trim().length === 0 ? 'Business problem is required' : null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!problem.trim() || create.isPending) return;
    try {
      await create.mutateAsync({ problem, rawText, files });
      onOpenChange(false);
    } catch {
      // The hook toasted the reason; the modal stays open with what was typed.
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Capture idea</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogBody className="flex max-h-[70dvh] flex-col gap-4 overflow-y-auto">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="idea-problem">Business problem</Label>
              <Textarea
                id="idea-problem"
                value={problem}
                onChange={(e) => setProblem(e.target.value)}
                onBlur={() => setTouched(true)}
                aria-invalid={!!problemError}
                aria-describedby={problemError ? 'idea-problem-error' : undefined}
                rows={3}
                autoFocus
              />
              {problemError ? (
                <p id="idea-problem-error" className="text-xs text-destructive">
                  {problemError}
                </p>
              ) : null}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="idea-raw">Original message</Label>
              <Textarea id="idea-raw" value={rawText} onChange={(e) => setRawText(e.target.value)} rows={2} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="idea-files">Attachments</Label>
              <FileDropzone
                id="idea-files"
                multiple
                files={files}
                onFilesChange={setFiles}
                title="Drop files here or click to upload"
                hint=""
              />
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={create.isPending}>
              {create.isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : null}
              Capture
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

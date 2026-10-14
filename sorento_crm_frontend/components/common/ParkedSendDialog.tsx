'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { EmailPreviewFrame } from '@/app/(protected)/system-management/email-templates/components/EmailPreviewFrame';
import { cn } from '@/lib/utils';

export interface ParkedEmail {
  to: { name: string };
  cc: { name: string }[];
  inquiry_count: number;
  line_count: number;
  subject: string;
  body_html: string;
}

export interface ParkedPreviewData {
  emails: ParkedEmail[];
  unresolved: { inquiry_count: number; line_count: number }[];
  inquiry_total?: number;
}

/**
 * The shared "send all parked" dialog (AC-SN-44): a recipient table (To, Cc, Order
 * inquiries, Lines, total row, a "No recipient" line per unresolved group) over one line
 * tab per email showing its subject, To/Cc and rendered body. The caller owns the preview
 * query and the send mutation; nothing here is specific to one document.
 */
export function ParkedSendDialog({
  open,
  onOpenChange,
  title,
  preview,
  isLoading = false,
  onSend,
  isSending = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  preview?: ParkedPreviewData;
  isLoading?: boolean;
  onSend: () => Promise<unknown> | void;
  isSending?: boolean;
}) {
  const [selected, setSelected] = React.useState(0);
  React.useEffect(() => {
    if (open) setSelected(0);
  }, [open]);

  const emails = preview?.emails ?? [];
  const unresolved = preview?.unresolved ?? [];
  const current = emails[Math.min(selected, Math.max(emails.length - 1, 0))];
  const totalInquiries =
    preview?.inquiry_total ?? emails.reduce((sum, email) => sum + email.inquiry_count, 0);
  const totalLines = emails.reduce((sum, email) => sum + email.line_count, 0);
  const ready = !isLoading && preview != null;
  const sendLabel = `Send ${emails.length} ${emails.length === 1 ? 'email' : 'emails'}`;

  async function handleSend() {
    try {
      await onSend();
      onOpenChange(false);
    } catch {
      // The caller's mutation already toasted; the dialog stays open for a retry.
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90dvh] flex-col sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {ready ? (
            <DialogDescription>
              {`${totalInquiries} order ${totalInquiries === 1 ? 'inquiry' : 'inquiries'}, ${emails.length} ${emails.length === 1 ? 'email' : 'emails'}`}
            </DialogDescription>
          ) : null}
        </DialogHeader>
        <DialogBody className="flex-1 space-y-4 overflow-y-auto">
          {!ready ? (
            <div className="space-y-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-40 w-full" />
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <Table className="table-fixed min-w-[34rem]">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-40">To</TableHead>
                      <TableHead className="w-48">Cc</TableHead>
                      <TableHead className="w-32 text-end">Order inquiries</TableHead>
                      <TableHead className="w-24 text-end">Lines</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {emails.map((email, index) => (
                      <TableRow
                        key={`${email.to.name}-${index}`}
                        className={cn('cursor-pointer', index === selected && 'bg-muted/50')}
                        onClick={() => setSelected(index)}
                      >
                        <TableCell className="truncate" title={email.to.name}>
                          {email.to.name}
                        </TableCell>
                        <TableCell
                          className="truncate"
                          title={email.cc.map((person) => person.name).join(', ')}
                        >
                          {email.cc.map((person) => person.name).join(', ')}
                        </TableCell>
                        <TableCell className="text-end tabular-nums">{email.inquiry_count}</TableCell>
                        <TableCell className="text-end tabular-nums">{email.line_count}</TableCell>
                      </TableRow>
                    ))}
                    {unresolved.map((group, index) => (
                      <TableRow key={`unresolved-${index}`} className="text-muted-foreground">
                        <TableCell colSpan={2}>No recipient, stays parked</TableCell>
                        <TableCell className="text-end tabular-nums">{group.inquiry_count}</TableCell>
                        <TableCell className="text-end tabular-nums">{group.line_count}</TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="font-medium">
                      <TableCell colSpan={2}>{`${emails.length} ${emails.length === 1 ? 'email' : 'emails'}`}</TableCell>
                      <TableCell className="text-end tabular-nums">{totalInquiries}</TableCell>
                      <TableCell className="text-end tabular-nums">{totalLines}</TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </div>

              {current ? (
                <div className="space-y-3">
                  <Tabs value={String(selected)} onValueChange={(value) => setSelected(Number(value))}>
                    <div className="overflow-x-auto">
                      <TabsList variant="line" className="mb-0">
                        {emails.map((email, index) => (
                          <TabsTrigger
                            key={`${email.to.name}-${index}`}
                            value={String(index)}
                            onClick={() => setSelected(index)}
                          >
                            {`Email to ${email.to.name} (${email.inquiry_count})`}
                          </TabsTrigger>
                        ))}
                      </TabsList>
                    </div>
                  </Tabs>
                  <div className="min-w-0 space-y-1 text-sm">
                    <p className="truncate text-muted-foreground">
                      {`To ${current.to.name}${current.cc.length ? `, Cc ${current.cc.map((person) => person.name).join(', ')}` : ''}`}
                    </p>
                  </div>
                  <EmailPreviewFrame html={current.body_html} subject={current.subject} />
                </div>
              ) : null}
            </>
          )}
        </DialogBody>
        <DialogFooter className="flex-wrap gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSending}>
            Cancel
          </Button>
          {ready ? (
            <Button onClick={handleSend} disabled={isSending || emails.length === 0}>
              {sendLabel}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default ParkedSendDialog;

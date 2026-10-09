'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { approveConsent, denyConsent, getConsent } from '../services/consentService';

function useRemaining(expiresAt?: string) {
  const target = expiresAt ? Date.parse(expiresAt.endsWith('Z') ? expiresAt : `${expiresAt}Z`) : NaN;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (Number.isNaN(target)) return;
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [target]);
  if (Number.isNaN(target)) return null;
  const secs = Math.max(0, Math.floor((target - now) / 1000));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center p-4">
      <Card className="w-full max-w-md space-y-5 p-6">{children}</Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <b className="font-semibold">{value}</b>
    </div>
  );
}

/** The consent step of the claude.ai sign-in: names the app, the person and where the code goes. */
export default function ConsentCard({ requestId }: { requestId: string }) {
  const { data, error, isLoading } = useQuery({
    queryKey: ['claude-connector-consent', requestId],
    queryFn: () => getConsent(requestId),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const remaining = useRemaining(data?.expires_at);
  const go = (url: string) => window.location.assign(url);
  const approve = useMutation({ mutationFn: () => approveConsent(requestId), onSuccess: (r) => go(r.redirect_to) });
  const deny = useMutation({ mutationFn: () => denyConsent(requestId), onSuccess: (r) => go(r.redirect_to) });
  const busy = approve.isPending || deny.isPending;

  if (isLoading) {
    return (
      <Shell>
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-20 w-full" />
      </Shell>
    );
  }

  if (error || !data) {
    const code = (error as { code?: string } | null)?.code;
    if (code === 'impersonation_not_allowed') {
      return (
        <Shell>
          <h3 className="text-base font-semibold">Access cannot be granted</h3>
          <p className="text-sm text-muted-foreground">
            You are signed in as another user. Access cannot be granted on someone else&apos;s behalf.
          </p>
        </Shell>
      );
    }
    return (
      <Shell>
        <h3 className="text-base font-semibold">This request has expired</h3>
        <p className="text-sm text-muted-foreground">Return to Claude and connect again.</p>
      </Shell>
    );
  }

  const failed = approve.error || deny.error;

  return (
    <Shell>
      <h3 className="text-base font-semibold leading-snug">
        {`${data.client_name} (claude.ai) wants read-only access to CRM data as ${data.user_name}`}
      </h3>
      <div className="space-y-2">
        <Row label="Redirects to" value={data.redirect_host} />
        <Row label="Access" value="Read only" />
        {remaining ? <Row label="Request expires in" value={remaining} /> : null}
      </div>
      {!data.allowed ? (
        <p className="text-sm text-destructive">
          Your account does not have access to the Claude connector. Ask an administrator.
        </p>
      ) : null}
      {failed ? (
        <p className="text-sm text-destructive">That did not go through. Try again.</p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {data.allowed ? (
          <Button variant="primary" disabled={busy} onClick={() => approve.mutate()}>
            Allow
          </Button>
        ) : null}
        <Button variant="outline" disabled={busy} onClick={() => deny.mutate()}>
          Deny
        </Button>
      </div>
    </Shell>
  );
}

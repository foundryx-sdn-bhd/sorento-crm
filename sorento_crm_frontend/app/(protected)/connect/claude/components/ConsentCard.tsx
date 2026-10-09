'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useConsentMutations, useConsentQuery } from '../hooks/useConsent';

function useRemaining(expiresAt?: string) {
  const target = expiresAt ? Date.parse(expiresAt) : NaN;
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

/** Hands the answer back to the shared service as a form POST, so the assertion never sits in a URL. */
function postTo(action: string, fields: Record<string, string>) {
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = action;
  form.style.display = 'none';
  for (const [name, value] of Object.entries(fields)) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-[60dvh] items-center justify-center p-4">
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

function Ended({ title, hint }: { title: string; hint: string }) {
  return (
    <Shell>
      <h3 className="text-base font-semibold">{title}</h3>
      <p className="text-sm text-muted-foreground">{hint}</p>
    </Shell>
  );
}

const codeOf = (error: unknown) => (error as { code?: string | null } | null)?.code ?? null;

/** The consent step of the claude.ai sign-in: names the app, the person and where the answer goes. */
export default function ConsentCard({ req }: { req: string }) {
  const { data, error, isLoading } = useConsentQuery(req);
  const { approve, deny } = useConsentMutations(req);
  const remaining = useRemaining(data?.expires_at);
  const busy = approve.isPending || deny.isPending;

  if (isLoading) {
    return (
      <Shell>
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-20 w-full" />
      </Shell>
    );
  }

  const code = codeOf(error) ?? codeOf(approve.error) ?? codeOf(deny.error);
  if (code === 'impersonation_not_allowed') {
    return (
      <Ended
        title="Access cannot be granted"
        hint="You are signed in as another user. Access cannot be granted on someone else's behalf."
      />
    );
  }
  if (code === 'request_used') {
    return <Ended title="This request has already been used" hint="Return to Claude and connect again." />;
  }
  if (error || !data || code === 'request_expired' || code === 'bad_request') {
    return <Ended title="This request has expired" hint="Return to Claude and connect again." />;
  }

  const allow = () =>
    approve.mutate(undefined, {
      onSuccess: (r) => postTo(r.return_url, { request_id: r.request_id, assertion: r.assertion }),
    });
  const refuse = () =>
    deny.mutate(undefined, {
      onSuccess: (r) => postTo(r.return_url, { request_id: r.request_id, error: 'access_denied' }),
    });
  const failed = approve.error || deny.error;

  return (
    <Shell>
      <h3 className="text-base font-semibold leading-snug">
        {`${data.client_name} wants read-only access to CRM data as ${data.user_name}`}
      </h3>
      <div className="space-y-2">
        <Row label="Returns to" value={data.redirect_host} />
        <Row label="Access" value="Read only" />
        {remaining ? <Row label="Request expires in" value={remaining} /> : null}
      </div>
      {!data.allowed ? (
        <p className="text-sm text-destructive">
          Your account does not have access to the Claude connector. Ask an administrator.
        </p>
      ) : null}
      {failed ? <p className="text-sm text-destructive">That did not go through. Try again.</p> : null}
      <div className="flex flex-wrap gap-2">
        {data.allowed ? (
          <Button variant="primary" disabled={busy} onClick={allow}>
            Allow
          </Button>
        ) : null}
        <Button variant="outline" disabled={busy} onClick={refuse}>
          Deny
        </Button>
      </div>
    </Shell>
  );
}

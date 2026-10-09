'use client';

import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Skeleton } from '@/components/ui/skeleton';
import ConsentCard from './components/ConsentCard';

function Consent() {
  const requestId = useSearchParams().get('request');
  if (!requestId) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center p-4 text-sm text-muted-foreground">
        This request has expired. Return to Claude and connect again.
      </div>
    );
  }
  return <ConsentCard requestId={requestId} />;
}

export default function ConnectClaudePage() {
  return (
    <Suspense fallback={<Skeleton className="mx-auto mt-16 h-48 w-full max-w-md" />}>
      <Consent />
    </Suspense>
  );
}

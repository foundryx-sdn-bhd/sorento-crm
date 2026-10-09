'use client';

import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Skeleton } from '@/components/ui/skeleton';
import ConsentCard from './components/ConsentCard';

function Consent() {
  const req = useSearchParams().get('req');
  if (!req) {
    return (
      <div className="flex min-h-[60dvh] items-center justify-center p-4 text-sm text-muted-foreground">
        This request has expired. Return to Claude and connect again.
      </div>
    );
  }
  return <ConsentCard req={req} />;
}

export default function ConnectClaudePage() {
  return (
    <Suspense fallback={<Skeleton className="mx-auto mt-16 h-48 w-full max-w-md" />}>
      <Consent />
    </Suspense>
  );
}

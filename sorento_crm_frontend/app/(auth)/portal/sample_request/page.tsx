'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { portalHomePath } from '../lib/portal-paths';

// Legacy slug-less tree (staff impersonation stays on /portal). The landing already lists every
// sample request as cards; this path only forwards there.
export default function PortalSampleRequestListPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace(portalHomePath({ slug: null, type: 'sample_request' }));
  }, [router]);
  return null;
}

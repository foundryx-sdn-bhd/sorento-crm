'use client';

import { use, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { portalHomePath } from '../../../lib/portal-paths';

// The landing already lists every sample request as cards; this path only forwards there.
export default function PortalSlugSampleRequestListPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const router = useRouter();
  useEffect(() => {
    router.replace(portalHomePath({ slug, type: 'sample_request' }));
  }, [router, slug]);
  return null;
}

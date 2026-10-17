'use client';

import { use, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { portalHomePath } from '../../../lib/portal-paths';

// The landing already lists every new product application as cards; this path only forwards there.
export default function PortalSlugNewProductApplicationListPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  const router = useRouter();
  useEffect(() => {
    router.replace(portalHomePath({ slug, type: 'new_product_application' }));
  }, [router, slug]);
  return null;
}

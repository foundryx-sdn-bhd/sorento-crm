'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { portalHomePath } from '../lib/portal-paths';

// Legacy slug-less tree (staff impersonation stays on /portal). The landing already lists every
// new product application as cards; this path only forwards there.
export default function PortalNewProductApplicationListPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace(
      portalHomePath({ slug: null, type: 'new_product_application' }),
    );
  }, [router]);
  return null;
}

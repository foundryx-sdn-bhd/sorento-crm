'use client';

import { use } from 'react';
import NewProductApplicationForm from '../../components/NewProductApplicationForm';

// Trim trailing punctuation WhatsApp's link detection can absorb.
function sanitizeId(raw: string): string {
  return (raw || '').replace(/[^0-9a-fA-F-].*$/, '');
}

export default function PortalNewProductApplicationDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return <NewProductApplicationForm applicationId={sanitizeId(id)} />;
}

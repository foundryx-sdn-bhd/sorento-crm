'use client';

import { use } from 'react';
import SampleRequestForm from '../../../../components/SampleRequestForm';

// Trim trailing punctuation WhatsApp's link detection can absorb.
function sanitizeId(raw: string): string {
  return (raw || '').replace(/[^0-9a-fA-F-].*$/, '');
}

export default function PortalSlugSampleRequestDetailPage({
  params,
}: {
  params: Promise<{ slug: string; id: string }>;
}) {
  const { slug, id } = use(params);
  return <SampleRequestForm requestId={sanitizeId(id)} slug={slug} />;
}

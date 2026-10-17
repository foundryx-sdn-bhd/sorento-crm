'use client';

import { use } from 'react';
import SampleRequestForm from '../../../../components/SampleRequestForm';

export default function PortalSlugSampleRequestNewPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  return <SampleRequestForm slug={slug} />;
}

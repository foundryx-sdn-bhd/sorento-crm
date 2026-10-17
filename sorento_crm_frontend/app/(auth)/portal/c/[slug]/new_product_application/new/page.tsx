'use client';

import { use } from 'react';
import NewProductApplicationForm from '../../../../components/NewProductApplicationForm';

export default function PortalSlugNewProductApplicationNewPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = use(params);
  return <NewProductApplicationForm slug={slug} />;
}

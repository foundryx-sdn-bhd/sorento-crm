'use client';

import { use } from 'react';
import { PortalIdeaTrack } from './components/PortalIdeaTrack';

interface PageProps {
  params: Promise<{ token: string }>;
}

/** The customer's idea track page. The token in the link is the credential; there is no login. */
export default function IdeaTrackPortalPage({ params }: PageProps) {
  const { token } = use(params);
  return <PortalIdeaTrack token={token} />;
}

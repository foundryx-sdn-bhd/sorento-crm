'use client';

import SampleRequestForm from '../../components/SampleRequestForm';

// Legacy slug-less tree: without this route `/portal/sample_request/new` fell through to the
// generic [type] page and 404'd, because sample_request is not a generic submission kind.
export default function PortalSampleRequestNewPage() {
  return <SampleRequestForm />;
}

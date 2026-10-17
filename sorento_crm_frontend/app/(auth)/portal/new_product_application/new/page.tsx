'use client';

import NewProductApplicationForm from '../../components/NewProductApplicationForm';

// Legacy slug-less tree: without this route `/portal/new_product_application/new` fell through to the
// generic [type] page and 404'd, because new_product_application is not a generic submission kind.
export default function PortalNewProductApplicationNewPage() {
  return <NewProductApplicationForm />;
}

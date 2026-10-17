import { Metadata } from 'next';
import BillingDocumentDetail from './components/BillingDocumentDetail';

export const metadata: Metadata = {
  title: 'Billing Document',
  description: 'An invoice, cash sale, credit note or debit note from AutoCount.',
};

export default async function BillingDocumentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <BillingDocumentDetail id={id} />;
}

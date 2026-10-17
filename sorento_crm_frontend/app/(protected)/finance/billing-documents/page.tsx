import { Metadata } from 'next';
import BillingDocumentsView from './components/BillingDocumentsView';

export const metadata: Metadata = {
  title: 'Billing Documents',
  description: 'Invoices, cash sales, credit notes and debit notes from AutoCount.',
};

export default function BillingDocumentsPage() {
  return <BillingDocumentsView />;
}

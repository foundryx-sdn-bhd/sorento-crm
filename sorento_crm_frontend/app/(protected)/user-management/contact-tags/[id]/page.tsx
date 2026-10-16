import { Metadata } from 'next';
import ContactTagDetail from '../components/ContactTagDetail';

export const metadata: Metadata = {
  title: 'Contact Tag',
};

type Props = { params: Promise<{ id: string }> };

export default async function Page({ params }: Props) {
  const { id } = await params;
  return <ContactTagDetail id={id} />;
}

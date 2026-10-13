import { Metadata } from 'next';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import ChatbotStatusWordsList from './components/ChatbotStatusWordsList';

export const metadata: Metadata = {
  title: 'Chatbot Status Words',
  description: 'The status values the chatbot understands, per domain.',
};

export default function ChatbotStatusWordsPage() {
  return (
    <>
      <Container>
        <PageHeader title="Chatbot Status Words" />
      </Container>

      <Container>
        <ChatbotStatusWordsList />
      </Container>
    </>
  );
}

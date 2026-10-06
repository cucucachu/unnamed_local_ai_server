import { PageRedirect } from '@/components/PageRedirect';

/** `/chat`: the home pager's Chat page (M19-07; the chat itself is `components/ChatPage.tsx`). */
export default function ChatLink() {
  return <PageRedirect page="chat" />;
}

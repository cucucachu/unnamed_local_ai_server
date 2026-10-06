import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect } from 'react';

import { openChat } from '@/lib/currentChat';
import { showPage } from '@/lib/currentPage';

/** `/chat/<id>` (routine runs, links, notifications) opens that chat on the home pager's Chat page (M19-02, M19-07). */
export default function ChatDeepLink() {
  const { threadId } = useLocalSearchParams<{ threadId: string }>();
  const router = useRouter();
  useEffect(() => {
    if (threadId) openChat(threadId);
    showPage('chat');
    router.dismissTo('/');
  }, [threadId, router]);
  return null;
}

import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect } from 'react';

import { openChat } from '@/lib/currentChat';

/** `/chat/<id>` (routine runs, links, notifications) opens that chat in the Chat tab (M19-02). */
export default function ChatDeepLink() {
  const { threadId } = useLocalSearchParams<{ threadId: string }>();
  const router = useRouter();
  useEffect(() => {
    if (threadId) openChat(threadId);
    router.dismissTo('/chat');
  }, [threadId, router]);
  return null;
}

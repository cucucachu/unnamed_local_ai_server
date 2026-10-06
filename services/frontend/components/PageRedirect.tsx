import { useRouter } from 'expo-router';
import { useEffect } from 'react';

import { showPage, type HomePage } from '@/lib/currentPage';

/** A link to a page of the home pager (`/chat`, `/apps`; M19-07): shows it and returns to `/`. */
export function PageRedirect({ page }: { page: HomePage }) {
  const router = useRouter();
  useEffect(() => {
    showPage(page);
    router.dismissTo('/');
  }, [page, router]);
  return null;
}

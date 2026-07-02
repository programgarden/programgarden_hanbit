/**
 * Client-side providers — wraps the app shell with React Query and boots the
 * single WebSocket stream.
 *
 * The root layout is a server component; this is the one client boundary that
 * owns the QueryClient and the stream lifecycle. Anything below it (screens,
 * TopBar widgets) can call the query/store hooks.
 */

"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { startStream, stopStream } from "@/lib/ws/client";
import { OrderTicketModal } from "@/components/OrderTicketModal";

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  useEffect(() => {
    startStream(queryClient);
    return () => stopStream();
  }, [queryClient]);

  return (
    <QueryClientProvider client={queryClient}>
      {children}
      {/* Mounted once — opened from anywhere via the order-ticket store. */}
      <OrderTicketModal />
    </QueryClientProvider>
  );
}

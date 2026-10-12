import { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { ApiError, getToken, onTokenChange } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LoginPage } from "@/features/auth/LoginPage";
import { Console } from "@/features/console/Console";

function createClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (count, error) => count < 2 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
        refetchOnWindowFocus: true,
      },
    },
  });
}

export function App({ client: injected }: { client?: QueryClient }) {
  const [client] = useState(() => injected ?? createClient());
  const [token, setTokenState] = useState(getToken);
  useEffect(() => onTokenChange((value) => {
    if (!value) client.clear();
    setTokenState(value);
  }), [client]);

  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={300}>
        {token ? <Console /> : <LoginPage />}
        <Toaster position="top-center" richColors closeButton />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

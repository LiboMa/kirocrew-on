import { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { ApiError, cookieSession, getToken, onSignInChange } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LoginPage } from "@/features/auth/LoginPage";
import { Console } from "@/features/console/Console";
import { useTheme } from "@/lib/theme";

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
  const [theme] = useTheme();
  // null until the session cookie has answered (D67): a new tab or a reopened browser is still signed in by it.
  const [signedIn, setSignedIn] = useState<boolean | null>(() => (getToken() ? true : null));
  useEffect(() => onSignInChange((value) => {
    if (!value) client.clear();
    setSignedIn(value);
  }), [client]);
  useEffect(() => {
    if (signedIn === null) void cookieSession().then((value) => setSignedIn((current) => current ?? value));
  }, [signedIn]);

  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={300}>
        {signedIn === null ? null : signedIn ? <Console /> : <LoginPage />}
        {/* Below the 56 px top bar: a toast must never cover its buttons (on 390 px it hid 「打开会话列表」 while hovered) */}
        <Toaster position="top-center" richColors closeButton theme={theme} offset={{ top: 64 }} mobileOffset={{ top: 64 }} />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

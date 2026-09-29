import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./api/auth";
import { Toaster } from "sonner";
import App from "./App";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

// Issue #466 review R2: data router にする。未保存入力の破棄確認
// (`lib/ui-draft.tsx` の `useBlocker`) は data router の中でしか動かず、
// BrowserRouter のままではブラウザーの戻る/進むや `navigate(...)` を止められ
// ない。画面のルーティングは従来どおり `App` の `<Routes>` が持つ (splat の
// 下の子孫 Routes)。
const router = createBrowserRouter([
  {
    path: "*",
    element: (
      <AuthProvider>
        <App />
        <Toaster position="bottom-right" richColors closeButton />
      </AuthProvider>
    ),
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);

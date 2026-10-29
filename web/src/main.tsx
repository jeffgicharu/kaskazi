import "./styles/fonts.css";
import "@fontsource/ibm-plex-mono/400.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/app.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";

import { ApiError } from "./api/client";
import { ErrorPage } from "./pages/ErrorPage";
import { NotFound } from "./pages/NotFound";
import { Landing } from "./pages/Landing";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

const router = createBrowserRouter([
  {
    errorElement: <ErrorPage />,
    children: [
      { path: "/", element: <Landing /> },
      // Each app loads its own code, so the landing page doesn't carry the chat, the desk and the
      // console (the first visit's script fell from 131 KB to under half that, gzipped).
      { path: "/chat", lazy: async () => ({ Component: (await import("./pages/chat/ChatPage")).ChatPage }) },
      { path: "/apply", lazy: async () => ({ Component: (await import("./pages/apply/ApplyPage")).ApplyPage }) },
      { path: "/console", lazy: async () => ({ Component: (await import("./pages/console/ConsolePage")).ConsolePage }) },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}

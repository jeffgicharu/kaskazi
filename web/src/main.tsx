import "@fontsource-variable/fraunces";
import "@fontsource-variable/atkinson-hyperlegible-next";
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
import { ApplyPage } from "./pages/apply/ApplyPage";
import { ChatPage } from "./pages/chat/ChatPage";
import { ConsolePage } from "./pages/console/ConsolePage";

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
      { path: "/chat", element: <ChatPage /> },
      { path: "/apply", element: <ApplyPage /> },
      { path: "/console", element: <ConsolePage /> },
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

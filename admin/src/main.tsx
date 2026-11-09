import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import "./estilos.css";

const cliente = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 15_000 } } });

ReactDOM.createRoot(document.getElementById("raiz")!).render(
  <React.StrictMode>
    <QueryClientProvider client={cliente}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);

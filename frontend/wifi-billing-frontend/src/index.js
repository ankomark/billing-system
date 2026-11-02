import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { Toaster } from "react-hot-toast";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { queryClient } from "./lib/queryClient";
import { persistOptions } from "./lib/persistCache";
import "./index.css";
import StepUpPrompt from "./components/security/StepUpPrompt";

const root = ReactDOM.createRoot(document.getElementById("root"));

root.render(
  <React.StrictMode>
    <ErrorBoundary>
      <PersistQueryClientProvider client={queryClient} persistOptions={persistOptions}>
        <BrowserRouter>
          <App />
          <StepUpPrompt />
          <Toaster
            position="top-right"
            toastOptions={{
              duration: 4000,
              style: {
                borderRadius: "10px",
                fontSize: "14px",
                fontWeight: 500,
              },
              success: {
                iconTheme: { primary: "#059669", secondary: "#fff" },
              },
              error: {
                iconTheme: { primary: "#dc2626", secondary: "#fff" },
              },
            }}
          />
        </BrowserRouter>
      </PersistQueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>
);

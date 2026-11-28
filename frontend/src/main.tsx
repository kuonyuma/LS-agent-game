import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AccountGate } from "./components/AccountGate";
import { ThemeProvider } from "./theme";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <AccountGate>
        {(account, onLogout) => <App key={account.id} account={account} onLogout={onLogout} />}
      </AccountGate>
    </ThemeProvider>
  </StrictMode>,
);

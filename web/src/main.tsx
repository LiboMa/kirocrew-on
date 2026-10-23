import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@/App";
import { applyTheme } from "@/lib/theme";
import "@/index.css";

applyTheme();   // before the first paint: no flash of the other theme

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

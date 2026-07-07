import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// トークン CSS(色・アクセント slate 既定・フォント)。3a §1 決定。
import "@yakudoku/tokens/css/tokens.css";
import "@yakudoku/tokens/css/accents.css";
import "@yakudoku/tokens/css/fonts.css";

import { App } from "./App";
import "./popup.css";

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

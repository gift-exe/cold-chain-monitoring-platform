import "maplibre-gl/dist/maplibre-gl.css";
import "./styles.css";

import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App.jsx";
import { login } from "./auth.js";

const root = createRoot(document.getElementById("root"));
login()
  .then(() =>
    root.render(
      <React.StrictMode>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </React.StrictMode>,
    ),
  )
  .catch((e) => root.render(<p className="fatal">Could not sign in: {String(e?.message || e)}</p>));

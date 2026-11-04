import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./workspace.css";

if ("serviceWorker" in navigator && window.isSecureContext) {
  void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

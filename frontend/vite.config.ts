import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Remote preview is still private: only Tailscale's authenticated *.ts.net
    // hostnames may reach the loopback-bound dev server through Tailscale Serve.
    allowedHosts: ["localhost", "127.0.0.1", ".ts.net"],
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true
      }
    }
  }
});

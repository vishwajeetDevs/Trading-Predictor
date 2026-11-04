import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const api = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/health": api,
      "/products": api,
      "/candles": api,
      "/data": api,
      "/diagnostics": api,
    },
  },
  test: {
    environment: "node",
  },
});

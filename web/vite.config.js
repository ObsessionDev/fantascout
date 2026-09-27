import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Fixed port so it never collides with other local tools; the API runs on 8441.
  // allowedHosts: reached from a phone through Tailscale (`tailscale serve`, *.ts.net).
  server: { host: "127.0.0.1", port: 8442, strictPort: true, allowedHosts: [".ts.net"] },
});

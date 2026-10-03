import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Fixed port so it never collides with other local tools; the API runs on 8441.
  // allowedHosts: reached from a phone through Tailscale (`tailscale serve`, *.ts.net).
  // /salute is the health contract of the Mike ecosystem: the control center asks the GUI port.
  // It is forwarded to the API; if the API is down the GUI answers for it, saying so.
  server: {
    host: "127.0.0.1",
    port: 8442,
    strictPort: true,
    allowedHosts: [".ts.net"],
    proxy: {
      "/salute": {
        target: "http://127.0.0.1:8441",
        rewrite: () => "/api/salute",
        configure: (proxy) => {
          proxy.on("error", (_err, _req, res) => {
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ ok: false, modulo: "fantascout", errore: "l'API (8441) non risponde" }));
          });
        },
      },
    },
  },
});

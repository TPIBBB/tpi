import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // GitHub Pages 的網址是 https://tpibbb.github.io/tpi/，部署時由 workflow 設 VITE_BASE=/tpi/
  base: process.env.VITE_BASE ?? "/",
  server: { port: 5173 },
});

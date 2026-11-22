import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// GitHub Pages 的網址是 https://tpibbb.github.io/tpi/，部署時由 workflow 用 --base=/tpi/ 指定
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});

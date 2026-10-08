import { defineConfig } from "vite";
import uni from "@dcloudio/vite-plugin-uni";
export default defineConfig({
  plugins: [uni()],
  server: {
    host: "127.0.0.1",
    port: 8102,
    proxy: { "/api": { target: "http://127.0.0.1:8097", changeOrigin: true } },
  },
});

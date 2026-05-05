import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import yaml from "js-yaml";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

type WebYaml = {
  dev?: {
    host?: string;
    port?: number;
    gateway_proxy_target?: string;
  };
};

function loadWebConfig(): WebYaml {
  for (const name of ["config.yaml", "config.example.yaml"]) {
    const p = path.join(__dirname, name);
    if (!fs.existsSync(p)) continue;
    const raw = yaml.load(fs.readFileSync(p, "utf8")) as unknown;
    if (raw == null || typeof raw !== "object") return {};
    return raw as WebYaml;
  }
  return {};
}

const w = loadWebConfig();
const dev = w.dev ?? {};
const host = dev.host ?? "127.0.0.1";
const port = dev.port ?? 5173;
const gatewayTarget = dev.gateway_proxy_target ?? "http://127.0.0.1:8000";

const proxy = {
  target: gatewayTarget,
  changeOrigin: true,
  credentials: true,
} as const;

export default defineConfig({
  plugins: [react()],
  server: {
    host,
    port,
    strictPort: true,
    allowedHosts: ["im-office-agent.nat100.top"],
    proxy: {
      "/api": proxy,
      "/lark": proxy,
      "/health": proxy,
    },
  },
});

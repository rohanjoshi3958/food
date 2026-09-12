import type { NextConfig } from "next";

// Base URL of the FastAPI backend that `/api/*` is proxied to.
// Locally this is the uvicorn dev server; in production the Amplify branch
// sets BACKEND_URL to the App Runner (or custom API domain) URL (see infra/).
// Only short requests (CRUD, job enqueue, status polls) should go through this
// rewrite: Amplify's SSR proxy has a ~30 s hard limit.
const DEFAULT_BACKEND_URL = "http://localhost:8000";
const backendUrl = (process.env.BACKEND_URL || DEFAULT_BACKEND_URL).replace(
  /\/+$/,
  "",
);

const nextConfig: NextConfig = {
  experimental: {
    proxyTimeout: 300_000,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

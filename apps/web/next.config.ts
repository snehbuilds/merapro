import type { NextConfig } from "next";

const apiInternal = process.env.MERA_API_URL || "http://api:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // @mera/sdk ships TypeScript source (packages/sdk/src); compile it with the app.
  transpilePackages: ["@mera/sdk"],
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiInternal}/api/v1/:path*`,
      },
      {
        source: "/healthz",
        destination: `${apiInternal}/healthz`,
      },
      {
        source: "/readyz",
        destination: `${apiInternal}/readyz`,
      },
      {
        source: "/docs",
        destination: `${apiInternal}/api/v1/docs`,
      },
      {
        source: "/redoc",
        destination: `${apiInternal}/redoc`,
      },
      {
        source: "/openapi.json",
        destination: `${apiInternal}/openapi.json`,
      },
    ];
  },
};

export default nextConfig;

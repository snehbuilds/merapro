import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // @mera/sdk ships TypeScript source (packages/sdk/src); compile it with the app.
  transpilePackages: ["@mera/sdk"],
};

export default nextConfig;

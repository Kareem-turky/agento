import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Self-contained server (server.js + traced runtime dependencies) for the Web image.
  output: "standalone",
};

export default nextConfig;

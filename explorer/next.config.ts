import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  basePath: "/explorer/app",
  assetPrefix: "/explorer/app",
  trailingSlash: true,
  distDir: "out",
  images: {
    unoptimized: true,
  },
};

export default nextConfig;

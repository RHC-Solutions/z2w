import type { NextConfig } from "next";

const isProd = process.env.NODE_ENV === "production";

const nextConfig: NextConfig = {
  // Static export for Flask to serve
  output: isProd ? "export" : undefined,
  basePath: isProd ? "/ui" : "",
  trailingSlash: true,

  // Dev: proxy all /api and /login calls to Flask
  async rewrites() {
    if (isProd) return [];
    return [
      { source: "/api/:path*", destination: "http://localhost:5000/api/:path*" },
      { source: "/login", destination: "http://localhost:5000/login" },
      { source: "/login/:path*", destination: "http://localhost:5000/login/:path*" },
      { source: "/logout", destination: "http://localhost:5000/logout" },
      { source: "/static/:path*", destination: "http://localhost:5000/static/:path*" },
    ];
  },
};

export default nextConfig;

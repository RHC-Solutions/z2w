import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output: creates a self-contained Node.js server
  // Flask will proxy all non-API requests to the Next.js process
  output: "standalone",
  basePath: "",
  trailingSlash: false,

  // Proxy all /api and /login calls to Flask in dev mode
  async rewrites() {
    const flaskBase = process.env.FLASK_URL ?? "http://localhost:5000";
    return [
      { source: "/api/:path*", destination: `${flaskBase}/api/:path*` },
      { source: "/login", destination: `${flaskBase}/login` },
      { source: "/login/:path*", destination: `${flaskBase}/login/:path*` },
      { source: "/logout", destination: `${flaskBase}/logout` },
      { source: "/static/:path*", destination: `${flaskBase}/static/:path*` },
    ];
  },
};

export default nextConfig;

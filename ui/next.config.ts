import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output: creates a self-contained Node.js server
  output: "standalone",
  basePath: "",
  trailingSlash: false,

  // Proxy API, auth, and static calls to Flask backend
  async rewrites() {
    const flaskBase = process.env.FLASK_URL ?? "http://localhost:5000";
    return [
      // All API routes go to Flask
      { source: "/api/:path*", destination: `${flaskBase}/api/:path*` },
      // OAuth flow stays in Flask (Microsoft callback)
      { source: "/login/oauth", destination: `${flaskBase}/login/oauth` },
      { source: "/login/oauth/:path*", destination: `${flaskBase}/login/oauth/:path*` },
      // Logout clears Flask session and redirects
      { source: "/logout", destination: `${flaskBase}/logout` },
      // Flask static files (logo, favicon, etc.)
      { source: "/static/:path*", destination: `${flaskBase}/static/:path*` },
    ];
  },
};

export default nextConfig;

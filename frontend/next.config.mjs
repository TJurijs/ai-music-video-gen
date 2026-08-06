const backendUrl = process.env.BACKEND_URL || "http://localhost:8010";

/** @type {import('next').NextConfig} */
const nextConfig = {
  turbopack: {
    // This repo can live below a user profile that also has a package lock.
    // Pin discovery here so builds never import dependencies from a parent.
    root: process.cwd(),
  },
  images: {
    remotePatterns: [
      { protocol: "http", hostname: "localhost", port: "8010" },
      { protocol: "http", hostname: "127.0.0.1", port: "8010" },
      { protocol: "http", hostname: "localhost", port: "8011" },
      { protocol: "http", hostname: "127.0.0.1", port: "8011" },
    ],
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
      { source: "/storage/:path*", destination: `${backendUrl}/storage/:path*` },
    ];
  },
};

export default nextConfig;

/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    remotePatterns: [{ protocol: "http", hostname: "localhost", port: "8000" }],
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: "http://localhost:8010/api/:path*" },
      { source: "/storage/:path*", destination: "http://localhost:8010/storage/:path*" },
    ];
  },
};

export default nextConfig;

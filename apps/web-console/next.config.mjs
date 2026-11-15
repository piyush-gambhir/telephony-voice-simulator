const isDevelopment = process.env.NODE_ENV === "development";
const simulatorBackendUrl =
  process.env.SIMULATOR_BACKEND_URL ?? "http://127.0.0.1:8978";

/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: { agentUpgrade: 'security' },
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  ...(isDevelopment
    ? {
        async rewrites() {
          return [
            {
              source: "/simulator-api/:path*",
              destination: `${simulatorBackendUrl}/:path*`,
            },
          ];
        },
      }
    : { output: "export" }),
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default nextConfig;

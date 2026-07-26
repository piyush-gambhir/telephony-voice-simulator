const isDevelopment = process.env.NODE_ENV === "development";
const simulatorBackendUrl =
  process.env.SIMULATOR_BACKEND_URL ?? "http://127.0.0.1:8978";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // The documented local URL uses 127.0.0.1 while Next advertises localhost.
  // Allow both loopback origins so the development client hydrates instead of
  // rendering an inert server-only shell.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // Fully static export — deploy the `out/` dir to any static host
  // (GitHub Pages, Cloudflare Pages, Netlify, S3, Vercel, ...).
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
  // Emit route/index.html so the documented local `pnpm start` server and
  // ordinary object/static hosts resolve clean URLs such as `/console/`.
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default nextConfig;

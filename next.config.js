// The FastAPI dev server's port. Set by mise (see `API_PORT` in mise.toml), so
// a machine where 8000 is taken by another app can move this one in
// mise.local.toml without editing anything committed.
const apiPort = process.env.API_PORT || "8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Performance optimizations
  compiler: {
    removeConsole: process.env.NODE_ENV === 'production', // Remove console.log in production
  },

  // No `images.remotePatterns`: every image this app renders is local. Add the
  // host here if that ever stops being true.

  rewrites: async () => {
    return [
      {
        source: "/api/:path*",
        destination:
          process.env.NODE_ENV === "development"
            ? `http://127.0.0.1:${apiPort}/api/:path*`
            : "/api/",
      },
      {
        source: "/docs",
        destination:
          process.env.NODE_ENV === "development"
            ? `http://127.0.0.1:${apiPort}/docs`
            : "/api/docs",
      },
      {
        source: "/openapi.json",
        destination:
          process.env.NODE_ENV === "development"
            ? `http://127.0.0.1:${apiPort}/openapi.json`
            : "/api/openapi.json",
      },
    ];
  },

  // Reduce bundle size
  productionBrowserSourceMaps: false,

  // Next.js's own gzip buffers a proxied response until it ends, so the dev
  // rewrite above delivered a turn's progress events ("Querying the
  // warehouse") all at once with the answer. On Vercel the CDN compresses,
  // not this server, so turning it off costs nothing there.
  compress: false,

  // The dev rewrite above proxies /api to FastAPI, and Next.js drops a proxied
  // request after 30 seconds by default. The answer only arrives when the
  // whole turn is done, so any question needing more than 30 seconds of tool
  // calls showed an empty answer locally while the backend logged 200. 300
  // seconds matches the Vercel function limit in production.
  experimental: {
    proxyTimeout: 300_000,
  },
};

module.exports = nextConfig;

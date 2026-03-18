/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    // CIF timetable files can be several hundred MB; raise the proxy body limit accordingly.
    middlewareClientMaxBodySize: 2 * 1024 * 1024 * 1024, // 2 GB
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;

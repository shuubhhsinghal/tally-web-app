/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: ['35.237.237.150'],
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: process.env.BACKEND_URL ? `${process.env.BACKEND_URL}/api/:path*` : 'http://127.0.0.1:8000/api/:path*', // Proxy to Backend
      },
    ]
  },
};

export default nextConfig;

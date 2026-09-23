import type { NextConfig } from 'next';
const backend = process.env.NCC_API_ORIGIN || 'http://127.0.0.1:8000';
const config: NextConfig = { async rewrites() { return [
  { source: '/api/:path*', destination: `${backend}/api/:path*` },
  { source: '/meetings/:path*', destination: `${backend}/meetings/:path*` },
  { source: '/documents/:path*', destination: `${backend}/documents/:path*` },
]; } };
export default config;

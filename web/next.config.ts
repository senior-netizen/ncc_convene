import type { NextConfig } from 'next';
const backend = process.env.NCC_API_ORIGIN || 'http://127.0.0.1:8000';
const config: NextConfig = { async rewrites() { return [
  { source: '/api/:path*', destination: `${backend}/api/:path*` },
  { source: '/meetings/:meetingId/documents', destination: `${backend}/meetings/:meetingId/documents` },
  { source: '/documents/:path*', destination: `${backend}/documents/:path*` },
]; } };
export default config;

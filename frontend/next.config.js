/** @type {import('next').NextConfig} */
const nextConfig = {
  async redirects() {
    // The NIW surface moved from / to /niw; the capitalised form is what
    // appears in written materials, so both spellings resolve.
    return [{ source: '/NIW', destination: '/niw', permanent: false }];
  }, reactStrictMode: true };
module.exports = nextConfig;

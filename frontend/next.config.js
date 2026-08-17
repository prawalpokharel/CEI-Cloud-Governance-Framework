/** @type {import('next').NextConfig} */
// NOTE: the /NIW -> /niw redirect lives in src/middleware.js, not in
// redirects() here. Next's redirects() matches case-INsensitively, so a
// '/NIW' source also matches '/niw' itself and loops forever -- middleware
// compares the pathname exactly, which is the behaviour we actually want.
const nextConfig = { reactStrictMode: true };
module.exports = nextConfig;

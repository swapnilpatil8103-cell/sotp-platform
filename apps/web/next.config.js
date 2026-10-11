/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The Docker build sets NEXT_OUTPUT=standalone: a minimal server with only the files it
  // traces (a much smaller image than shipping all of node_modules). Left off for local
  // builds, where it needs symlink rights on Windows and gains nothing.
  ...(process.env.NEXT_OUTPUT === "standalone" ? { output: "standalone" } : {}),
};

module.exports = nextConfig;

import type { NextConfig } from "next";

/**
 * The basemap is 1.6 MB of immutable public-domain geometry.
 *
 * **Cache it hard.** It is served to a browser on an air-gapped workstation that will be
 * opened and closed many times across one case, and it never changes between deployments —
 * the digest is asserted in `tests/geo-map.test.ts`, so a content change is a code change.
 * Without a long cache header every open of a case refetches a megabyte over a link that
 * may not even have a route to the internet.
 */
const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/basemap/:file",
        headers: [
          { key: "Cache-Control", value: "public, max-age=31536000, immutable" },
        ],
      },
    ];
  },
};

export default nextConfig;

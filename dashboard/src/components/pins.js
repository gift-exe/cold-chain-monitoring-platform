// Map pins and base map shared by the map view and the incident details: a pin's colour, size and badge
// show status (never colour alone, FR10.2); its glyph shows what the place is.
import { STATUS } from "./Status.jsx";

const PROBLEM = new Set(["critical", "warning"]);

export const ROUTE = "#0F766E";        // a field team's route

export const MAP_STYLE = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.55, "raster-opacity": 0.9 } }],
};

// Pin glyphs, drawn in a 20 x 20 box inside the pin's white disc.
export const GLYPH = {
  facility: (c) => `<path d="M3 18V8l7-5 7 5v10z" fill="${c}"/>
                    <path d="M9 8.5h2V11h2.5v2H11v2.5H9V13H6.5v-2H9z" fill="#fff"/>`,
  store: (c) => `<path d="M2 18V7.5L10 3l8 4.5V18h-2.5v-7h-11v7z" fill="${c}"/>
                 <path d="M6 13h8M6 15.5h8" stroke="${c}" stroke-width="1.5"/>`,
  mobile: (c) => `<path d="M1 6h11v8.5H1zM12 9h3.8l3.2 3.2v2.3h-7z" fill="${c}"/>
                  <circle cx="5" cy="15.5" r="2.1" fill="${c}" stroke="#fff" stroke-width="1"/>
                  <circle cx="15" cy="15.5" r="2.1" fill="${c}" stroke="#fff" stroke-width="1"/>`,
};
export const SIZE = { critical: 46, warning: 40, stale: 26, ok: 26, no_data: 24 };
export const KIND_LABEL = { facility: "Health facility", store: "Cold store", mobile: "Field team" };

export function pinSvg(status, kind, size) {
  const s = STATUS[status] || STATUS.no_data;
  const badge = PROBLEM.has(status)
    ? `<circle cx="27" cy="6" r="6" fill="#fff" stroke="${s.ink}" stroke-width="1.5"/>
       <text x="27" y="9.3" text-anchor="middle" font-size="9" font-weight="700" fill="${s.ink}">${s.symbol}</text>`
    : "";
  return `<svg width="${size}" height="${(size * 42) / 32}" viewBox="-1 -1 34 44" aria-hidden="true">
    <path d="M16 1C7.7 1 1 7.6 1 15.8c0 10.6 13.2 24 14.1 24.9a1.3 1.3 0 0 0 1.8 0C17.8 39.8 31 26.4 31 15.8 31 7.6 24.3 1 16 1z"
          fill="${s.fill}" stroke="#fff" stroke-width="2"/>
    <circle cx="16" cy="15.5" r="10.5" fill="#fff"/>
    <g transform="translate(6 5.5)">${GLYPH[kind](s.ink)}</g>${badge}
  </svg>`;
}

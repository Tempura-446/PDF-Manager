// Drawing scales and measuring, for to-scale drawings such as house plans.
//
// A scale is { ratio, units, label }: ``ratio`` is real length / length on
// the paper (1/4" = 1'-0" is 48, 1:100 is 100) and ``units`` says how lengths
// are shown: "ft" (feet and inches) or "m" (metres and millimetres).
// Lengths on the page are in PDF points (72 to the inch).

const PT_PER_IN = 72;
const MM_PER_IN = 25.4;

const FRACTIONS = { 0.0625: "1/16", 0.09375: "3/32", 0.125: "1/8", 0.1875: "3/16", 0.25: "1/4", 0.375: "3/8",
  0.5: "1/2", 0.75: "3/4", 1: "1", 1.5: "1 1/2", 3: "3" };

/** Architectural scales: so many inches on paper to a foot. */
export const ARCHITECTURAL = Object.keys(FRACTIONS).map(Number).map((inches) =>
  ({ ratio: 12 / inches, units: "ft", label: `${FRACTIONS[inches]}" = 1'-0"` }));
/** Engineering scales: an inch on paper to so many feet. */
export const ENGINEERING = [10, 20, 30, 40, 50, 60, 100].map((feet) =>
  ({ ratio: 12 * feet, units: "ft", label: `1" = ${feet}'` }));
/** Metric scales. */
export const METRIC = [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000].map((n) =>
  ({ ratio: n, units: "m", label: `1:${n}` }));
export const FULL_SIZE = { ratio: 1, units: "ft", label: "Full size (1:1)" };
export const ALL_SCALES = [...ARCHITECTURAL, ...ENGINEERING, ...METRIC];

/** A number written as 3, 0.25, 1/4 or 1 1/2 (also 1-1/2). */
function number(text) {
  const t = text.trim().replace(/\s*-\s*(?=\d+\/)/, " ");
  let m = t.match(/^(\d+)\s+(\d+)\/(\d+)$/);
  if (m) return +m[1] + +m[2] / +m[3];
  m = t.match(/^(\d+)\/(\d+)$/);
  if (m) return +m[2] ? +m[1] / +m[2] : NaN;
  return /^\d*\.?\d+$/.test(t) ? +t : NaN;
}

const INCH = String.raw`(?:"|''|”|″|\s*in(?:ch(?:es)?)?\.?)`;
const FOOT = String.raw`(?:'|’|′|\s*f(?:ee)?t\.?|\s*foot)`;
const NUM = String.raw`(\d+(?:\s*-?\s*\d+\/\d+|\/\d+|\.\d+)?)`;
const PATTERNS = [
  // 1/4" = 1'-0"  ·  1 1/2" = 1'  ·  1/8 in = 1 ft
  [new RegExp(`${NUM}${INCH}\\s*=\\s*1${FOOT}(?:\\s*-?\\s*0\\s*${INCH}?)?`, "gi"), (m) => {
    const paper = number(m[1]);
    return paper > 0 && paper <= 12 ? { ratio: 12 / paper, units: "ft", label: `${FRACTIONS[paper] || m[1].trim()}" = 1'-0"` } : null;
  }],
  // 1" = 20'  ·  1 in = 20 ft
  [new RegExp(`1${INCH}\\s*=\\s*(\\d+(?:\\.\\d+)?)${FOOT}(?!\\s*-?\\s*\\d)`, "gi"), (m) => {
    const feet = +m[1];
    return feet >= 2 && feet <= 2000 ? { ratio: 12 * feet, units: "ft", label: `1" = ${m[1]}'` } : null;
  }],
  // 1:100  ·  1 : 50
  [/(?<![\d:.])1\s*:\s*(\d{1,4})(?![\d:.])/g, (m) => {
    const n = +m[1];
    return n >= 2 && n <= 5000 ? { ratio: n, units: "m", label: `1:${n}` } : null;
  }],
];

/** The drawing scale printed on a sheet, from the sheet's text: the one
 *  written most often (a sheet may have details at other scales), preferring
 *  ones after the word "scale". null when there is none. */
export function findScale(text) {
  const found = new Map();
  for (const [re, make] of PATTERNS) {
    for (const m of text.matchAll(re)) {
      const scale = make(m);
      if (!scale) continue;
      const before = text.slice(Math.max(0, m.index - 24), m.index);
      const weight = /scale\s*[:\-]?\s*$/i.test(before) ? 3 : /scale/i.test(before) ? 2 : 1;
      if (scale.units === "m" && weight === 1) continue;  // a bare 1:20 is too often a time or a ratio
      const key = scale.label;
      const entry = found.get(key) || { scale, weight: 0, first: m.index };
      entry.weight += weight;
      found.set(key, entry);
    }
  }
  let best = null;
  for (const entry of found.values()) {
    if (!best || entry.weight > best.weight || (entry.weight === best.weight && entry.first < best.first)) best = entry;
  }
  return best ? best.scale : null;
}

function fraction(inches, step = 4) {
  // inches to the nearest 1/step, as 6 1/4
  const whole = Math.floor(inches + 1e-9);
  let n = Math.round((inches - whole) * step), d = step;
  if (n === step) return String(whole + 1);
  if (!n) return String(whole);
  while (n % 2 === 0 && d % 2 === 0) { n /= 2; d /= 2; }
  return whole ? `${whole} ${n}/${d}` : `${n}/${d}`;
}

/** Feet and inches to the nearest quarter inch: 12' 6 1/4". */
export function feetInches(inches) {
  const sign = inches < 0 ? "-" : "";
  let quarters = Math.round(Math.abs(inches) * 4);
  const feet = Math.floor(quarters / 48);
  quarters -= feet * 48;
  const rest = fraction(quarters / 4);
  if (!feet) return `${sign}${rest}"`;
  return `${sign}${feet}' ${rest}"`;
}

/** A length on the page (PDF points) as the real length it stands for. */
export function formatLength(points, scale) {
  const paperIn = points / PT_PER_IN;
  if (!scale) return `${paperIn.toFixed(2)} in on paper`;
  const real = paperIn * scale.ratio;
  if (scale.units === "m") {
    const mm = real * MM_PER_IN;
    return mm >= 1000 ? `${(mm / 1000).toFixed(2)} m` : `${Math.round(mm)} mm`;
  }
  return feetInches(real);
}

/** An area on the page (square PDF points) as the real area it stands for. */
export function formatArea(points2, scale) {
  const paperIn2 = points2 / (PT_PER_IN * PT_PER_IN);
  if (!scale) return `${paperIn2.toFixed(2)} sq in on paper`;
  const real = paperIn2 * scale.ratio * scale.ratio;
  if (scale.units === "m") return `${(real * MM_PER_IN * MM_PER_IN / 1e6).toFixed(2)} m²`;
  return `${(real / 144).toFixed(1)} sq ft`;
}

/** A real length as typed: 12'-6", 12' 6 1/2", 150", 12.5' (feet), 3.5 m,
 *  3500 mm, 45 cm; a bare number counts in ``bare`` units ("ft" or "m").
 *  Returns { inches, units } or null. */
export function parseLength(text, bare = "ft") {
  const t = text.trim().toLowerCase().replace(/[’′]/g, "'").replace(/[”″]|''/g, '"');
  let m = t.match(/^(\d+(?:\.\d+)?)\s*(mm|cm|m)$/);
  if (m) {
    const mm = +m[1] * { mm: 1, cm: 10, m: 1000 }[m[2]];
    return mm > 0 ? { inches: mm / MM_PER_IN, units: "m" } : null;
  }
  m = t.match(/^(?:(\d+(?:\.\d+)?)\s*(?:'|ft|feet|foot))?\s*-?\s*(?:([\d\s/.-]+?)\s*(?:"|in|inch|inches))?$/);
  if (m && (m[1] || m[2])) {
    const feet = m[1] ? +m[1] : 0;
    const inches = m[2] ? number(m[2]) : 0;
    if (Number.isNaN(inches)) return null;
    const total = feet * 12 + inches;
    return total > 0 ? { inches: total, units: "ft" } : null;
  }
  const n = number(t);
  if (!(n > 0)) return null;
  return bare === "m" ? { inches: n * 1000 / MM_PER_IN, units: "m" } : { inches: n * 12, units: "ft" };
}

/** The scale that makes a line of ``points`` on the page stand for ``real``
 *  (from parseLength). */
export function scaleFrom(points, real) {
  const ratio = real.inches / (points / PT_PER_IN);
  const label = real.units === "m" ? `1:${+ratio.toFixed(ratio < 10 ? 2 : 0)}` : `${feetInches(12 / ratio)} = 1'-0"`;
  return { ratio, units: real.units, label: `Set from a dimension (${label})`, custom: true };
}

/** Length of a polyline and area of a polygon, both in PDF points. */
export function distance(a, b) { return Math.hypot(b[0] - a[0], b[1] - a[1]); }
export function polygonArea(points) {
  let s = 0;
  for (let i = 0; i < points.length; i++) {
    const [x1, y1] = points[i], [x2, y2] = points[(i + 1) % points.length];
    s += x1 * y2 - x2 * y1;
  }
  return Math.abs(s) / 2;
}
export function perimeter(points) {
  let s = 0;
  for (let i = 0; i < points.length; i++) s += distance(points[i], points[(i + 1) % points.length]);
  return s;
}

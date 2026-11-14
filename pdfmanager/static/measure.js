/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
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

/** Feet and inches to the nearest sixteenth: 12' 6 1/16". */
export function feetInches(inches, step = 16) {
  const sign = inches < 0 ? "-" : "";
  let parts = Math.round(Math.abs(inches) * step);
  const feet = Math.floor(parts / (12 * step));
  parts -= feet * 12 * step;
  const rest = fraction(parts / step, step);
  if (!feet) return `${sign}${rest}"`;
  return `${sign}${feet}' ${rest}"`;
}

/** How lengths can be shown: [id, label]. "auto" follows the sheet's
 *  scale (feet and inches to 1/16", or metres and millimetres). */
export const UNITS = [
  ["auto", "As the sheet's scale"],
  ["ftin16", "Feet and inches, to 1/16\""], ["ftin8", "Feet and inches, to 1/8\""], ["ftin4", "Feet and inches, to 1/4\""],
  ["ftin2", "Feet and inches, to 1/2\""], ["ftin1", "Feet and inches, to the inch"],
  ["ft", "Decimal feet"], ["in", "Inches"], ["m", "Metres"], ["mm", "Millimetres"],
];

/** A real length (in inches) shown in ``units`` (see UNITS); ``sheet`` is
 *  the sheet scale's kind ("ft" or "m"), for "auto". */
export function formatInches(inches, units = "auto", sheet = "ft") {
  const u = units === "auto" || !UNITS.some(([id]) => id === units) ? (sheet === "m" ? "m-auto" : "ftin16") : units;
  const mm = inches * MM_PER_IN;
  if (u === "m-auto") return mm >= 1000 ? `${(mm / 1000).toFixed(3)} m` : `${Math.round(mm)} mm`;
  if (u.startsWith("ftin")) return feetInches(inches, +u.slice(4));
  if (u === "ft") return `${(inches / 12).toFixed(2)} ft`;
  if (u === "in") return `${inches < 0 ? "-" : ""}${fraction(Math.abs(inches), 16)}"`;
  if (u === "m") return `${(mm / 1000).toFixed(3)} m`;
  return `${Math.round(mm)} mm`;
}

/** A real area (in square inches) shown in ``units``. */
export function formatSquareInches(sqIn, units = "auto", sheet = "ft") {
  const u = units === "auto" || !UNITS.some(([id]) => id === units) ? (sheet === "m" ? "m" : "ft") : units;
  if (u === "m" || u === "mm") return `${(sqIn * MM_PER_IN * MM_PER_IN / 1e6).toFixed(3)} m²`;
  if (u === "in") return `${sqIn.toFixed(1)} sq in`;
  return `${(sqIn / 144).toFixed(2)} sq ft`;
}

/** The real length (inches) a length on the page (points) stands for. */
export function realInches(points, scale) { return points / PT_PER_IN * scale.ratio; }

/** A length on the page (PDF points) as the real length it stands for. */
export function formatLength(points, scale, units = "auto") {
  const paperIn = points / PT_PER_IN;
  if (!scale) return `${paperIn.toFixed(3)} in on paper`;
  return formatInches(paperIn * scale.ratio, units, scale.units);
}

/** An area on the page (square PDF points) as the real area it stands for. */
export function formatArea(points2, scale, units = "auto") {
  const paperIn2 = points2 / (PT_PER_IN * PT_PER_IN);
  if (!scale) return `${paperIn2.toFixed(3)} sq in on paper`;
  return formatSquareInches(paperIn2 * scale.ratio * scale.ratio, units, scale.units);
}

/** The standard scale nearest to ``ratio``, of the same kind (feet or
 *  metres): { scale, off }, ``off`` how far apart they are (0.004 = 0.4%). */
export function nearestScale(ratio, units = "ft") {
  const list = units === "m" ? METRIC : [...ARCHITECTURAL, ...ENGINEERING, FULL_SIZE];
  let best = null;
  for (const scale of list) {
    const off = Math.abs(ratio / scale.ratio - 1);
    if (!best || off < best.off) best = { scale, off };
  }
  return best;
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
/** Length of an open polyline (a run of walls), in PDF points. */
export function pathLength(points) {
  let s = 0;
  for (let i = 1; i < points.length; i++) s += distance(points[i - 1], points[i]);
  return s;
}
export function perimeter(points) {
  let s = 0;
  for (let i = 0; i < points.length; i++) s += distance(points[i], points[(i + 1) % points.length]);
  return s;
}

/** The angle at ``vertex`` between the arms to ``a`` and ``b``, in degrees (0-180). */
export function angle(a, vertex, b) {
  const a1 = Math.atan2(a[1] - vertex[1], a[0] - vertex[0]), a2 = Math.atan2(b[1] - vertex[1], b[0] - vertex[0]);
  let d = Math.abs(a1 - a2) * 180 / Math.PI;
  if (d > 180) d = 360 - d;
  return d;
}

/** Whether ``p`` is inside the polygon ``points``. */
export function inside(p, points) {
  let into = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [xi, yi] = points[i], [xj, yj] = points[j];
    if ((yi > p[1]) !== (yj > p[1]) && p[0] < (xj - xi) * (p[1] - yi) / (yj - yi) + xi) into = !into;
  }
  return into;
}

/** The middle (average) of some points. */
export function centre(points) {
  return [points.reduce((s, p) => s + p[0], 0) / points.length, points.reduce((s, p) => s + p[1], 0) / points.length];
}

/** An area's own size less its cut-outs (``holes``), in square PDF points. */
export function netArea(points, holes = []) {
  return Math.max(0, polygonArea(points) - holes.reduce((s, h) => s + polygonArea(h), 0));
}

/** The four corners of the rectangle with opposite corners ``a`` and ``b``. */
export function rectangle(a, b) {
  return [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]];
}

/** A real length (inches) or area (square inches) as a plain number in the
 *  sheet's units, for a spreadsheet: [number, unit]. */
export function plainNumber(real, isArea, sheet = "ft") {
  if (sheet === "m") return isArea ? [+(real * MM_PER_IN * MM_PER_IN / 1e6).toFixed(4), "m²"] : [+(real * MM_PER_IN / 1000).toFixed(4), "m"];
  return isArea ? [+(real / 144).toFixed(3), "sq ft"] : [+(real / 12).toFixed(4), "ft"];
}

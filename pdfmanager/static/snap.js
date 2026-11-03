// The lines of a drawing, and snapping to them, so that measurements land
// exactly on the geometry in the file rather than where the mouse happened
// to be.
//
//   const geometry = fromOperatorList(opList, OPS);   // every line and curve on a page
//   const hit = geometry.snap([x, y], tolerance, { from, ortho });
//
// Coordinates are PDF user-space points. pdf.js gives a page's content as an
// operator list in which every path is a flat array of draw ops (0 moveTo x y,
// 1 lineTo x y, 2 curveTo x1 y1 x2 y2 x y, 3 quadTo cx cy x y, 4 closePath),
// in the user space current at that point of the content stream.

const CELL = 24;        // the index's cell size, in points (1/3 in)
const CURVE_STEPS = 16; // a curve becomes this many straight pieces; its ends stay exact
export const MAX_SEGMENTS = 2_000_000;  // past this a sheet is read only so far (memory)
const CHUNK = 20000;    // segments indexed between two breathers, when built async

/** m1 then m2, as pdf.js composes transforms (p -> m1(m2(p))). */
function compose(m1, m2) {
  return [
    m1[0] * m2[0] + m1[2] * m2[1], m1[1] * m2[0] + m1[3] * m2[1],
    m1[0] * m2[2] + m1[2] * m2[3], m1[1] * m2[2] + m1[3] * m2[3],
    m1[0] * m2[4] + m1[2] * m2[5] + m1[4], m1[1] * m2[4] + m1[3] * m2[5] + m1[5],
  ];
}
function apply(m, x, y) { return [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]]; }

/** The straight segments a drawing's paths make, as a flat array
 *  [x1, y1, x2, y2, ...], from a pdf.js operator list. Clipping paths and
 *  text are left out; filled shapes count, since walls are often drawn as
 *  filled strips. */
export function segmentsFromOperatorList({ fnArray, argsArray }, OPS) {
  const out = [];
  out.truncated = false;
  if (!fnArray || !argsArray || typeof OPS?.constructPath !== "number") return out;  // not the pdf.js we know
  let ctm = [1, 0, 0, 1, 0, 0];
  const stack = [];
  const paint = new Set([OPS.stroke, OPS.closeStroke, OPS.fill, OPS.eoFill, OPS.fillStroke, OPS.eoFillStroke,
    OPS.closeFillStroke, OPS.closeEOFillStroke]);
  for (let i = 0; i < fnArray.length; i++) {
    const fn = fnArray[i], args = argsArray[i];
    switch (fn) {
      case OPS.save: stack.push(ctm); break;
      case OPS.restore: if (stack.length) ctm = stack.pop(); break;
      case OPS.transform: ctm = compose(ctm, args); break;
      case OPS.paintFormXObjectBegin: stack.push(ctm); if (args?.[0]) ctm = compose(ctm, args[0]); break;
      case OPS.paintFormXObjectEnd: if (stack.length) ctm = stack.pop(); break;
      case OPS.constructPath: {
        if (!paint.has(args?.[0])) break;  // a clip, or a path thrown away
        const path = args[1]?.[0];
        if (path?.length) addPath(out, path, ctm);
        if (out.length >= MAX_SEGMENTS * 4) { out.truncated = true; return out; }
        break;
      }
      default: break;
    }
  }
  return out;
}

function addPath(out, path, m) {
  let cx = 0, cy = 0, sx = 0, sy = 0;
  const line = (x, y) => { if (Number.isFinite(x) && Number.isFinite(y)) out.push(cx, cy, x, y); cx = x; cy = y; };
  for (let i = 0; i < path.length;) {
    switch (path[i++]) {
      case 0: [cx, cy] = apply(m, path[i++], path[i++]); sx = cx; sy = cy; break;
      case 1: line(...apply(m, path[i++], path[i++])); break;
      case 2: {
        const [x1, y1] = apply(m, path[i++], path[i++]), [x2, y2] = apply(m, path[i++], path[i++]), [x, y] = apply(m, path[i++], path[i++]);
        const [x0, y0] = [cx, cy];
        for (let k = 1; k <= CURVE_STEPS; k++) {
          const t = k / CURVE_STEPS, u = 1 - t;
          line(u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x,
            u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y);
        }
        break;
      }
      case 3: {
        const [x1, y1] = apply(m, path[i++], path[i++]), [x, y] = apply(m, path[i++], path[i++]);
        const [x0, y0] = [cx, cy];
        for (let k = 1; k <= CURVE_STEPS; k++) {
          const t = k / CURVE_STEPS, u = 1 - t;
          line(u * u * x0 + 2 * u * t * x1 + t * t * x, u * u * y0 + 2 * u * t * y1 + t * t * y);
        }
        break;
      }
      case 4: if (cx !== sx || cy !== sy) line(sx, sy); break;
      default: return;  // not a path we understand: keep what we have
    }
  }
}

/** The point on segment a-b nearest p, and where along it (0..1). */
function nearestOnSegment(ax, ay, bx, by, px, py) {
  const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy;
  if (len2 === 0) return [ax, ay, 0];
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  return [ax + t * dx, ay + t * dy, t];
}

/** Where segments a-b and c-d cross, or null (parallel, or not within both). */
export function intersection(ax, ay, bx, by, cx, cy, dx, dy) {
  const r = [bx - ax, by - ay], s = [dx - cx, dy - cy];
  const den = r[0] * s[1] - r[1] * s[0];
  if (Math.abs(den) < 1e-12) return null;
  const qp = [cx - ax, cy - ay];
  const t = (qp[0] * s[1] - qp[1] * s[0]) / den, u = (qp[0] * r[1] - qp[1] * r[0]) / den;
  const eps = 1e-9;
  if (t < -eps || t > 1 + eps || u < -eps || u > 1 + eps) return null;
  return [ax + t * r[0], ay + t * r[1]];
}

/** The geometry of one page, indexed for quick lookups near a point. */
export class Geometry {
  constructor(segments, { index = true } = {}) {
    this.segments = segments;  // flat [x1, y1, x2, y2, ...]
    this.cells = new Map();    // "cx|cy" -> segment indices crossing that cell
    this.count = segments.length / 4;
    this.truncated = !!segments.truncated;
    this.indexed = 0;
    if (index) this.indexTo(this.count);
  }

  /** Index segments up to ``end``; returns true once all are indexed. */
  indexTo(end) {
    for (; this.indexed < Math.min(end, this.count); this.indexed++) this.#index(this.indexed);
    return this.indexed >= this.count;
  }

  /** Build the index a chunk at a time, giving the page a chance to draw
   *  between chunks (a dense sheet can have a million lines). */
  static async build(segments, { yieldWith = (fn) => setTimeout(fn, 0) } = {}) {
    const geometry = new Geometry(segments, { index: false });
    while (!geometry.indexTo(geometry.indexed + CHUNK)) await new Promise((r) => yieldWith(r));
    return geometry;
  }

  /** Put segment ``i`` in every cell it passes through (a supercover walk). */
  #index(i) {
    const s = this.segments, x1 = s[i * 4], y1 = s[i * 4 + 1], x2 = s[i * 4 + 2], y2 = s[i * 4 + 3];
    let cx = Math.floor(x1 / CELL), cy = Math.floor(y1 / CELL);
    const ex = Math.floor(x2 / CELL), ey = Math.floor(y2 / CELL);
    const dx = x2 - x1, dy = y2 - y1;
    const stepX = Math.sign(dx), stepY = Math.sign(dy);
    let tMaxX = dx ? ((stepX > 0 ? (cx + 1) * CELL : cx * CELL) - x1) / dx : Infinity;
    let tMaxY = dy ? ((stepY > 0 ? (cy + 1) * CELL : cy * CELL) - y1) / dy : Infinity;
    const tDeltaX = dx ? Math.abs(CELL / dx) : Infinity, tDeltaY = dy ? Math.abs(CELL / dy) : Infinity;
    for (let guard = 0; guard < 100000; guard++) {
      const key = cx + "|" + cy;
      const list = this.cells.get(key);
      if (list) list.push(i); else this.cells.set(key, [i]);
      if (cx === ex && cy === ey) break;
      if (tMaxX < tMaxY) { cx += stepX; tMaxX += tDeltaX; } else { cy += stepY; tMaxY += tDeltaY; }
      if ((stepX > 0 ? cx > ex : cx < ex) && (stepY > 0 ? cy > ey : cy < ey)) break;
    }
  }

  /** Indices of the segments within ``r`` of (x, y) (by cell, so a few more). */
  near(x, y, r) {
    const found = new Set();
    for (let cx = Math.floor((x - r) / CELL); cx <= Math.floor((x + r) / CELL); cx++) {
      for (let cy = Math.floor((y - r) / CELL); cy <= Math.floor((y + r) / CELL); cy++) {
        const list = this.cells.get(cx + "|" + cy);
        if (list) for (const i of list) found.add(i);
      }
    }
    return found;
  }

  /** The best point of the drawing to snap to within ``tol`` of p, or null:
   *  a line's end, where two lines cross, a line's middle, the foot of the
   *  perpendicular from ``from``, or a point on a line, in that order. With
   *  ``ortho`` (an angle in radians, with ``from``), where the locked line
   *  from ``from`` crosses a line of the drawing. Returns
   *  { x, y, kind, dist }. */
  snap(p, tol, options = {}) {
    const best = this.candidates(p, tol, options)[0];
    return best ? { x: best.x, y: best.y, kind: best.kind, dist: best.dist } : null;
  }

  /** Every point snap() could choose within ``tol`` of p, best first (the
   *  order snap() goes by), each once: [{ x, y, kind, dist, segs }], with
   *  ``segs`` the indices of the lines it's on (to show which line it is). */
  candidates([px, py], tol, { from = null, ortho = null } = {}) {
    if (this.indexed < this.count || !Number.isFinite(px) || !Number.isFinite(py) || !(tol > 0)) return [];
    const s = this.segments;
    const ids = [...this.near(px, py, tol)];
    if (!ids.length) return [];
    const d = (x, y) => Math.hypot(x - px, y - py);
    const found = [];
    const consider = (x, y, kind, limit, priority, segs) => {
      const dist = d(x, y);
      if (dist <= limit) found.push({ x, y, kind, dist, priority, segs });
    };
    if (ortho !== null && from) {
      // The locked line through ``from``: where does it cross the drawing?
      const ex = from[0] + Math.cos(ortho) * 1e6, ey = from[1] + Math.sin(ortho) * 1e6;
      const fx = from[0] - Math.cos(ortho) * 1e6, fy = from[1] - Math.sin(ortho) * 1e6;
      for (const i of ids) {
        const hit = intersection(fx, fy, ex, ey, s[i * 4], s[i * 4 + 1], s[i * 4 + 2], s[i * 4 + 3]);
        if (hit) consider(hit[0], hit[1], "cross", tol, 0, [i]);
      }
    } else {
      for (const i of ids) {
        consider(s[i * 4], s[i * 4 + 1], "end", tol, 0, [i]);
        consider(s[i * 4 + 2], s[i * 4 + 3], "end", tol, 0, [i]);
      }
      if (ids.length <= 80) {
        for (let a = 0; a < ids.length; a++) {
          const i = ids[a];
          for (let b = a + 1; b < ids.length; b++) {
            const j = ids[b];
            const hit = intersection(s[i * 4], s[i * 4 + 1], s[i * 4 + 2], s[i * 4 + 3], s[j * 4], s[j * 4 + 1], s[j * 4 + 2], s[j * 4 + 3]);
            if (hit) consider(hit[0], hit[1], "cross", tol * 0.9, 1, [i, j]);
          }
        }
      }
      for (const i of ids) {
        const ax = s[i * 4], ay = s[i * 4 + 1], bx = s[i * 4 + 2], by = s[i * 4 + 3];
        consider((ax + bx) / 2, (ay + by) / 2, "mid", tol * 0.8, 2, [i]);
        if (from) {
          const [fx, fy, t] = nearestOnSegment(ax, ay, bx, by, from[0], from[1]);
          if (t > 0 && t < 1) consider(fx, fy, "perp", tol * 0.8, 3, [i]);
        }
        const [ox, oy] = nearestOnSegment(ax, ay, bx, by, px, py);
        consider(ox, oy, "on", tol * 0.8, 4, [i]);
      }
    }
    found.sort((a, b) => a.priority - b.priority || a.dist - b.dist);
    // The same spot found more than once (two lines ending there): once, with all its lines.
    const out = [];
    for (const c of found) {
      const same = out.find((o) => Math.abs(o.x - c.x) < 1e-6 && Math.abs(o.y - c.y) < 1e-6);
      if (same) { for (const i of c.segs) if (!same.segs.includes(i)) same.segs.push(i); continue; }
      out.push({ x: c.x, y: c.y, kind: c.kind, dist: c.dist, segs: [...c.segs] });
    }
    return out;
  }

  /** Segment ``i`` as [x1, y1, x2, y2]. */
  segment(i) { const s = this.segments; return [s[i * 4], s[i * 4 + 1], s[i * 4 + 2], s[i * 4 + 3]]; }
}

export function fromOperatorList(opList, OPS) {
  return new Geometry(segmentsFromOperatorList(opList, OPS));
}

/** Like fromOperatorList, but without freezing the page while it works. */
export function buildFromOperatorList(opList, OPS) {
  return Geometry.build(segmentsFromOperatorList(opList, OPS));
}

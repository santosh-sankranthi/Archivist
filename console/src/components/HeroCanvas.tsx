import { useEffect, useRef } from "react";

import styles from "./HeroCanvas.module.css";

/** Ambient "signal field" — the landing backdrop (DESIGN-BRIEF §P2.1).
 *
 *  Port of the taste-01 spike engine (docs/spikes/taste-01/hero.template.html):
 *  three depth layers of drifting nodes over a seeded constellation, amber
 *  signal pulses travelling the drawn edges with additive glow + fading
 *  trails, pointer parallax, slow seven-minute field rotation. DPR capped at
 *  2; the rAF loop pauses while the tab is hidden; `prefers-reduced-motion`
 *  renders one static frame with signals frozen mid-flight instead.
 *
 *  The canvas fades in when `staged` flips (Landing gates staging on fonts),
 *  so type never swaps mid-entrance. Capture scripts align shots to the
 *  entrance timeline via `window.__archivist.stagedAt` (same contract as the
 *  spike's capture.py). */

interface Props {
  /** True once the entrance cascade has started. */
  staged: boolean;
}

interface Node {
  x: number;
  y: number;
  layer: number;
  par: number;
  r: number;
  a: number;
  a1: number;
  w1: number;
  p1: number;
  a2: number;
  w2: number;
  p2: number;
  br: number;
  hub: boolean;
  label: string | null;
}

interface Edge {
  a: number;
  b: number;
  act: number;
  bucket: number;
}

interface Pulse {
  path: number[];
  seg: number;
  f: number;
  speed: number;
  trail: Array<[number, number]>;
  fade: number;
  done: boolean;
}

interface Pt {
  x: number;
  y: number;
}

export function HeroCanvas({ staged }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvasEl = ref.current;
    if (!canvasEl) return;
    const ctxEl = canvasEl.getContext("2d");
    if (!ctxEl) return;
    /* Bind non-null consts — TS does not carry narrowing into the closures
       below, and the engine references these from many nested functions. */
    const canvas: HTMLCanvasElement = canvasEl;
    const ctx: CanvasRenderingContext2D = ctxEl;

    /* ---------------- engine state (closure-scoped) ---------------- */
    let W = 0;
    let H = 0;
    let dpr = 1;

    let nodes: Node[] = [];
    let edges: Edge[] = [];
    let adj: Array<Array<[number, number]>> = [];
    let labels: number[] = [];
    let flashes: number[] = [];
    let pulses: Pulse[] = [];
    const par: Pt = { x: 0, y: 0 };
    const parT: Pt = { x: 0, y: 0 };

    const SEED0 = 20261016;
    let seed = SEED0;
    const rnd = () => {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      return seed / 4294967296;
    };
    const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
    const TAU = Math.PI * 2;

    const LAYER = [
      { rMin: 0.9, rMax: 1.4, aMin: 0.1, aMax: 0.21, par: 0.55 },
      { rMin: 1.4, rMax: 2.0, aMin: 0.17, aMax: 0.33, par: 0.8 },
      { rMin: 1.9, rMax: 2.9, aMin: 0.28, aMax: 0.55, par: 1.0 },
    ];
    const BUCKET_A = [0.055, 0.08, 0.112];
    const ROT = { fx: 0.62, fy: 0.44 };
    const OMEGA = TAU / 420; /* full field revolution ≈ 7 min */

    const glowSpr = (() => {
      const s = document.createElement("canvas");
      s.width = s.height = 64;
      const g = s.getContext("2d");
      if (g) {
        const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
        gr.addColorStop(0, "rgba(255,196,110,0.85)");
        gr.addColorStop(0.35, "rgba(240,169,78,0.28)");
        gr.addColorStop(1, "rgba(240,169,78,0)");
        g.fillStyle = gr;
        g.fillRect(0, 0, 64, 64);
      }
      return s;
    })();

    const P1: Pt = { x: 0, y: 0 };
    const P2: Pt = { x: 0, y: 0 };

    function nodePos(nd: Node, t: number, o: Pt): Pt {
      const x =
        nd.x + nd.a1 * Math.sin(nd.w1 * t + nd.p1) + nd.a2 * Math.sin(nd.w2 * t + nd.p2);
      const y =
        nd.y +
        nd.a1 * 0.8 * Math.cos(nd.w1 * 0.9 * t + nd.p1 * 1.3) +
        nd.a2 * Math.cos(nd.w2 * 1.1 * t + nd.p2);
      const ccx = W * ROT.fx;
      const ccy = H * ROT.fy;
      const ang = OMEGA * t;
      const ca = Math.cos(ang);
      const sa = Math.sin(ang);
      const dx = x - ccx;
      const dy = y - ccy;
      o.x = ccx + dx * ca - dy * sa + par.x * nd.par;
      o.y = ccy + dx * sa + dy * ca + par.y * nd.par;
      return o;
    }

    function build(): void {
      seed = SEED0;
      nodes = [];
      edges = [];
      adj = [];
      labels = [];
      flashes = [];
      pulses = [];
      const n = Math.max(70, Math.min(120, Math.round((W * H) / 11500)));
      let i: number;
      let j: number;

      for (i = 0; i < n; i++) {
        const lr = rnd();
        const layer = lr < 0.42 ? 0 : lr < 0.78 ? 1 : 2;
        const cf = LAYER[layer];
        nodes.push({
          x: W * (0.03 + 0.94 * rnd()),
          y: H * (0.05 + 0.92 * rnd()),
          layer,
          par: cf.par,
          r: lerp(cf.rMin, cf.rMax, rnd()),
          a: lerp(cf.aMin, cf.aMax, rnd()),
          a1: 5 + 9 * rnd(),
          w1: TAU / (22 + 10 * rnd()),
          p1: rnd() * TAU,
          a2: 1.5 + 2.5 * rnd(),
          w2: TAU / (7 + 5 * rnd()),
          p2: rnd() * TAU,
          br: rnd() * TAU,
          hub: false,
          label: null,
        });
      }
      flashes = new Array<number>(n).fill(0);

      /* hubs — three near nodes wearing a ring */
      const near: number[] = [];
      for (i = 0; i < n; i++) if (nodes[i].layer === 2) near.push(i);
      for (i = 0; i < 3 && near.length; i++) {
        nodes[near[Math.floor(rnd() * near.length)]].hub = true;
      }

      /* edges — 2-nearest within 230px + a few long threads */
      const eset: Record<string, number> = {};
      adj = [];
      for (i = 0; i < n; i++) adj.push([]);
      function addEdge(a: number, b: number): void {
        const key = a < b ? `${a}:${b}` : `${b}:${a}`;
        if (eset[key]) return;
        eset[key] = 1;
        const e = edges.length;
        edges.push({ a, b, act: 0, bucket: Math.max(nodes[a].layer, nodes[b].layer) });
        adj[a].push([b, e]);
        adj[b].push([a, e]);
      }
      for (i = 0; i < n; i++) {
        const best: Array<[number, number]> = [];
        for (j = 0; j < n; j++) {
          if (i === j) continue;
          const ddx = nodes[i].x - nodes[j].x;
          const ddy = nodes[i].y - nodes[j].y;
          const d2 = ddx * ddx + ddy * ddy;
          if (d2 < 230 * 230) best.push([d2, j]);
        }
        best.sort((x, y2) => x[0] - y2[0]);
        for (j = 0; j < Math.min(2, best.length); j++) addEdge(i, best[j][1]);
        if (rnd() < 0.18) {
          let far = -1;
          let tries = 0;
          while (tries++ < 12) {
            const k = Math.floor(rnd() * n);
            if (k === i) continue;
            const ex = nodes[i].x - nodes[k].x;
            const ey = nodes[i].y - nodes[k].y;
            if (ex * ex + ey * ey < 300 * 300) {
              far = k;
              break;
            }
          }
          if (far >= 0) addEdge(i, far);
        }
      }

      /* labels — real flask symbols, right side, spread >=150px apart */
      const names = [
        "url_for()",
        "render_template()",
        "Flask",
        "Blueprint",
        "helpers.py",
        "make_response()",
        "session",
      ];
      const cand: number[] = [];
      for (i = 0; i < n; i++) if (nodes[i].layer >= 1 && nodes[i].x > W * 0.44) cand.push(i);
      cand.sort((a, b) => nodes[a].x - nodes[b].x);
      const chosen: number[] = [];
      for (i = 0; i < cand.length && chosen.length < names.length; i++) {
        const k2 = cand[i];
        let ok = true;
        for (j = 0; j < chosen.length; j++) {
          const wx = nodes[k2].x - nodes[chosen[j]].x;
          const wy = nodes[k2].y - nodes[chosen[j]].y;
          if (wx * wx + wy * wy < 150 * 150) {
            ok = false;
            break;
          }
        }
        if (ok) chosen.push(k2);
      }
      for (i = 0; i < chosen.length; i++) {
        nodes[chosen[i]].label = names[i % names.length];
        labels.push(chosen[i]);
      }
    }

    /* ---------------- pulses ---------------- */
    function edgeAct(a: number, b: number, v: number): void {
      for (let i = 0; i < adj[a].length; i++) {
        if (adj[a][i][0] === b) {
          edges[adj[a][i][1]].act = v;
          return;
        }
      }
    }

    function spawnPulse(): void {
      let start = -1;
      let tries = 0;
      while (tries++ < 48) {
        const k = Math.floor(rnd() * nodes.length);
        if (nodes[k].x > W * 0.35 && nodes[k].layer > 0) {
          start = k;
          break;
        }
      }
      if (start < 0) start = Math.floor(rnd() * nodes.length);
      const path = [start];
      let cur = start;
      const hops = 3 + Math.floor(rnd() * 3);
      for (let h = 0; h < hops; h++) {
        const nb = adj[cur] || [];
        if (!nb.length) break;
        const pool: number[] = [];
        for (let i = 0; i < nb.length; i++) if (nodes[nb[i][0]].x > W * 0.3) pool.push(nb[i][0]);
        if (!pool.length) for (let i = 0; i < nb.length; i++) pool.push(nb[i][0]);
        const next = pool[Math.floor(rnd() * pool.length)];
        if (path.indexOf(next) !== -1) break;
        path.push(next);
        cur = next;
      }
      if (path.length < 2) return;
      pulses.push({
        path,
        seg: 0,
        f: 0,
        speed: 180 + 90 * rnd(),
        trail: [],
        fade: 1,
        done: false,
      });
    }

    function headOf(p: Pulse, t: number, o: Pt): Pt {
      const a = nodePos(nodes[p.path[p.seg]], t, P1);
      const b = nodePos(nodes[p.path[p.seg + 1]], t, P2);
      o.x = a.x + (b.x - a.x) * p.f;
      o.y = a.y + (b.y - a.y) * p.f;
      return o;
    }

    function stepPulses(t: number, dt: number): void {
      for (let i = pulses.length - 1; i >= 0; i--) {
        const p = pulses[i];
        if (p.done) {
          p.fade -= dt / 0.9;
          if (p.fade <= 0.02) pulses.splice(i, 1);
          continue;
        }
        let rem = p.speed * dt;
        let guard = 0;
        while (rem > 0 && !p.done && guard++ < 8) {
          const a = nodePos(nodes[p.path[p.seg]], t, P1);
          const b = nodePos(nodes[p.path[p.seg + 1]], t, P2);
          const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
          const need = (1 - p.f) * len;
          if (rem >= need) {
            rem -= need;
            p.f = 0;
            edgeAct(p.path[p.seg], p.path[p.seg + 1], 1);
            flashes[p.path[p.seg + 1]] = 1;
            p.seg++;
            if (p.seg >= p.path.length - 1) {
              p.done = true;
              p.f = 1;
            }
          } else {
            p.f += rem / len;
            rem = 0;
          }
        }
        if (!p.done) {
          headOf(p, t, P1);
          const lastPt = p.trail[p.trail.length - 1];
          if (!lastPt || Math.hypot(P1.x - lastPt[0], P1.y - lastPt[1]) > 1.6) {
            p.trail.push([P1.x, P1.y]);
            if (p.trail.length > 44) p.trail.shift();
          }
        }
      }
    }

    /* ---------------- draw ---------------- */
    let labelFade = 0;
    let fontsReady = false;
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(() => {
        fontsReady = true;
      });
    } else {
      fontsReady = true;
    }

    function draw(t: number): void {
      ctx.clearRect(0, 0, W, H);
      let i: number;
      let b: number;

      /* base edges — batched per depth bucket */
      for (b = 0; b < 3; b++) {
        ctx.beginPath();
        let any = false;
        for (i = 0; i < edges.length; i++) {
          const e = edges[i];
          if (e.bucket !== b || e.act > 0.02) continue;
          nodePos(nodes[e.a], t, P1);
          nodePos(nodes[e.b], t, P2);
          ctx.moveTo(P1.x, P1.y);
          ctx.lineTo(P2.x, P2.y);
          any = true;
        }
        if (!any) continue;
        ctx.strokeStyle = `rgba(154,164,186,${BUCKET_A[b]})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      /* activated edges — the amber history a signal leaves behind */
      for (i = 0; i < edges.length; i++) {
        const e2 = edges[i];
        if (e2.act <= 0.02) continue;
        nodePos(nodes[e2.a], t, P1);
        nodePos(nodes[e2.b], t, P2);
        ctx.globalCompositeOperation = "lighter";
        ctx.strokeStyle = `rgba(240,169,78,${(0.1 * e2.act).toFixed(3)})`;
        ctx.lineWidth = 3.5;
        ctx.beginPath();
        ctx.moveTo(P1.x, P1.y);
        ctx.lineTo(P2.x, P2.y);
        ctx.stroke();
        ctx.strokeStyle = `rgba(242,189,112,${(0.62 * e2.act).toFixed(3)})`;
        ctx.lineWidth = 1.1;
        ctx.beginPath();
        ctx.moveTo(P1.x, P1.y);
        ctx.lineTo(P2.x, P2.y);
        ctx.stroke();
        ctx.globalCompositeOperation = "source-over";
      }

      /* nodes */
      for (i = 0; i < nodes.length; i++) {
        const nd = nodes[i];
        nodePos(nd, t, P1);
        const fl = flashes[i];
        const r = nd.r * (1 + 0.06 * Math.sin(t * 0.97 + nd.br));
        if (fl > 0.02) {
          ctx.globalCompositeOperation = "lighter";
          ctx.globalAlpha = 0.5 * fl;
          const gs = 26 + 30 * fl;
          ctx.drawImage(glowSpr, P1.x - gs / 2, P1.y - gs / 2, gs, gs);
          ctx.globalAlpha = 1;
          ctx.globalCompositeOperation = "source-over";
        }
        if (nd.hub) {
          ctx.strokeStyle = `rgba(${Math.round(lerp(178, 240, fl))},${Math.round(
            lerp(186, 178, fl),
          )},${Math.round(lerp(204, 120, fl))},${(0.12 + 0.5 * fl).toFixed(3)})`;
          ctx.lineWidth = 0.8;
          ctx.beginPath();
          ctx.arc(P1.x, P1.y, r + 4, 0, TAU);
          ctx.stroke();
        }
        ctx.fillStyle = `rgba(${Math.round(lerp(176, 255, fl))},${Math.round(
          lerp(184, 203, fl),
        )},${Math.round(lerp(202, 138, fl))},${Math.min(0.85, nd.a + 0.3 * fl).toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(P1.x, P1.y, r, 0, TAU);
        ctx.fill();
      }

      /* pulses — additive glow, soft trail */
      ctx.globalCompositeOperation = "lighter";
      for (i = 0; i < pulses.length; i++) {
        const p = pulses[i];
        const tr = p.trail;
        const L = tr.length;
        if (L > 1) {
          for (let s2 = 1; s2 < L; s2++) {
            const u = s2 / (L - 1);
            const al = Math.pow(u, 2.0) * 0.58 * p.fade;
            ctx.strokeStyle = `rgba(255,186,105,${al.toFixed(3)})`;
            ctx.lineWidth = 0.7 + 1.3 * u;
            ctx.beginPath();
            ctx.moveTo(tr[s2 - 1][0], tr[s2 - 1][1]);
            ctx.lineTo(tr[s2][0], tr[s2][1]);
            ctx.stroke();
          }
          for (let s2 = 1; s2 < L; s2 += 2) {
            const u2 = s2 / (L - 1);
            ctx.strokeStyle = `rgba(240,169,78,${(Math.pow(u2, 2.4) * 0.18 * p.fade).toFixed(3)})`;
            ctx.lineWidth = 2.4 + 2.6 * u2;
            ctx.beginPath();
            ctx.moveTo(tr[s2 - 1][0], tr[s2 - 1][1]);
            ctx.lineTo(tr[s2][0], tr[s2][1]);
            ctx.stroke();
          }
        }
        if (!p.done) {
          headOf(p, t, P1);
          ctx.globalAlpha = 0.55 * p.fade;
          ctx.drawImage(glowSpr, P1.x - 26, P1.y - 26, 52, 52);
          ctx.globalAlpha = 1;
          ctx.fillStyle = `rgba(255,222,166,${(0.95 * p.fade).toFixed(3)})`;
          ctx.beginPath();
          ctx.arc(P1.x, P1.y, 2.6, 0, TAU);
          ctx.fill();
        }
      }
      ctx.globalCompositeOperation = "source-over";

      /* labels — instrument whispers attached to nodes */
      if (fontsReady && labelFade > 0.01) {
        ctx.font = '10px "JetBrains Mono", ui-monospace, monospace';
        ctx.textBaseline = "middle";
        for (i = 0; i < labels.length; i++) {
          const li = labels[i];
          const lnd = nodes[li];
          nodePos(lnd, t, P1);
          const lfl = flashes[li];
          const lal = (0.34 + 0.5 * Math.min(1, lfl * 1.4)) * labelFade;
          ctx.strokeStyle = `rgba(150,160,180,${(0.16 * labelFade).toFixed(3)})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(P1.x + lnd.r + 1.5, P1.y - lnd.r - 1.5);
          ctx.lineTo(P1.x + 9, P1.y - 9);
          ctx.stroke();
          ctx.fillStyle =
            lfl > 0.25
              ? `rgba(255,214,150,${Math.min(0.8, lal).toFixed(3)})`
              : `rgba(178,186,202,${lal.toFixed(3)})`;
          ctx.fillText(lnd.label ?? "", P1.x + 11, P1.y - 9);
        }
      }
    }

    /* ---------------- loop + lifecycle ---------------- */
    const T0 = performance.now();
    const reduce =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let rafId = 0;
    let running = false;
    let nextSpawn = 1.35;
    let last = 0;

    function frame(now: number): void {
      const t = (now - T0) / 1000;
      const dt = Math.min(0.05, last ? (now - last) / 1000 : 0.016);
      last = now;

      par.x += (parT.x - par.x) * (1 - Math.exp(-dt * 2.2));
      par.y += (parT.y - par.y) * (1 - Math.exp(-dt * 2.2));

      for (let i = 0; i < edges.length; i++) {
        if (edges[i].act > 0.005) edges[i].act *= Math.exp(-dt * 1.15);
        else edges[i].act = 0;
      }
      for (let i = 0; i < nodes.length; i++) {
        if (flashes[i] > 0.005) flashes[i] *= Math.exp(-dt * 1.5);
        else flashes[i] = 0;
      }

      let active = 0;
      for (let i = 0; i < pulses.length; i++) if (!pulses[i].done) active++;
      if (t >= nextSpawn && active < 4) {
        spawnPulse();
        nextSpawn = t + 0.45 + 0.4 * rnd();
      }

      stepPulses(t, dt);

      if (t > 0.8) labelFade = Math.min(1, labelFade + dt / 0.7);

      draw(t);
      if (running) rafId = requestAnimationFrame(frame);
    }

    function start(): void {
      if (running || reduce) return;
      running = true;
      last = 0;
      rafId = requestAnimationFrame(frame);
    }

    function stop(): void {
      running = false;
      cancelAnimationFrame(rafId);
    }

    /* ---------------- reduced-motion static frame ---------------- */
    function renderStatic(): void {
      pulses = [];
      const tS = 47.3;
      seed = 9091;
      for (let k = 0; k < 3; k++) {
        spawnPulse();
        const p = pulses[pulses.length - 1];
        if (!p) continue;
        p.seg = Math.max(
          0,
          Math.min(p.path.length - 2, Math.floor((p.path.length - 1) * (0.35 + 0.2 * k))),
        );
        p.f = 0.55;
        const a = nodePos(nodes[p.path[p.seg]], tS, P1);
        const b = nodePos(nodes[p.path[p.seg + 1]], tS, P2);
        for (let q = 8; q >= 0; q--) {
          const fq = Math.max(0, p.f - 0.1 * q);
          p.trail.push([a.x + (b.x - a.x) * fq, a.y + (b.y - a.y) * fq]);
        }
        for (let s2 = 0; s2 < p.seg; s2++) edgeAct(p.path[s2], p.path[s2 + 1], 0.5);
        flashes[p.path[p.seg + 1]] = 0.6;
      }
      labelFade = 1;
      draw(tS);
    }

    /* ---------------- sizing ---------------- */
    let rt = 0;
    function resize(): void {
      W = window.innerWidth;
      H = window.innerHeight;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      canvas.style.width = `${W}px`;
      canvas.style.height = `${H}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      build();
      if (reduce) renderStatic();
    }

    const onResize = () => {
      window.clearTimeout(rt);
      rt = window.setTimeout(resize, 120);
    };
    const onPointerMove = (ev: PointerEvent) => {
      parT.x = (ev.clientX / W - 0.5) * 22;
      parT.y = (ev.clientY / H - 0.5) * 14;
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else start();
    };

    window.addEventListener("resize", onResize);
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);

    resize();
    if (reduce) {
      /* fonts may not be ready yet — redraw once so labels use the real face */
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(() => {
          if (reduce) renderStatic();
        });
      }
    } else {
      start();
    }

    return () => {
      stop();
      window.clearTimeout(rt);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("pointermove", onPointerMove);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return (
    <canvas
      ref={ref}
      className={`${styles.graph}${staged ? ` ${styles.on}` : ""}`}
      aria-hidden="true"
    />
  );
}

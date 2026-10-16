/** Cytoscape canvas — "Ink & Signal" restyle with selection spotlight.
 *
 *  Data contract: elements carry `nodeId` / `edgeId` (server ids; canvas ids
 *  are prefixed to dodge node/edge id-space collisions). When a layout
 *  settles the container sets `data-settled="1"` — capture tooling waits on
 *  that instead of guessing timings. De-collision recipe (REVIEW-M4):
 *  repulsion 16k, edge 104, nodeOverlap 14, min-zoomed-font-size 7,
 *  text-max-width 96px, ellipsis.
 *
 *  Motion (P2.2): node hover scales 1.06 + glow; the spotlight eases over
 *  300ms; with `draw` (Explorer paths) lit edges reveal as a 600ms dash
 *  sweep and a signal pulse travels the chain; with `ripple` (Ask replay)
 *  the subgraph fades in under a concentric ring at its subject, and the
 *  camera fits with a short animation. Reduced motion keeps every state,
 *  drops every movement. */

import cytoscape from "cytoscape";
import type {
  Core,
  Css,
  EdgeSingular,
  LayoutOptions,
  NodeSingular,
  StylesheetJson,
} from "cytoscape";
import { useEffect, useRef } from "react";

import { orderChain, toCyElements } from "../lib/graph";
import type { Chain, Spotlight } from "../lib/graph";
import { prefersReducedMotion } from "../lib/motion";
import type { GraphNode, GraphPayload } from "../lib/types";
import styles from "./GraphCanvas.module.css";

/** Runtime-supported style props the bundled .d.ts omits (the shadow
 *  family that draws the amber glow). Kept in one helper so the rest of
 *  the sheet stays fully typed. */
function glow(props: Record<string, number | string>): Css.Node | Css.Edge {
  return props as unknown as Css.Node;
}

/** Rendered edge length in px — present at runtime but omitted from the
 *  bundled .d.ts (same family as the shadow styles above). Returns 0 when
 *  the call is unavailable, so callers can fall back to a fixed length. */
function renderedLen(edge: EdgeSingular): number {
  const fn = (edge as unknown as { renderedLength?: () => number }).renderedLength;
  return typeof fn === "function" ? fn.call(edge) : 0;
}

const STYLE: StylesheetJson = [
  {
    selector: "node",
    style: {
      "background-color": "#161b25",
      "border-width": 1.5,
      "border-color": "#323a4c",
      label: "data(label)",
      "font-family": "JetBrains Mono, ui-monospace, monospace",
      "font-size": 9,
      color: "#a8b0c0",
      "text-valign": "bottom",
      "text-halign": "center",
      "text-margin-y": 5,
      "text-wrap": "ellipsis",
      "text-max-width": "96px",
      "min-zoomed-font-size": 7,
      "text-events": "yes",
      "text-background-color": "#0c0e12",
      "text-background-opacity": 0.8,
      "text-background-padding": "3px",
      width: 15,
      height: 15,
      "transition-property": "opacity",
      "transition-duration": 140,
    },
  },
  {
    selector: 'node[type="Symbol"][kind="class"]',
    style: { shape: "round-rectangle", "border-color": "#3d465c" },
  },
  {
    selector: 'node[type="File"]',
    style: {
      shape: "round-rectangle",
      width: 13,
      height: 13,
      "background-color": "#1d2431",
      "border-color": "#4a5570",
    },
  },
  {
    selector: 'node[type="Module"]',
    style: {
      shape: "diamond",
      width: 13,
      height: 13,
      "background-color": "#141922",
      "border-color": "#6e7687",
    },
  },
  {
    selector: "node[?focus]",
    style: glow({
      "border-width": 2,
      "border-color": "#e9ae58",
      color: "#e9ae58",
      "shadow-blur": 18,
      "shadow-color": "#e9ae58",
      "shadow-opacity": 0.28,
      "shadow-offset-x": 0,
      "shadow-offset-y": 0,
    }),
  },
  {
    selector: "node.sel",
    style: glow({
      "border-width": 2.5,
      "border-color": "#f2bd70",
      "shadow-blur": 22,
      "shadow-color": "#e9ae58",
      "shadow-opacity": 0.35,
      "shadow-offset-x": 0,
      "shadow-offset-y": 0,
      "transition-duration": 300,
    }),
  },
  {
    selector: "edge",
    style: {
      width: 1,
      "line-color": "#2b3344",
      "target-arrow-color": "#2b3344",
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.65,
      opacity: 0.85,
      "transition-property": "opacity",
      "transition-duration": 140,
    },
  },
  {
    selector: 'edge[rel="IMPORTS"]',
    style: { "line-style": "dotted", "line-color": "#37425a" },
  },
  {
    selector: 'edge[rel="DEFINES"]',
    style: { "target-arrow-shape": "none", "line-color": "#242b38", opacity: 0.6 },
  },
  {
    selector: "edge[?weak]",
    style: {
      "line-style": "dashed",
      "line-color": "#4a4436",
      "target-arrow-color": "#4a4436",
    },
  },
  {
    selector: "edge.lit",
    style: glow({
      "line-color": "#e9ae58",
      "target-arrow-color": "#e9ae58",
      width: 1.6,
      "shadow-blur": 10,
      "shadow-color": "#e9ae58",
      "shadow-opacity": 0.3,
      "shadow-offset-x": 0,
      "shadow-offset-y": 0,
      "transition-duration": 300,
    }),
  },
  {
    selector: "node.hov",
    style: glow({
      "border-color": "#e9ae58",
      "shadow-blur": 14,
      "shadow-color": "#e9ae58",
      "shadow-opacity": 0.18,
      "shadow-offset-x": 0,
      "shadow-offset-y": 0,
    }),
  },
  {
    selector: ".dim",
    style: { opacity: 0.35, "transition-duration": 300 },
  },
];

interface Props {
  payload: GraphPayload | null;
  spotlight?: Spotlight | null;
  selectedId?: number | null;
  onSelect?: (node: GraphNode | null) => void;
  /** Explorer paths: reveal lit edges as a dash sweep + pulse the chain. */
  draw?: boolean;
  /** Ask replay: fade the subgraph in and ripple a ring at its subject. */
  ripple?: boolean;
  /** "wide": roomier forces for hub-shaped replay graphs (Ask canvas). */
  spread?: "normal" | "wide";
}

export function GraphCanvas({
  payload,
  spotlight,
  selectedId,
  onSelect,
  draw = false,
  ripple = false,
  spread = "normal",
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const nodesRef = useRef<Map<number, GraphNode>>(new Map());
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  /* Trace motion state: a token invalidates any in-flight draw/pulse;
   * timers, edge styles, and the pulse dot are restored by stopTrace. */
  const traceTokenRef = useRef(0);
  const traceTimersRef = useRef<number[]>([]);
  const pendingChainRef = useRef<Chain | null>(null);
  const activeChainKeyRef = useRef("");
  const drawnEdgesRef = useRef<EdgeSingular[]>([]);
  const pulseRef = useRef<HTMLSpanElement | null>(null);
  const ringRef = useRef<HTMLSpanElement | null>(null);

  function clearTraceTimers(): void {
    for (const id of traceTimersRef.current) window.clearTimeout(id);
    traceTimersRef.current = [];
  }

  /** Invalidate in-flight trace motion and restore edge styles. Safe to
   *  call repeatedly; every mutation path calls it before re-arming. */
  function stopTrace(): void {
    traceTokenRef.current += 1;
    clearTraceTimers();
    for (const edge of drawnEdgesRef.current) {
      if (!edge.removed()) {
        if (edge.animated()) edge.stop();
        edge.removeStyle();
      }
    }
    drawnEdgesRef.current = [];
    const dot = pulseRef.current;
    if (dot) dot.style.opacity = "0";
  }

  /** Hover feel: 1.06 scale (transform-equivalent via width/height) plus a
   *  static glow — the glow stays, the scaling drops under reduced motion. */
  function setHover(node: NodeSingular, on: boolean): void {
    node.toggleClass("hov", on);
    if (prefersReducedMotion()) return;
    const base = (node.data("_baseSize") as { w: number; h: number } | undefined) ?? {
      w: node.numericStyle("width"),
      h: node.numericStyle("height"),
    };
    node.data("_baseSize", base);
    const k = on ? 1.06 : 1;
    node.animate(
      { style: { width: base.w * k, height: base.h * k } },
      { duration: 160, easing: "ease-out-cubic" },
    );
  }

  /** A dot walks the chain node → edge-midpoint → node, pausing between
   *  laps while the path stays lit. Reduced motion: never starts. */
  function startPulse(chain: Chain, token: number): void {
    const cy = cyRef.current;
    const dot = pulseRef.current;
    if (!cy || !dot || prefersReducedMotion()) return;
    const start = cy.getElementById(`n${chain.nodes[0]}`).renderedPosition();
    const points: Array<{ x: number; y: number }> = [{ x: start.x, y: start.y }];
    chain.edges.forEach((edgeId, index) => {
      const edge = cy.getElementById(`e${edgeId}`);
      if (!edge.empty()) {
        const mid = edge.renderedMidpoint();
        points.push({ x: mid.x, y: mid.y });
      }
      const node = cy.getElementById(`n${chain.nodes[index + 1]}`);
      if (!node.empty()) {
        const pos = node.renderedPosition();
        points.push({ x: pos.x, y: pos.y });
      }
    });
    if (points.length < 2) return;

    const lap = (): void => {
      if (traceTokenRef.current !== token) return;
      dot.style.transition = "none";
      dot.style.transform = `translate(${points[0].x}px, ${points[0].y}px)`;
      dot.style.opacity = "0";
      window.requestAnimationFrame(() => {
        if (traceTokenRef.current !== token) return;
        dot.style.transition = "opacity 160ms linear";
        dot.style.opacity = "1";
        let i = 0;
        const hop = (): void => {
          if (traceTokenRef.current !== token) return;
          if (i >= points.length - 1) {
            traceTimersRef.current.push(
              window.setTimeout(() => {
                dot.style.opacity = "0";
              }, 260),
              window.setTimeout(lap, 900),
            );
            return;
          }
          const a = points[i];
          const b = points[i + 1];
          const dist = Math.hypot(b.x - a.x, b.y - a.y);
          const dur = Math.min(380, Math.max(140, dist / 0.55));
          dot.style.transition = `transform ${dur}ms linear`;
          dot.style.transform = `translate(${b.x}px, ${b.y}px)`;
          i += 1;
          traceTimersRef.current.push(window.setTimeout(hop, dur));
        };
        hop();
      });
    };
    lap();
  }

  /** Dash-draw the lit chain (600ms sweep, 60ms per-hop stagger), then
   *  hand over to the pulse. Runs only once the layout has settled —
   *  otherwise the chain stays pending for the layoutstop callback. */
  function maybeStartTrace(): void {
    const cy = cyRef.current;
    const container = containerRef.current;
    const chain = pendingChainRef.current;
    if (!cy || !container || !chain) return;
    if (container.dataset.settled !== "1") return;
    pendingChainRef.current = null;
    if (prefersReducedMotion()) return;
    const token = traceTokenRef.current + 1;
    traceTokenRef.current = token;
    const edges = chain.edges
      .map((id) => cy.getElementById(`e${id}`) as EdgeSingular)
      .filter((edge) => !edge.empty());
    if (edges.length === 0) return;
    edges.forEach((edge, index) => {
      const len = Math.max(16, renderedLen(edge) || 120);
      edge.style({
        "line-style": "dashed",
        "line-dash-pattern": [len, len],
        "line-dash-offset": -len,
      });
      // Stagger via timers (the bundled .d.ts omits `delay` on animate).
      traceTimersRef.current.push(
        window.setTimeout(() => {
          if (traceTokenRef.current !== token) return;
          edge.animate(
            { style: { "line-dash-offset": 0 } },
            { duration: 600, easing: "ease-out-cubic" },
          );
        }, index * 60),
      );
    });
    drawnEdgesRef.current = edges;
    const total = 600 + (edges.length - 1) * 60 + 80;
    traceTimersRef.current.push(
      window.setTimeout(() => {
        if (traceTokenRef.current !== token) return;
        for (const edge of edges) {
          if (!edge.removed()) edge.removeStyle();
        }
        drawnEdgesRef.current = [];
        startPulse(chain, token);
      }, total),
    );
  }

  /** Replay ripple: a ring expands at the subject node (focus, else first). */
  function showRipple(): void {
    const cy = cyRef.current;
    const ring = ringRef.current;
    if (!cy || !ring || !payload || payload.nodes.length === 0) return;
    const subject = payload.nodes.find((node) => node.focus) ?? payload.nodes[0];
    const node = cy.getElementById(`n${subject.id}`);
    if (node.empty()) return;
    const pos = node.renderedPosition();
    ring.style.left = `${pos.x}px`;
    ring.style.top = `${pos.y}px`;
    ring.classList.remove(styles.ringPlay);
    void ring.offsetWidth; // restart the CSS animation
    ring.classList.add(styles.ringPlay);
  }

  // Init once; events read the latest callbacks through refs.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const cy = cytoscape({
      container,
      elements: [],
      // Deliberate: gentler zoom for demos. cytoscape prints a known
      // one-line startup warning for any non-default wheelSensitivity; we
      // accept it (same call as v1, REVIEW-M4).
      wheelSensitivity: 0.2,
      minZoom: 0.08,
      maxZoom: 3,
      style: STYLE,
      layout: { name: "preset" },
    });
    cyRef.current = cy;

    cy.on("tap", "node", (event) => {
      const id = event.target.data("nodeId") as number | undefined;
      const node = id === undefined ? undefined : nodesRef.current.get(id);
      if (node) onSelectRef.current?.(node);
    });
    cy.on("tap", (event) => {
      if (event.target === cy) onSelectRef.current?.(null);
    });
    cy.on("mouseover", "node", (event) => {
      container.style.cursor = "pointer";
      setHover(event.target as NodeSingular, true);
    });
    cy.on("mouseout", "node", (event) => {
      container.style.cursor = "";
      setHover(event.target as NodeSingular, false);
    });
    // User viewport gestures park the signal pulse (it would trail stale
    // coordinates); the lit path itself stays exactly as drawn.
    cy.on("dragpan scrollzoom pinchzoom", () => {
      stopTrace();
    });

    const observer = new ResizeObserver(() => cy.resize());
    observer.observe(container);

    return () => {
      observer.disconnect();
      stopTrace();
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  // Elements: replace + re-layout whenever the payload identity changes.
  useEffect(() => {
    const cy = cyRef.current;
    const container = containerRef.current;
    if (!cy || !container) return;
    stopTrace();
    container.dataset.settled = "0";
    cy.elements().remove();
    if (!payload || payload.nodes.length === 0) {
      nodesRef.current = new Map();
      container.dataset.settled = "1";
      return;
    }
    nodesRef.current = new Map(payload.nodes.map((node) => [node.id, node]));
    cy.add(toCyElements(payload));
    cy.resize();
    const reduce = prefersReducedMotion();
    if (ripple && !reduce) {
      // Replay: the new subgraph fades in under its coming ripple ring.
      container.style.transition = "none";
      container.style.opacity = "0";
      window.requestAnimationFrame(() => {
        container.style.transition = "opacity 280ms ease-out";
        container.style.opacity = "1";
      });
    }
    // Replay graphs are hub-shaped (a subject + its evidence): concentric is
    // deterministic and collision-tidy; cose keeps its organic feel in the
    // Explorer (default spread).
    const layout =
      spread === "wide"
        ? cy.layout({
            name: "concentric",
            animate: !reduce,
            animationDuration: 420,
            animationEasing: "ease-out-cubic",
            minNodeSpacing: 72,
            equidistant: true,
            avoidOverlap: true,
            padding: 36,
          } as unknown as LayoutOptions)
        : cy.layout({
            name: "cose",
            animate: !reduce,
            animationDuration: 350,
            animationEasing: "ease-out-cubic",
            nodeRepulsion: 16000,
            idealEdgeLength: 104,
            gravity: 0.55,
            componentSpacing: 72,
            padding: 30,
            numIter: 900,
            nodeOverlap: 14,
          } as unknown as LayoutOptions);
    const fitPadding = spread === "wide" ? 42 : 26;
    layout.one("layoutstop", () => {
      container.dataset.settled = "1";
      if (ripple && !reduce) {
        // Camera fit for replay: a short glide, then the subject ring.
        cy.animate(
          { fit: { eles: cy.elements(), padding: fitPadding } },
          {
            duration: 320,
            easing: "ease-out-cubic",
            complete: () => {
              showRipple();
            },
          },
        );
      } else {
        cy.fit(undefined, fitPadding);
      }
      maybeStartTrace();
    });
    layout.run();
  }, [payload, spread, ripple]);

  // Spotlight + selection classes.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("dim lit sel");
    if (spotlight) {
      cy.nodes().forEach((node) => {
        const id = node.data("nodeId") as number;
        node.toggleClass("dim", !spotlight.nodes.has(id));
      });
      cy.edges().forEach((edge) => {
        const id = edge.data("edgeId") as number;
        const lit = spotlight.edges.has(id);
        edge.toggleClass("lit", lit);
        edge.toggleClass("dim", !lit);
      });
    }
    if (selectedId != null) {
      cy.getElementById(`n${selectedId}`).addClass("sel");
    }
    // Arm the draw sweep + pulse for Explorer paths — only when the lit
    // chain actually changed, so selecting nodes mid-view does not restart
    // or kill motion that is already running. If the layout has not
    // settled yet, maybeStartTrace is re-invoked from layoutstop.
    const chain =
      draw && spotlight && spotlight.edges.size > 0 && payload
        ? orderChain(payload.edges, spotlight.edges)
        : null;
    const key = chain ? chain.edges.join(",") : "";
    if (key !== activeChainKeyRef.current) {
      activeChainKeyRef.current = key;
      stopTrace();
      pendingChainRef.current = null;
      if (chain && chain.edges.length > 0) {
        pendingChainRef.current = chain;
        maybeStartTrace();
      }
    }
  }, [spotlight, selectedId, payload, draw]);

  function zoomBy(factor: number): void {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({
      level: cy.zoom() * factor,
      renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
    });
  }

  return (
    <>
      <div
        ref={containerRef}
        className={styles.canvas}
        role="img"
        aria-label="Call graph — select a node; its details appear in the inspector"
      />
      <span ref={pulseRef} className={styles.pulse} aria-hidden="true" />
      <span ref={ringRef} className={styles.ring} aria-hidden="true" />
      <div className={styles.controls}>
        <button
          type="button"
          className={styles.control}
          aria-label="Zoom in"
          onClick={() => zoomBy(1.3)}
        >
          +
        </button>
        <button
          type="button"
          className={styles.control}
          aria-label="Zoom out"
          onClick={() => zoomBy(1 / 1.3)}
        >
          −
        </button>
        <button
          type="button"
          className={styles.control}
          aria-label="Fit graph"
          onClick={() => {
            cyRef.current?.fit(undefined, 26);
          }}
        >
          fit
        </button>
      </div>
    </>
  );
}

/** Cytoscape canvas — "Ink & Signal" restyle with selection spotlight.
 *
 *  Data contract: elements carry `nodeId` / `edgeId` (server ids; canvas ids
 *  are prefixed to dodge node/edge id-space collisions). When a layout
 *  settles the container sets `data-settled="1"` — capture tooling waits on
 *  that instead of guessing timings. De-collision recipe (REVIEW-M4):
 *  repulsion 16k, edge 104, nodeOverlap 14, min-zoomed-font-size 7,
 *  text-max-width 96px, ellipsis. */

import cytoscape from "cytoscape";
import type { Core, Css, LayoutOptions, StylesheetJson } from "cytoscape";
import { useEffect, useRef } from "react";

import { toCyElements } from "../lib/graph";
import type { Spotlight } from "../lib/graph";
import type { GraphNode, GraphPayload } from "../lib/types";
import styles from "./GraphCanvas.module.css";

/** Runtime-supported style props the bundled .d.ts omits (the shadow
 *  family that draws the amber glow). Kept in one helper so the rest of
 *  the sheet stays fully typed. */
function glow(props: Record<string, number | string>): Css.Node | Css.Edge {
  return props as unknown as Css.Node;
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
    }),
  },
  { selector: ".dim", style: { opacity: 0.35 } },
];

interface Props {
  payload: GraphPayload | null;
  spotlight?: Spotlight | null;
  selectedId?: number | null;
  onSelect?: (node: GraphNode | null) => void;
  /** "wide": roomier forces for hub-shaped replay graphs (Ask canvas). */
  spread?: "normal" | "wide";
}

export function GraphCanvas({ payload, spotlight, selectedId, onSelect, spread = "normal" }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const nodesRef = useRef<Map<number, GraphNode>>(new Map());
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

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
    cy.on("mouseover", "node", () => {
      container.style.cursor = "pointer";
    });
    cy.on("mouseout", "node", () => {
      container.style.cursor = "";
    });

    const observer = new ResizeObserver(() => cy.resize());
    observer.observe(container);

    return () => {
      observer.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  // Elements: replace + re-layout whenever the payload identity changes.
  useEffect(() => {
    const cy = cyRef.current;
    const container = containerRef.current;
    if (!cy || !container) return;
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
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
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
      cy.fit(undefined, fitPadding);
      container.dataset.settled = "1";
    });
    layout.run();
  }, [payload, spread]);

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
  }, [spotlight, selectedId, payload]);

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

/** Ownership view helpers — pure and unit-tested (M4R ownership).
 *
 *  The server does every ranking; this module only builds request URLs and
 *  derives display values. Honesty rule: the printed share is always the
 *  exact fraction the server returned; bars are the glance, never the
 *  record. A bar is scaled to the widest row in its list (the top author),
 *  so a 2% sliver stays visible next to a 63% leader. */

export interface OwnershipOptions {
  file?: string | null;
  symbol?: string | null;
}

/** Request URL for `/api/graph/ownership` — repo scope when unscoped. */
export function ownershipPath(options: OwnershipOptions = {}): string {
  const params = new URLSearchParams();
  if (options.file) params.set("file", options.file);
  if (options.symbol) params.set("symbol", options.symbol);
  const qs = params.toString();
  return `/api/graph/ownership${qs ? `?${qs}` : ""}`;
}

/** 0.633 -> "63.3%" — one decimal, printed for every row. */
export function sharePct(share: number): string {
  return `${(share * 100).toFixed(1)}%`;
}

/** Bar width as a percent of the widest row (leader = 100). A floor of 2%
 *  keeps a nonzero share from rendering as a hairline of nothing; the
 *  printed percentage is what a reader should trust. */
export function barWidth(share: number, maxShare: number): number {
  if (maxShare <= 0 || share <= 0) return 0;
  return Math.max(2, Math.round((share / maxShare) * 1000) / 10);
}

export interface MixSegment {
  key: string;
  label: string;
  share: number;
  rest: boolean;
}

/** Stacked owner-mix segments for a file row: the top `max` owners plus an
 *  aggregated remainder — only when a remainder actually exists (a fully
 *  covered list must not grow a phantom "others" band). `authorsTotal` is
 *  the file's true author count; when it exceeds the listed owners, the
 *  remainder is labeled as the long tail the display cap hid. */
export function mixSegments(
  owners: { name: string; share: number }[],
  max = 3,
  authorsTotal?: number,
): MixSegment[] {
  if (owners.length === 0) return [];
  const top = owners.slice(0, Math.max(0, max));
  const segments: MixSegment[] = top.map((owner) => ({
    key: `o:${owner.name}`,
    label: owner.name,
    share: owner.share,
    rest: false,
  }));
  const covered = top.reduce((sum, owner) => sum + owner.share, 0);
  const remainder = Math.round((1 - covered) * 1000) / 1000;
  if (remainder >= 0.005) {
    const hidden = Math.max(0, (authorsTotal ?? owners.length) - top.length);
    segments.push({
      key: "rest",
      label: hidden > 0 ? "others" : "unattributed",
      share: remainder,
      rest: true,
    });
  }
  return segments;
}

/** Initials for the owner dot: "Armin Ronacher" -> "AR", "pgjones" -> "PG",
 *  "dependabot[bot]" -> "DE". Falls back to "?" for a name with no letters. */
export function ownerInitials(name: string): string {
  const words = name
    .split(/[\s._-]+/)
    .map((word) => word.replace(/[^\p{L}\p{N}]/gu, ""))
    .filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return `${words[0][0]}${words[1][0]}`.toUpperCase();
}


/** The KiroCrew ON mark (owner 2026-10-24: square, flat, technical — no rounded shapes): four squares, the left two
 * teal as the K's stem, the right two cut by the K's arms — a crew of agents working as one. */
export function Logo({ size = 32, className }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 30 30" role="img" aria-label="KiroCrew ON" className={className}>
      <rect width="14" height="14" fill="#14b8a6" />
      <rect x="16" width="14" height="14" fill="#334155" />
      <rect y="16" width="14" height="14" fill="#14b8a6" />
      <rect x="16" y="16" width="14" height="14" fill="#5eead4" />
      <polygon points="16,14 30,0 30,6 22,14" className="fill-sidebar" />
      <polygon points="16,16 22,16 30,24 30,30" className="fill-sidebar" />
    </svg>
  );
}

// Placement maths for the sidebar "?" tips. Framework-free and separate from the
// JSX so tests/test_helptip.mjs can drive it: the repo tests plain .js modules,
// not components, and this is the part with real edge cases -- a tip anchored
// near the right edge or near the foot of a long sidebar.

export const HELPTIP_W = 340;   // panel width; mirrored by .helptip-body in styles.css
export const HELPTIP_GAP = 6;   // vertical space between the icon and the panel
export const HELPTIP_MARGIN = 8; // smallest allowed distance to a window edge

/** Where the panel's top-left corner goes.
 *
 * `anchor` is the icon's viewport rect, `view` is {width, height}. `bodyHeight`
 * is null on the first pass, when the panel has not been measured yet; pass the
 * measured height on the second pass and the panel flips above the icon if it
 * would otherwise run off the bottom.
 */
export function helpTipPosition(anchor, view, bodyHeight = null) {
  // Left-align with the icon, then pull back so the panel never leaves the
  // window. The right clamp wins, because a tip half off-screen is unreadable
  // whereas one nudged a few pixels left of its icon is not.
  const left = Math.max(
    HELPTIP_MARGIN,
    Math.min(anchor.left - 4, view.width - HELPTIP_W - HELPTIP_MARGIN),
  );
  const below = anchor.bottom + HELPTIP_GAP;
  if (bodyHeight == null) return { left, top: below };
  const overflows = below + bodyHeight > view.height - HELPTIP_MARGIN;
  // Flipping above is only an improvement if there is room up there; when the
  // window is shorter than the panel, pinning to the top margin is the least-bad
  // option and keeps the first lines readable.
  const top = overflows
    ? Math.max(HELPTIP_MARGIN, anchor.top - bodyHeight - HELPTIP_GAP)
    : below;
  return { left, top };
}

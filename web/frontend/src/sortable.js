// Column sorting for the Results tables. Kept framework-free and separate from
// the JSX so it can be exercised by tests/test_results_sort.mjs -- the repo tests
// plain .js modules, not components.
//
// A column spec is { key, label, value(row), numeric? }. `value` pulls the sort
// key out of a row, so the same helper serves a table of arrays and a table of
// objects.

/** Direction a column should take when it is clicked. A numeric column opens
 *  DESCENDING -- for a cell count the interesting end is the big one -- and a
 *  text column opens ascending. Clicking the column that is already active
 *  reverses it. */
export function nextSort(col, sort) {
  return sort && sort.key === col.key
    ? { key: col.key, dir: sort.dir === "asc" ? "desc" : "asc" }
    : { key: col.key, dir: col.numeric ? "desc" : "asc" };
}

/** `rows` ordered by `sort`, or `rows` unchanged when the sort names no known
 *  column. Never mutates the input. */
export function sortRows(rows, cols, sort) {
  const col = sort && cols.find((c) => c.key === sort.key);
  if (!col) return rows;
  const sign = sort.dir === "asc" ? 1 : -1;
  // Decorated with the original index so equal keys keep the order they arrived
  // in. Array.prototype.sort is stable in modern engines, but saying so in the
  // comparator means the table does not quietly depend on that.
  return rows
    .map((row, i) => [row, i])
    .sort(([a, ia], [b, ib]) => {
      const x = col.value(a), y = col.value(b);
      if (x === y) return ia - ib;
      // Missing values sort last in BOTH directions: a blank is not "small",
      // and flipping the arrow should not parade the blanks to the top.
      if (x == null) return 1;
      if (y == null) return -1;
      const c = (typeof x === "number" && typeof y === "number")
        ? x - y
        : String(x).localeCompare(String(y), undefined, { numeric: true });
      return c * sign;
    })
    .map(([row]) => row);
}


/** One page of `rows`, and the page actually shown.
 *
 * `page` is clamped rather than trusted: sorting, a smaller job or a filter can
 * leave the caller holding a page number that no longer exists, and silently
 * showing an empty table is worse than snapping to the last real page.
 * Returns { rows, page, pages, from, to, total } with `from`/`to` 1-based and
 * inclusive, for a "21-30 of 136" readout.
 */
export function pageSlice(rows, page, size) {
  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const p = Math.min(Math.max(0, page | 0), pages - 1);
  const start = p * size;
  return {
    rows: rows.slice(start, start + size),
    page: p, pages, total,
    from: total ? start + 1 : 0,
    to: Math.min(start + size, total),
  };
}

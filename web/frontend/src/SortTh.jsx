import React from "react";
import { nextSort } from "./sortable";

// A sortable column header: the label, and a marker that says which way the
// column is sorted (or that it can be). Shared by the Results tables and the
// Resume list, so every sortable table in the app behaves the same way.
export default function SortTh({ col, sort, onSort, style }) {
  const active = sort.key === col.key;
  const next = nextSort(col, sort);
  return (
    <th style={style}
        aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className="sortth" onClick={() => onSort(next)}
              title={`Sort by ${col.label}, ${next.dir === "asc" ? "ascending" : "descending"}`}
              // right-aligned columns read better with the marker on the left
              style={{ flexDirection: style?.textAlign === "right" ? "row-reverse" : "row" }}>
        {col.label}
        <span className="sortarrow" aria-hidden="true">
          {active ? (sort.dir === "asc" ? "▲" : "▼") : "⇅"}
        </span>
      </button>
    </th>
  );
}

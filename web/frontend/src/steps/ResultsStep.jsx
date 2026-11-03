import React from "react";
import { sortRows, pageSlice } from "../sortable";
import HelpTip from "../HelpTip";
import SortTh from "../SortTh";
import CellCanvas from "../CellCanvas";
import OverlayCanvas, { PillSection } from "../OverlayCanvas";
import SectionCorrCanvas from "../SectionCorrCanvas";
import { levelVerdict } from "../sectionCorr";
import { toggleDivision } from "../overlayPlace";
import { overhangSummary } from "../jobapi";

// Hex-bin QC request parameters. Defaults live here so the fetch, the caption
// and the control can never disagree about what the user is looking at.
const HEX_SIZE_UM = 100;
// Fixed, not user-selectable: on a well-populated coronal section this barely
// binds (2,077 bins at 5 vs 1,887 at 20, median r 0.243 vs 0.247), so exposing
// it added a decision without adding useful control.
const HEX_MIN_CELLS = 10;

// Bin size trades spatial detail against per-bin statistical reliability, so
// each option says what it costs as well as what it buys.
const HEX_SIZE_OPTIONS = [
  { v: 100, label: "100 um",
    note: "Detailed: resolves cortical layers and the hippocampal ridge at ~50 cells per bin. The default." },
  // 300 um is switched OFF at the user's request (2026-09-21), not removed. The
  // backend still accepts any size between 100 and 1000, so restoring this line
  // is the whole change. Kept because the trade-off it names is real: ~9x more
  // cells per bin and higher correlations, at the cost of averaging away the
  // layer-scale structure the 100 um map exists to show.
  // { v: 300, label: "300 um",
  //   note: "Smoothest: ~9x more cells per bin and the highest correlations, at the cost of averaging away layer-scale structure." },
];

// `job` is what renames the download to the job's label; the server falls back
// to the on-disk name without it, so passing it is an improvement rather than a
// requirement.
function dl(path, job) {
  return `/api/download?path=${encodeURIComponent(path)}`
       + (job ? `&job=${encodeURIComponent(job)}` : "");
}

// A compact download affordance placed beside the figure or table it belongs
// to. QC artifacts used to live only in the Downloads block at the bottom of
// the step, which meant scrolling away from the thing you were looking at and
// then matching it to a filename; anchoring each one to its own output removes
// both steps. /api/download sets Content-Disposition, so a plain link saves.
function DlChip({ path, job, children }) {
  if (!path) return null;
  // The verb is spelled out rather than carried by a glyph: a bare arrow
  // renders as a hairline in the default UI font stack and does not say what
  // the pill saves. The component owns the "Download " prefix so call sites
  // pass only the artifact name and every chip reads the same way.
  return (
    <a className="dlchip" href={dl(path, job)}>Download {children}</a>
  );
}

// ---------------------------------------------------------------------------
// Sortable tables. Column specs live at module scope so they are stable across
// renders and can be a useMemo dependency without churning.
//
// `value` pulls the sort key out of a row. A numeric column opens DESCENDING on
// the first click -- for "Cells" the interesting end is the big one -- and a text
// column opens ascending. Clicking the active column again reverses it.

const REGION_COLS = [
  { key: "acronym", label: "Allen acronym", value: (r) => r[0] },
  { key: "name", label: "Region name", value: (r) => r[1] },
  { key: "cells", label: "Cells", value: (r) => r[2] || 0, numeric: true },
  // "% of mapped" is cells / n, so it orders identically to cells; it is still
  // clickable because the reader should not have to work that out.
  { key: "pct", label: "% of mapped", value: (r) => r[2] || 0, numeric: true },
];

// The same columns plus the automatic counts and their delta, used when the job
// has a registration result to compare against. A separate constant rather than a
// conditional inside REGION_COLS so both stay stable identities for useMemo.
const NO_ROWS = [];   // stable identity: a fresh [] would churn useMemo
const CONF_PAGE = 10;  // rows per page in the region-by-region table

const CONFUSION_COLS = [
  { key: "mapped", label: "mapped", value: (c) => c.region },
  // cells first, before the scores: a 76-cell profile and a 1,560-cell one
  // deserve different amounts of belief, and that should be read before the r.
  { key: "cells", label: "cells", value: (c) => c.n_cells, numeric: true },
  { key: "self", label: "self r", value: (c) => c.self_corr, numeric: true },
  { key: "best_match", label: "best match", value: (c) => c.best_match },
  { key: "best", label: "best r", value: (c) => c.best_corr, numeric: true },
  // the margin, last, because it is the conclusion the other columns support
  { key: "gap", label: "gap", value: (c) => c.gap, numeric: true },
];

function useSorted(rows, cols, initial = { key: null, dir: "desc" }) {
  const [sort, setSort] = React.useState(initial);
  const sorted = React.useMemo(
    () => sortRows(rows, cols, sort), [rows, cols, sort]);
  return [sorted, sort, setSort];
}

export default function ResultsStep({ job, onRestart }) {
  const man = job.manual?.result || {};
  const reg = job.register?.result || {};
  const cov = man.coverage ?? reg.coverage;
  const n = man.n_mapped ?? reg.n_mapped;
  // The automatic coverage, kept separate rather than collapsed into `cov` by
  // the ?? above. The pair is the diagnostic: curation that DROPS coverage has
  // warped tissue off the atlas, and showing only the curated number cannot say
  // so. Only meaningful when the job actually has both.
  const autoCov = (man.coverage != null && reg.coverage != null)
    ? reg.coverage : null;
  const covDelta = autoCov != null ? cov - autoCov : null;
  const total = man.n_total ?? (cov ? Math.round(n / cov) : n);
  const ap = job.ap_idx ?? reg.ap;
  // The CURATED figure when there is one, never a mix: an older curated job has
  // no field, and quietly falling back to the automatic number would describe a
  // different alignment from the rest of this record.
  const overhang = overhangSummary(
    man.coverage != null ? man.atlas_overhang : reg.atlas_overhang);
  const aff = job.manual?.affine || null;
  const affApplied = !!aff && (
    Math.abs(aff.tx) >= 1 || Math.abs(aff.ty) >= 1 ||
    Math.abs(aff.rot_deg) >= 0.05 ||
    Math.abs(aff.sx - 1) >= 1e-3 || Math.abs(aff.sy - 1) >= 1e-3);
  const top = man.top_regions || reg.top_regions || [];
  const warpJson = job.manual?.warp_json;
  // local warp grid stats, as REPORTED BY THE BACKEND finalize (not the browser):
  // finalize_composite re-validates the grid and returns what it actually applied.
  const warp = man.warp || null;
  const warpApplied = !!(warp && warp.applied);
  const atlasMeta = man.atlas_provenance || reg.atlas_provenance;

  // Final curated figure (confirmed manual warp + labels); fall back to the auto
  // preview for older jobs that predate the curated render.
  const curatedFig = man.fig_path;
  // Every panel on its own at full width, plus SVG for the two overlay panels,
  // in one archive -- six separate chips would be worse than one.
  const figZip = man.figure_zip_path;
  // The automatic figure is rendered on request, not during registration, so it
  // may not exist yet. `autoOn` holds what the button produced this session.
  const [autoOn, setAutoOn] = React.useState(null);
  const [autoBusy, setAutoBusy] = React.useState(false);
  const [autoErr, setAutoErr] = React.useState(null);
  const autoZip = autoOn?.zip || reg.figure_zip_path;
  const autoFig = autoOn?.png || reg.fig_path;
  const [figView, setFigView] = React.useState("curated");
  const [showAll, setShowAll] = React.useState(false);
  // `top` already arrives sorted by cell count descending, so seeding the sort
  // there means the indicator describes the table from the first paint instead
  // of appearing only after a click.
  // Region composition shows the CURATED counts only. The auto-vs-curated
  // columns were removed on 2026-09-22: the comparison belongs to the Auto view
  // of the figure, and inside a composition table it read as four columns of
  // arithmetic rather than as anatomy.
  const [regionRows, regionSort, setRegionSort] =
    useSorted(top, REGION_COLS, { key: "cells", dir: "desc" });
  const shownFig = figView === "curated" ? curatedFig || autoFig : autoFig || curatedFig;

  const [qc, setQc] = React.useState(null);
  // Declared unconditionally even though the table below is conditional --
  // hooks cannot sit inside the `qc && qc.available` branch that renders it.
  // Every scored region, not the six worst: the six were a summary of a table
  // nobody could see. Seeded worst-self-r-first so page 1 still opens on the
  // problems, which is what the six used to show.
  const scoredAll = qc?.scored || qc?.confusions || NO_ROWS;
  // `self_is_best` only exists on the newer `scored` payload. An older cached
  // result (or a server that has not been restarted) has `confusions` only, and
  // filtering on an absent field would silently empty the table -- so the toggle
  // is offered only when the field is actually there.
  const hasScored = Array.isArray(qc?.scored) && qc.scored.length > 0;
  const [confOnly, setConfOnly] = React.useState(false);
  const nConfusable = React.useMemo(
    () => scoredAll.filter((c) => c.self_is_best === false).length, [scoredAll]);
  const confSource = React.useMemo(
    () => (confOnly ? scoredAll.filter((c) => c.self_is_best === false) : scoredAll),
    [scoredAll, confOnly]);
  const [confusionRows, confusionSort, setConfusionSort] =
    useSorted(confSource, CONFUSION_COLS, { key: "self", dir: "asc" });
  const [confPage, setConfPage] = React.useState(0);
  const confView = pageSlice(confusionRows, confPage, CONF_PAGE);
  // a new ordering invalidates the page you were on
  const sortConfusions = (next) => { setConfusionSort(next); setConfPage(0); };

  // ---- Whole-section expression correlation ------------------------------
  // The tissue-level check: does this section look like brain at this AP at
  // all. Loaded with the page because it needs only the panel counts -- no
  // labels, no registration -- so it is the one QC here that is independent of
  // everything the alignment did.
  const [scorr, setScorr] = React.useState(null);
  React.useEffect(() => {
    let live = true;
    fetch(`/api/jobs/${job.id}/section_corr`)
      .then((r) => r.json())
      .then((d) => { if (live) setScorr(d); })
      .catch((e) => { if (live) setScorr({ available: false, reason: String(e) }); });
    return () => { live = false; };
  }, [job.id]);
  const apCheck = React.useMemo(
    () => (scorr?.available
      ? levelVerdict(scorr.best, scorr.query_mm, scorr.axis, scorr.r) : null),
    [scorr]);

  // ---- Interactive depth-4 overlay panels -------------------------------
  // These redraw the SAME boundaries the curation step draws, through the SAME
  // transform, rather than reading a saved curated raster -- there isn't one.
  // The bundle holds the automatic label plane; "curated" is that plane plus the
  // confirmed affine + CSRBF, which is what /transform returns. So the Auto view
  // is simply the identity transform, and the two views differ by exactly the
  // curation edits and nothing else.
  const [xform, setXform] = React.useState(null);
  const [bmeta, setBmeta] = React.useState(null);
  const [bounds, setBounds] = React.useState(null);
  const [mkLegend, setMkLegend] = React.useState([]);
  const [mkOn, setMkOn] = React.useState(null);   // null = every division
  React.useEffect(() => {
    let live = true;
    fetch(`/api/jobs/${job.id}/transform`)
      .then((r) => r.json())
      .then((t) => { if (live) setXform(t); })
      .catch(() => { if (live) setXform({ available: false }); });
    return () => { live = false; };
  }, [job.id]);
  const bpath = xform?.bundle_path || null;
  React.useEffect(() => {
    if (!bpath) return undefined;
    let live = true;
    const q = "?path=" + encodeURIComponent(bpath);
    fetch("/api/bundle" + q).then((r) => r.json())
      .then((m) => { if (!live) return;
        setBmeta(m);
        if (m.has_markers) {
          fetch("/api/markers" + q).then((r) => r.json())
            .then((r) => live && setMkLegend((r.regions || []).filter((x) => x.used)))
            .catch(() => {});
        }
      }).catch(() => {});
    fetch(`/api/boundaries?depth=depth4&path=${encodeURIComponent(bpath)}`)
      .then((r) => r.json()).then((b) => live && setBounds(b)).catch(() => {});
    return () => { live = false; };
  }, [bpath]);
  // Auto view = no manual transform. The boundaries already arrive in the
  // bundle's own frame, which IS the automatic alignment.
  const overlayXform = figView === "auto" ? null : xform;
  // `v` is the bundle's content fingerprint, and it is load-bearing rather than
  // cosmetic. Jobs on one section SHARE a bundle file that every registration
  // overwrites in place, and these responses carry max-age=3600, so without it
  // the browser happily reuses the DAPI it fetched for a previous job -- a
  // background from a different AP, under correctly-placed regions. That is
  // exactly the "curation looked aligned, results look shifted" report.
  const dapiUrl = bpath && bmeta?.has_dapi
    ? `/api/dapi.png?path=${encodeURIComponent(bpath)}`
      + `&v=${encodeURIComponent(bmeta.fp || "")}` : null;
  // Without DAPI, the first overlay sits on cell density instead -- the same
  // grayscale background curation showed -- rather than on nothing.
  // density.png is served no-store, so it needs no cache key.
  const densityUrl = bpath && bmeta && !bmeta.has_dapi
    ? `/api/density.png?path=${encodeURIComponent(bpath)}` : null;
  // The marker composite is rendered server-side per selection, so a division
  // toggle means re-fetching it; the server caches per selection, so flicking one
  // off and back on is instant after the first time.
  const markerUrl = bpath && bmeta?.has_markers
    ? `/api/marker_density.png?path=${encodeURIComponent(bpath)}`
      + `&v=${encodeURIComponent(bmeta.fp || "")}`
      + (mkOn ? `&regions=${encodeURIComponent(mkOn.join(","))}` : "")
    : null;
  const toggleMarker = (acro) =>
    setMkOn(toggleDivision(mkLegend.map((r) => r.acronym), mkOn, acro));

  // Per-cell data for the interactive panels, fetched on request rather than with
  // the page: ~0.35 MB gzipped is cheap but not free, and the static figure above
  // already answers most visits.
  const [cells, setCells] = React.useState(null);
  const [cellsBusy, setCellsBusy] = React.useState(false);
  function loadCells() {
    setCellsBusy(true);
    fetch(`/api/jobs/${job.id}/cells?which=${figView === "auto" ? "auto" : "curated"}`)
      .then((r) => r.json())
      .then(setCells)
      .catch((e) => setCells({ available: false, reason: String(e) }))
      .finally(() => setCellsBusy(false));
  }
  // Loaded with the page rather than on a button. It is ~0.35 MB gzipped, which
  // is less than the marker PNG the curation step already fetches, and a panel
  // nobody presses a button for is a panel nobody uses. Re-fetched when the
  // Auto/Curated toggle changes so it always shows the same alignment as the
  // figure above it.
  React.useEffect(() => { loadCells(); }, [job.id, figView]);

  function buildAutoFigure() {
    setAutoBusy(true); setAutoErr(null);
    fetch(`/api/jobs/${job.id}/auto_figure`, { method: "POST" })
      .then((r) => (r.ok ? r.json()
                         : r.json().then((e) => Promise.reject(e.detail || r.statusText))))
      .then((r) => {
        if (r.available) { setAutoOn(r); setFigView("auto"); }
        else setAutoErr(r.reason || "not available");
      })
      .catch((e) => setAutoErr(String(e)))
      .finally(() => setAutoBusy(false));
  }
  // same reason as sorting: the page you were on does not survive the change
  const toggleConfOnly = () => { setConfOnly((v) => !v); setConfPage(0); };
  const [qcLoading, setQcLoading] = React.useState(false);
  const [qcErr, setQcErr] = React.useState(null);

  function runQc() {
    setQcLoading(true);
    setQcErr(null);
    return fetch(`/api/jobs/${job.id}/qc`)
      .then((r) => (r.ok ? r.json() : r.json().then((e) => Promise.reject(e.detail || r.statusText))))
      .then(setQc)
      .catch((e) => setQcErr(String(e)))
      .finally(() => setQcLoading(false));
  }

  const pass = qc && qc.available && qc.verdict === "PASS";

  const [exp, setExp] = React.useState(null);
  const [expLoading, setExpLoading] = React.useState(false);
  const [expErr, setExpErr] = React.useState(null);

  function runExports() {
    setExpLoading(true);
    setExpErr(null);
    return fetch(`/api/jobs/${job.id}/region_exports`)
      .then((r) => (r.ok ? r.json() : r.json().then((e) => Promise.reject(e.detail || r.statusText))))
      .then(setExp)
      .catch((e) => setExpErr(String(e)))
      .finally(() => setExpLoading(false));
  }

  // Region specificity and Per-region agreement start as soon as the step opens. Both are
  // cheap to repeat -- the server caches each result against the mapped CSV's
  // (mtime, size) -- and neither takes a parameter, so making the reviewer click
  // for them only delayed information they always want. Spatial hex-bin QC stays
  // manual: it takes a bin size, so it should not run before that is chosen.
  const autoRan = React.useRef(null);
  React.useEffect(() => {
    if (autoRan.current === job.id) return;   // one kick-off per job, not per render
    autoRan.current = job.id;
    // Sequential, not parallel. Each endpoint reads the whole cell_by_gene.csv
    // (196 MB on the 500-gene panel measured here; 8.3 s and 4.5 s), so firing
    // both at once would hold two copies in server memory to save a few seconds
    // the reviewer spends reading the figure anyway.
    setExpLoading(true);
    // Hex QC joins the chain rather than waiting for a button. It is last
    // because it is the heaviest of the three and the least often decisive, and
    // serialised for the same reason the other two are: each holds a large frame
    // in server memory while it runs.
    runQc().finally(() => runExports().finally(runHexQc));
  }, [job.id]);

  const [hexQc, setHexQc] = React.useState(null);
  const [hexLoading, setHexLoading] = React.useState(false);
  const [hexErr, setHexErr] = React.useState(null);
  const [hexSize, setHexSize] = React.useState(HEX_SIZE_UM);

  function runHexQc() {
    setHexLoading(true);
    setHexErr(null);
    fetch(`/api/jobs/${job.id}/hex_qc?hex_size_um=${hexSize}&min_cells=${HEX_MIN_CELLS}`)
      .then((r) => (r.ok ? r.json() : r.json().then((e) => Promise.reject(e.detail || r.statusText))))
      .then(setHexQc)
      .catch((e) => setHexErr(String(e)))
      .finally(() => setHexLoading(false));
  }

  return (
    <>
      <div className="sidebar">
        <h1>Results</h1>
        <div className="muted">Final curated per-cell region labels for <b>{job.name}</b>.</div>
        <div className="muted" style={{ marginTop: 10, fontSize: 13 }}>
          Confirm the final manual alignment at the top, then review the statistics,
          QC and downloads below.
        </div>
        {/* One archive of everything this job produced, so a reviewer does not
            have to collect a dozen chips scattered down the page. Built on
            demand: the region tables and hex-bin map are cached on the mapped
            CSV, so this is free once the page has finished loading them. */}
        {/* styling lives in a.primary now, so this matches the New job button
            at the foot of the rail instead of rendering as bare link text */}
        <a className="primary" href={`/api/jobs/${job.id}/report.zip`}
           style={{ marginTop: 12, padding: 10, fontSize: 14 }}>
          Download full report (.zip)
        </a>
        <div className="muted" style={{ marginTop: 6, fontSize: 11 }}>
          Per-cell labels, region tables, GeoJSON, every figure panel from both
          the automatic and curated runs, the hex-bin QC, and a manifest.
        </div>
        {/* How this section was aligned. Kept beside the reference-data block
            rather than under the figure: both answer "what produced this", which
            is a different question from "what does the tissue show", and mixing
            the two made the anatomy compete with a manifest. */}
        <h2>
          Alignment record
          <HelpTip label="About the alignment record">
            <p><b>Cells labelled</b> — cells that landed inside an annotated Allen
              region. The rest fell outside the annotation, most often past the
              section edge, and carry no region.</p>
            <p><b>vs automatic</b> — the same figure before your curation. A drop
              means curated tissue moved off the annotated atlas, which is worth
              checking; a small rise is the usual outcome.</p>
            <p><b>Beyond atlas</b> — cells whose position lies past the end of
              the Allen volume, by end. They are part of the unlabelled count, and
              no alignment can label them: the reference simply stops there. Common
              on sagittal sections, where the AP axis runs across the image and the
              atlas has no margin at either AP end. Shown only when there are any.</p>
            <p><b>Section</b> — shown only for a coronal hemisection declared in
              step 2: the half of the brain the tissue is, as displayed. The
              automatic fit then used only that half of the atlas slice.</p>
            <p><b>Atlas slice</b> — the index on the plane&apos;s own axis: AP for
              coronal, ML for sagittal. It is resolution-dependent, so it changes
              meaning if the atlas resolution ever does.</p>
            <p><b>Global transform</b> — the whole-section affine you set in
              curation. Scale is a multiplier, not a pixel size: 0.967 means the
              atlas was shrunk 3.3% to match the tissue.</p>
            <p><b>Local warp</b> — the CSRBF grid, separate from the affine above.
              <b> min |J|</b> is the smallest Jacobian determinant over the field;
              it stays above zero, and well above it, or the warp would fold
              tissue onto itself.</p>
          </HelpTip>
        </h2>
        <div className="alignrec">
          {n != null && total != null && (
            <div>
              <span>Cells labelled</span>
              <b>{n.toLocaleString()} of {total.toLocaleString()}
                {cov != null && ` (${(cov * 100).toFixed(1)}%)`}</b>
            </div>
          )}
          {covDelta != null && (
            <div>
              <span>vs automatic</span>
              {/* amber only for a real drop; rounding noise either way is not a
                  finding, and colouring it would cry wolf on every job */}
              <b style={{ color: covDelta < -0.005 ? "#ffe17a" : undefined }}>
                {(autoCov * 100).toFixed(1)}% → {covDelta >= 0 ? "+" : "−"}
                {Math.abs(covDelta * 100).toFixed(1)} pts
              </b>
            </div>
          )}
          {overhang && (
            <div>
              <span>Beyond atlas</span>
              <b>{overhang.n.toLocaleString()} cells ({overhang.pct}) · {overhang.ends}</b>
            </div>
          )}
          {ap != null && (
            <div>
              <span>Atlas slice</span>
              <b>{(job.orientation?.plane || "coronal") === "sagittal" ? "ML" : "AP"} {ap}</b>
            </div>
          )}
          {job.orientation?.hemisection && (
            <div>
              <span>Section</span>
              <b>{job.orientation.hemisection} half (hemisection)</b>
            </div>
          )}
          <div>
            <span>Global transform</span>
            <b>{affApplied
              ? `Δ${aff.tx.toFixed(0)}, ${aff.ty.toFixed(0)} µm · `
                + `${aff.rot_deg.toFixed(1)}° · scale ${aff.sx.toFixed(3)} × ${aff.sy.toFixed(3)}`
              : "none"}</b>
          </div>
          <div>
            <span>Local warp</span>
            <b>{warpApplied
              /* denominator = every MOVABLE node (all but the 4 fixed corners),
                 because n_moved counts constrained edge nodes too */
              ? `${warp.n_moved}/${warp.rows * warp.cols - 4} nodes · `
                + `max ${Number(warp.max_disp).toFixed(0)} µm`
                + (warp.min_det != null
                   ? ` · min |J| ${Number(warp.min_det).toFixed(3)}` : "")
              : "none"}</b>
          </div>
        </div>

        {/* Rendering the automatic set costs ~12 s and ~40 MB, and is only wanted
            when someone is actually comparing, so it is built on request rather
            than during every registration. Once built it is cached on the
            bundle's fingerprint and reaches report.zip like any other artifact. */}
        {!autoFig && (
          <button onClick={buildAutoFigure} disabled={autoBusy}
                  style={{ width: "100%", marginTop: 10, padding: "7px 10px",
                           fontSize: 12 }}>
            {autoBusy ? "Rendering automatic result …"
                      : "Compare with automatic alignment"}
          </button>
        )}
        {autoFig && (
          <div className="muted" style={{ marginTop: 8, fontSize: 11 }}>
            Automatic result available — use the Auto / Curated toggle in
            Final curated visualization. It switches the download, the
            interactive panels and the cells they draw.
          </div>
        )}
        {autoErr && (
          <div className="muted" style={{ marginTop: 8, fontSize: 11,
                                          color: "#f85149" }}>
            {autoErr}
          </div>
        )}

        {atlasMeta && (
          <div className="provenance">
            <b>Reference data</b><br />
            {atlasMeta.atlas_id} · dataset v{atlasMeta.dataset_version} ·
            {atlasMeta.resolution_um?.[0] ?? 50} µm<br />
            <a href="https://doi.org/10.1016/j.cell.2020.04.007" target="_blank" rel="noreferrer">
              Wang et al., 2020
            </a> · {atlasMeta.dataset_date}
          </div>
        )}
        <div className="spacer" />
        <button className="primary" onClick={onRestart}
                style={{ width: "100%", padding: 10, fontSize: 14, marginTop: 16 }}>
          New job
        </button>
      </div>

      <div className="canvas-wrap" style={{ display: "block", padding: 24, overflow: "auto" }}>
        {/* ================= SECTION 1 — Final curated visualization =========
            The step is three things: what the alignment looks like, whether it
            holds up, and what you take away. Every heading here used to be an
            h3, so eight peers read as one flat list. This heading is the
            SECTION; "Check the boundaries" and "Explore cells" below are its
            subsections, and it keeps the downloads and the Auto/Curated toggle
            because they apply to everything inside it. */}
        <div className="row section-head first"
             style={{ justifyContent: "space-between", alignItems: "center",
                      marginTop: 4, marginBottom: 8 }}>
          {/* the download sits with the title; the Auto/Curated view toggle is a
              control, not an artifact, so it keeps the right-hand slot */}
          <div className="titlerow">
            {/* the title names what is on screen: the two views are different
                results, not two renderings of one */}
            <h2 className="section-title">
              {figView === "auto" && autoFig
                ? "Automatic alignment visualization"
                : "Final curated visualization"}
            </h2>
            {/* The single-PNG chip is gone: the .zip already contains that exact
                combined figure alongside each panel, so it was two ways to get
                the same file. Each view offers its OWN panel set -- the automatic
                run writes the same panels under a register_ prefix.

                The zip is the ONLY way to the combined PNG now, so where a job
                has no zip (finalized before 2026-09-21, or a figure that failed
                to build) the single-PNG chip is still offered rather than
                leaving the reader with no download at all. */}
            {figView === "curated" && (figZip
              ? <DlChip job={job.id} path={figZip}>static visualization panels (.zip)</DlChip>
              : curatedFig && <DlChip job={job.id} path={curatedFig}>curated figure PNG</DlChip>)}
            {figView === "auto" && (autoZip
              ? <DlChip job={job.id} path={autoZip}>static visualization panels (.zip)</DlChip>
              : autoFig && <DlChip job={job.id} path={autoFig}>auto figure PNG</DlChip>)}
          </div>
          {curatedFig && autoFig && (
            <div className="seg" style={{ width: "auto" }}>
              <button className={figView === "auto" ? "active" : ""}
                      onClick={() => setFigView("auto")}>Auto</button>
              <button className={figView === "curated" ? "active" : ""}
                      onClick={() => setFigView("curated")}>Curated</button>
            </div>
          )}
        </div>
        <div className="muted" style={{ marginBottom: 10, maxWidth: 720 }}>
          {/* One description, because both views are now the SAME four panels
              from the same renderer -- only the alignment behind them differs.
              The old Auto caption still described the two-panel preview that
              registration stopped drawing. */}
          Depth-4 regions over DAPI (cell density when there is none) and over
          the marker-gene composite, then
          leaf-level cells in two palettes: the rank palette for separability and
          the Allen palette for anatomical meaning. All four panels share one
          frame. The .zip holds each panel on its own at full width, the two
          overlay panels also as SVG.{" "}
          {figView === "auto" && autoFig
            ? "This is the automatic alignment, before any manual curation."
            : curatedFig && autoFig
              ? "Toggle Auto to see the same panels before your manual edits."
              : !curatedFig && "Showing the automatic-registration preview "
                + "(this job predates the curated render)."}
        </div>
        {/* The inline figure is hashed out as of 2026-09-22. It is still
            RENDERED and still DOWNLOADABLE -- only the on-page copy goes. The
            interactive panels below now answer the same questions better: the
            static image showed the four panels at one fixed zoom, which is the
            one thing a reader in a browser does not need, while the .zip remains
            the deliverable for a report. Restoring the block below is the whole
            change; `shownFig` is kept defined for exactly that reason.

        {shownFig ? (
          <img src={`${dl(shownFig, job.id)}&v=${encodeURIComponent(job.updated || "")}`}
               alt="final curated result"
               style={{ width: "100%", maxWidth: 1100, borderRadius: 8, display: "block",
                        background: "#0b0f14", border: "1px solid #1c2530" }} />
        ) : (
          <div className="result muted">No result figure available for this job.</div>
        )}
        */}
        {/* A job with nothing to download still has to say so, which the image
            used to do by failing to appear. */}
        {!shownFig && (
          <div className="result muted">No result figure available for this job.</div>
        )}

        {/* The key-metrics badge row moved into the sidebar on 2026-09-22.
            It describes HOW the section was aligned -- a manifest of the run --
            and sitting under the figure it competed with the anatomy for
            attention. See "Alignment record" in the sidebar. */}

        {/* 2 — Interactive depth-4 overlay panels -------------------------
            The confirmation pair: are the region boundaries where the anatomy
            is? The static figure answers that at one fixed zoom; here the
            background stays put while the overlay's thickness, opacity and
            per-region visibility move, so a boundary can be checked against the
            tissue under it at whatever magnification the question needs. */}
        {bounds && bmeta && (
          <>
            <div className="titlerow" style={{ marginTop: 22, marginBottom: 4 }}>
              <h3 style={{ margin: 0 }}>
                Check the boundaries
                <HelpTip label="About the interactive overlay panels">
                  <p>The depth-4 regions drawn live over each background.
                    <b> Scroll</b> to zoom about the cursor, <b>drag</b> to pan,
                    and use <b>reset view</b> to come back to the whole
                    section.</p>
                  <p>The background image is fixed. The controls act only on the
                    overlay: <b>boundary</b> sets outline thickness, <b>fill</b>
                    its opacity — take it to 0 to judge an edge against the
                    tissue with nothing tinted — and a <b>region pill</b> hides
                    that region entirely.</p>
                  <p>These are the same boundaries and the same transform the
                    curation canvas draws, so what you see here is what was
                    written to the per-cell CSV. On the Auto view they are drawn
                    without the manual transform, which is the difference
                    curation made.</p>
                </HelpTip>
              </h3>
              <span className="muted" style={{ fontSize: 12 }}>
                {figView === "auto" ? "automatic alignment" : "curated alignment"}
              </span>
            </div>
            <div className="row col-wide" style={{ gap: 32, alignItems: "flex-start",
                                                   flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 350px", minWidth: 350 }}>
                <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                  depth 4 · over {dapiUrl ? "DAPI" : "cell density (no DAPI)"}
                </div>
                <OverlayCanvas boundaries={bounds} meta={bmeta}
                               transform={overlayXform} bgUrl={dapiUrl || densityUrl}
                               hint={dapiUrl || densityUrl ? null
                                 : "This experiment has no DAPI mosaic; the regions are drawn on their own."} />
              </div>
              <div style={{ flex: "1 1 350px", minWidth: 350 }}>
                <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                  depth 4 · over marker genes
                </div>
                <OverlayCanvas boundaries={bounds} meta={bmeta}
                               transform={overlayXform} bgUrl={markerUrl}
                               hint={markerUrl ? null
                                 : "This panel has no anatomical marker genes; the regions are drawn on their own."}
                               extraControls={mkLegend.length > 0 && (
                  <PillSection layer="Background" title="marker divisions"
                               note="re-renders the image underneath">
                    {mkLegend.map((r) => {
                      const on = !mkOn || mkOn.includes(r.acronym);
                      return (
                        <button key={r.acronym} className="badge"
                                onClick={() => toggleMarker(r.acronym)}
                                title={`${on ? "Hide" : "Show"} ${r.name} — ${r.present.join(", ")}`}
                                style={{ cursor: "pointer", font: "inherit", fontSize: 11,
                                         borderColor: on ? r.color : "var(--border)",
                                         opacity: on ? 1 : 0.45,
                                         textDecoration: on ? "none" : "line-through" }}>
                          <span style={{ display: "inline-block", width: 9, height: 9,
                                         borderRadius: 2, marginRight: 6, verticalAlign: -1,
                                         background: on ? r.color : "transparent",
                                         border: on ? "none" : `1px solid ${r.color}` }} />
                          {r.short}
                        </button>
                      );
                    })}
                    {mkOn && (
                      <button className="badge" onClick={() => setMkOn(null)}
                              style={{ cursor: "pointer", font: "inherit", fontSize: 11 }}>
                        all divisions
                      </button>
                    )}
                  </PillSection>
                )} />
              </div>
            </div>
          </>
        )}

        {/* 2 — Interactive cell panels (prototype) ------------------------
            The same two views as the static cell panels, but live: pan, zoom,
            hover a cell for its region, and toggle a region off. The static
            figure and its .zip are unchanged -- this is in addition, because a
            downloadable panel and an explorable one are different jobs. */}
        <div className="titlerow" style={{ marginTop: 22, marginBottom: 4 }}>
          <h3 style={{ margin: 0 }}>
            Explore cells
            <HelpTip label="About the interactive cell panels">
              <p>The same cells as the panels above, drawn live instead of as an
                image: <b>scroll</b> to zoom about the cursor, <b>drag</b> to pan,
                <b> hover</b> a cell to name its Allen region, and <b>click a
                pill</b> to hide that region.</p>
              <p>Two palettes, as in the static figure. The <b>rank</b> palette
                separates neighbouring regions; the <b>Allen</b> palette is
                anatomically meaningful and matches the overlay, at the cost of
                related structures sharing a hue.</p>
              <p>Positions are quantised to about 0.2 µm, well below the ~22 µm
                spacing between cells, so nothing is lost. Cells with no region
                are not drawn.</p>
            </HelpTip>
          </h3>
          {cellsBusy && <span className="muted" style={{ fontSize: 12 }}>loading …</span>}
        </div>
        {cells && cells.available === false && (
          <div className="muted">{cells.reason}</div>
        )}
        {cells && cells.available && (
          <div className="row col-wide" style={{ gap: 32, alignItems: "flex-start",
                                                 flexWrap: "wrap" }}>
            {["rank", "allen"].map((pal) => (
              <div key={pal} style={{ flex: "1 1 350px", minWidth: 350 }}>
                <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                  leaf level · {pal === "rank" ? "rank palette" : "Allen palette"}
                </div>
                <CellCanvas data={cells} palette={pal} />
              </div>
            ))}
          </div>
        )}

        {/* ================= SECTION 2 — Stats and QC ========================
            Everything that answers "does this alignment hold up", as opposed to
            "what does it look like".

            Region composition is here rather than in section 1. It was not in
            the list of subsections asked for, but it is a table of per-region
            cell counts -- a statistic, not a picture -- and a reader looking for
            it would look under Stats and QC. Move it up one section if that
            reads wrong. */}
        <div className="section-head">
          <h2 className="section-title">Stats and QC</h2>
        </div>

        {/* Region composition ---------------------------------------------
            Held to half the page: four short columns stretched across a wide
            monitor put the cell count an inch from the name it belongs to. */}
        <div className="col-half">
        <div className="titlerow" style={{ marginTop: 22, marginBottom: 4 }}>
          <h3 style={{ margin: 0 }}>Region composition</h3>
          {exp?.available && <DlChip job={job.id} path={exp.paths?.summary}>region summary CSV</DlChip>}
        </div>
        <table className="jobs">
          <thead>
            <tr>
              {/* percentages are of MAPPED cells (n = n_mapped), not of all
                  segmented cells, so the header names the denominator. */}
              {REGION_COLS.map((col, ci) => (
                <SortTh key={col.key} col={col} sort={regionSort} onSort={setRegionSort}
                        style={{ textAlign: ci < 2 ? "left" : "right" }} />
              ))}
            </tr>
          </thead>
          <tbody>
            {/* sort first, then take the top 8 -- the collapsed view is the head
                of whatever order the reader has chosen, not a fixed top-by-cells */}
            {(showAll ? regionRows : regionRows.slice(0, 8)).map((r, i) => (
              <tr key={i}>
                <td>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <span style={{
                      width: 10, height: 10, borderRadius: 2, flex: "0 0 auto",
                      background: r[4] || "#8aa0b8",
                    }} title={r[3] ? `${r[3]} division` : ""} />
                    <b>{r[0]}</b>
                  </span>
                </td>
                <td className="muted">{r[1]}</td>
                <td style={{ textAlign: "right" }}>{(r[2] || 0).toLocaleString()}</td>
                <td style={{ textAlign: "right" }} className="muted">
                  {n ? ((r[2] / n) * 100).toFixed(1) + "%" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {top.length > 8 && (
          <button onClick={() => setShowAll((v) => !v)}
                  style={{ marginTop: 8, padding: "6px 12px", fontSize: 13 }}>
            {showAll ? "Show top regions" : `Show all regions (${top.length})`}
          </button>
        )}
        </div>

        {/* Everything from here to the end of the hex-bin QC shares ONE column,
            the same width Region composition uses, so the page has a single
            right edge instead of the ragged 620 / 640 / 660 / 700 / 900 maxWidths
            these blocks each used to set for themselves. The section HEADERS
            stay outside it: their rule spans the page. */}
        <div className="col-half">

        {/* Whole-section correlation --------------------------------------
            Before Region specificity on purpose: it asks the cruder and more
            basic question, needs nothing from the alignment, and if it fails
            there is no point reading the per-region table below. */}
        <div className="titlerow" style={{ marginTop: 22, marginBottom: 4 }}>
          <h3 style={{ margin: 0 }}>
            Whole-section correlation
            <HelpTip label="About the whole-section correlation">
              <p>One point per gene shared with the Allen panel: its mean counts
                per cell in the <b>Allen reference section at this AP</b> on x,
                and in <b>this section</b> on y, both log-scaled. The dashed line
                is equality.</p>
              <p>This is a <b>tissue-level</b> check and uses no region labels
                and no alignment — only the panel counts. So it is independent of
                everything steps 3–5 did, which is why it comes first: if a
                section does not look like brain at this AP, the per-region
                numbers below cannot be trusted either.</p>
              <p>The method is the one the transcript-QC workflow uses between
                two MERFISH experiments on overlapping panels: log10 Pearson over
                shared gene symbols. <b>Scroll</b> to zoom, <b>drag</b> to pan,
                <b> hover</b> a point for the gene.</p>
              <p>The reference is a real slice at the matching level, not a
                whole-brain average — a cerebellar and a forebrain section have
                very different global expression. Each plane has its own
                reference, both cut from the same Allen point cloud: 53 real
                coronal sections along AP, and 200 µm bands along ML for
                sagittal.</p>
            </HelpTip>
          </h3>
          {scorr?.available && (
            <span className="badge" title="log10 Pearson over shared genes">
              r = {scorr.r.toFixed(3)}
            </span>
          )}
        </div>
        {scorr?.available && (
          <div className="muted" style={{ marginBottom: 10 }}>
            {scorr.n_genes} of this panel&rsquo;s {scorr.n_panel} genes are shared
            with the Allen panel, over {scorr.n_cells.toLocaleString()} cells,
            against the {scorr.plane} reference <b>{scorr.ref_section}</b>
            {` at ${scorr.axis} ${scorr.ref_mm.toFixed(2)} mm`}
            {" "}(you confirmed {scorr.axis} {scorr.query_mm.toFixed(2)} mm).
            {apCheck && (
              <div style={{ marginTop: 4,
                            color: apCheck.verdict === "mismatch"
                              ? "#d29922" : undefined }}>
                {apCheck.text}
              </div>
            )}
          </div>
        )}
        <SectionCorrCanvas data={scorr} />

        {/* 4 — Region specificity ----------------------------------------
            Renamed from "Biological QC" on 2026-09-18: that named the scope and
            not the question, and "Region-level QC" below did the same, so the
            pair read as two flavours of one thing. Specificity (competitive,
            z-scored) vs agreement (absolute, raw) is the actual difference. */}
        <h3>
          Region specificity
          <HelpTip label="About region specificity">
            <p>Regions that fail name what they look like instead, which is how a
              wrong AP slice or a flipped section shows up.</p>
            <p>It does not use the AP index, so it is an <b>independent</b> check on
              that choice — but it only <i>supports</i> the registration. Judge it
              together with the overlay.</p>
            <p>One verdict for the whole section. For how well each region matches
              its own profile, and the downloadable tables, see Per-region
              agreement below.</p>
          </HelpTip>
        </h3>
        <div className="muted" style={{ marginBottom: 10 }}>
          Does anatomical region R match its own reference better than any{" "}
          <i>other</i> region&apos;s? Genes z-scored across regions; every region pair.
        </div>

        {qcLoading && <div className="muted">Correlating expression …</div>}
        {qcErr && (
          <div style={{ marginTop: 8 }}>
            <div style={{ color: "#d62728" }}>QC failed: {qcErr}</div>
            <button onClick={runQc} style={{ marginTop: 6, padding: "5px 12px", fontSize: 12 }}>
              Retry biological QC
            </button>
          </div>
        )}

        {qc && !qc.available && (
          <div className="result">
            <div className="muted">{qc.reason}</div>
          </div>
        )}

        {qc && qc.available && (
          <div>
            <div className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <span className="badge" style={{
                background: pass ? "#1e7e34" : "#a12a2a", color: "#fff" }}>
                {qc.verdict}
              </span>
              <span className="badge">median self-corr {qc.median_self_corr.toFixed(3)}</span>
              <span className="badge">self-best {qc.self_is_best}/{qc.n_regions}</span>
              <span className="badge">{qc.n_genes} genes</span>
            </div>
            <div className="muted" style={{ marginTop: 8 }}>
              {pass
                ? "PASS: mapped-region expression matches the Allen ABC reference — the localization is credible."
                : "WARN: self-correlation is low — possibly an AP/registration issue; cross-check the overlay."}
            </div>
            {qc.confusions && qc.confusions.length > 0 && (
              <>
                <div className="titlerow" style={{ marginTop: 22, marginBottom: 4 }}>
                <h3 style={{ margin: 0 }}>
                  Region-by-region scores
                  <HelpTip label="About the expression-confusable table">
                    <p>Every mapped region with at least 50 cells gets a mean
                      per-cell expression profile. Genes are log-scaled and
                      z-scored across regions, so this compares <b>regional
                      specificity patterns</b>, not abundance. Each profile is then
                      Pearson-correlated against every Allen ABC reference region.</p>
                    <p><b>cells</b> — cells the profile was built from.<br />
                      <b>self r</b> — correlation with its <i>own</i> ABC profile.<br />
                      <b>best match</b> — the reference region it matches most.<br />
                      <b>best r</b> — that correlation.<br />
                      <b>gap</b> — best r minus self r.</p>
                    <p>Every scored region is listed, worst self r first. A
                      highlighted <b>best match</b> means that region matches some
                      <i> other</i> region better than itself; <b>Show only
                      confusable</b> narrows the table to those. Sort any column —
                      paging and the filter both follow the order you choose.</p>
                    <p>Read the <i>kind</i> of best match: a neighbour, a parent
                      or the same cortical layer is a soft boundary; a fibre tract
                      scores low because it has little region-specific neuronal
                      signal at all. An anatomically remote match, or a large gap
                      on a well-populated region, is the one worth chasing in the
                      overlay.</p>
                  </HelpTip>
                </h3>
                {hasScored && nConfusable > 0 && (
                  <button className="badge" onClick={toggleConfOnly}
                          title={confOnly
                            ? "Show every scored region again"
                            : "Show only the regions that match some other region better than themselves"}
                          style={{
                            cursor: "pointer", font: "inherit",
                            borderColor: confOnly ? "#ffd23f" : "var(--border)",
                            color: confOnly ? "#ffe17a" : undefined,
                          }}>
                    {confOnly ? `Showing confusable only (${nConfusable})`
                              : `Show only confusable (${nConfusable})`}
                  </button>
                )}
                </div>
                <table className="jobs">
                  <thead>
                    <tr>
                      {CONFUSION_COLS.map((col, ci) => (
                        <SortTh key={col.key} col={col} sort={confusionSort}
                                onSort={sortConfusions}
                                style={col.key === "best_match"
                                  ? { textAlign: "left", paddingLeft: 16 }
                                  : { textAlign: ci === 0 ? "left" : "right" }} />
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {confView.rows.map((c, i) => (
                      <tr key={i}>
                        <td><b>{c.region}</b></td>
                        <td style={{ textAlign: "right" }} className="muted">
                          {c.n_cells == null ? "—" : c.n_cells.toLocaleString()}
                        </td>
                        <td style={{ textAlign: "right" }}>{c.self_corr.toFixed(2)}</td>
                        <td style={{ paddingLeft: 16,
                                     // amber only when the best match is NOT itself;
                                     // on a self-best row it repeats the acronym and
                                     // should stay quiet
                                     color: c.self_is_best === false ? "#ffd23f" : undefined }}
                            className={c.self_is_best === false ? "" : "muted"}>
                          {c.best_match}
                        </td>
                        <td style={{ textAlign: "right" }}>{c.best_corr.toFixed(2)}</td>
                        <td style={{ textAlign: "right" }} className="muted">
                          {c.gap == null ? "—" : c.gap.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {confView.pages > 1 && (
                  <div className="row" style={{ gap: 8, marginTop: 8, fontSize: 12 }}>
                    <button onClick={() => setConfPage(confView.page - 1)}
                            disabled={confView.page === 0}
                            style={{ padding: "4px 10px", fontSize: 12 }}>‹ Prev</button>
                    <span className="muted">
                      {confView.from}–{confView.to} of {confView.total}
                      {" · page "}{confView.page + 1}/{confView.pages}
                    </span>
                    <button onClick={() => setConfPage(confView.page + 1)}
                            disabled={confView.page >= confView.pages - 1}
                            style={{ padding: "4px 10px", fontSize: 12 }}>Next ›</button>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        <div className="titlerow" style={{ marginTop: 22, marginBottom: 4 }}>
          <h3 style={{ margin: 0 }}>
            Per-region agreement
            <HelpTip label="About per-region agreement">
              <p>One row per region, plus the deliverables. No competition and no
                z-scoring, so a region is judged on its own without reference to
                its neighbours.</p>
              <p>The specificity score from above is carried alongside, and it is
                what drives each row&apos;s <b>PASS/WARN</b> — the absolute
                correlations are reported but only decide a row when specificity
                cannot be computed.</p>
              <p>Use it to find the handful of bad regions inside an otherwise
                sound fit. The two measures disagree often: a region can match its
                own profile well and still resemble a neighbour more.</p>
            </HelpTip>
          </h3>
          {exp?.available && (
            <>
              <DlChip job={job.id} path={exp.paths?.gene_counts}>region×gene counts CSV</DlChip>
              <DlChip job={job.id} path={exp.paths?.qc}>region expression QC CSV</DlChip>
            </>
          )}
        </div>
        <div className="muted" style={{ marginBottom: 10 }}>
          Does anatomical region R resemble its own reference at all? Pearson and
          Spearman on raw log counts, region against itself only.
        </div>

        {expLoading && (
          <div className="muted">
            {qcLoading ? "Queued behind biological QC …"
                       : "Summarizing regions → counting region×gene → correlating …"}
          </div>
        )}
        {expErr && (
          <div style={{ marginTop: 8 }}>
            <div style={{ color: "#d62728" }}>Exports failed: {expErr}</div>
            <button onClick={runExports} style={{ marginTop: 6, padding: "5px 12px", fontSize: 12 }}>
              Retry region-level QC
            </button>
          </div>
        )}

        {exp && !exp.available && (
          <div className="result">
            <div className="muted">{exp.reason}</div>
          </div>
        )}

        {/* exp.paths.polygons is set to man.geojson_path by the backend
            (run_region_qc is called with with_polygons=False), so it gets no chip
            above: it would be a second link to the curated GeoJSON already in Job
            deliverables, where the whole-job outputs live. */}
        {exp && exp.available && (
          <div className="row" style={{ gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
            <span className="badge">{exp.n_regions} regions</span>
            <span className="badge">PASS {exp.n_pass}/{exp.n_regions}</span>
            <span className="badge">source: {exp.source}</span>
          </div>
        )}

        {/* 5 — Spatial hex-bin QC ---------------------------------------- */}
        <div className="titlerow" style={{ marginTop: 22, marginBottom: 4 }}>
          <h3 style={{ margin: 0 }}>Spatial hex-bin QC</h3>
          {hexQc?.available && (
            <>
              <DlChip job={job.id} path={hexQc.paths?.png}>hex-bin QC PNG</DlChip>
              <DlChip job={job.id} path={hexQc.paths?.csv}>per-bin QC CSV</DlChip>
            </>
          )}
        </div>
        <div className="muted" style={{ marginBottom: 10 }}>
          Groups cells into {hexSize} um hexagons and compares each bin&apos;s expression with
          the Allen ABC profile of its mapped region. The inferno map is an exploratory
          spatial diagnostic, not an independent PASS/FAIL test. Bins are assigned in
          the raw MERFISH frame, but the map is drawn in the same dorsal-up orientation
          as the figures above so the two can be compared side by side. Bins with
          fewer than {HEX_MIN_CELLS} cells are not scored.
        </div>

        {/* The radio group renders only when there is a choice to make. With
            300 um switched off it would be a single locked option, which reads
            as a broken control rather than a fixed parameter. */}
        {HEX_SIZE_OPTIONS.length > 1 && (
          <fieldset className="hexopts" disabled={hexLoading}>
            <legend>Bin size</legend>
            {HEX_SIZE_OPTIONS.map((o) => (
              <label key={o.v} className="hexopt">
                <input type="radio" name="hexsize" value={o.v} checked={hexSize === o.v}
                       onChange={() => setHexSize(o.v)} />
                <span className="hexopt-label">{o.label}</span>
                <span className="hexopt-note">{o.note}</span>
              </label>
            ))}
          </fieldset>
        )}

        <div className="muted" style={{ margin: "2px 0 10px" }}>
          Smaller bins push the median correlation down: fewer cells per bin means a
          noisier profile, not a worse registration. Compare runs only at matching
          bin sizes.
        </div>

        {hexLoading && (
          <div className="muted">Computing spatial correlations …</div>
        )}
        {/* Re-run stays available after the first pass -- the map is cached on the
            mapped-CSV fingerprint, so this is cheap and is the way back from a
            transient failure without leaving the step. */}
        {!hexLoading && hexQc && (
          <button onClick={runHexQc} style={{ padding: "6px 12px", fontSize: 12 }}>
            Re-run at {hexSize} um
          </button>
        )}
        {!hexLoading && !hexQc && hexErr && (
          <button className="primary" onClick={runHexQc}
                  style={{ padding: "8px 14px", fontSize: 13 }}>
            Retry spatial hex-bin QC
          </button>
        )}
        {hexErr && <div style={{ color: "#d62728", marginTop: 8 }}>Hex QC failed: {hexErr}</div>}
        {hexQc && !hexQc.available && (
          <div className="result">
            <div className="muted">{hexQc.reason}</div>
          </div>
        )}
        {hexQc && hexQc.available && (
          <div>
            <div className="row" style={{ gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
              <span className="badge">{hexQc.hex_size_um} um bins</span>
              <span className="badge">min {hexQc.min_cells} cells/bin</span>
              <span className="badge">{hexQc.n_scored}/{hexQc.n_bins} bins scored</span>
              <span className="badge">{hexQc.n_cells.toLocaleString()} cells</span>
              <span className="badge">{hexQc.n_genes} genes</span>
              <span className="badge">median mapped-region r {hexQc.median_self_corr == null ? "n/a" : hexQc.median_self_corr.toFixed(3)}</span>
              <span className="badge">best-region agreement {hexQc.best_match_agreement == null ? "n/a" : `${(100 * hexQc.best_match_agreement).toFixed(1)}%`}</span>
            </div>
            <div className="muted" style={{ marginBottom: 10 }}>
              Brighter hexagons agree more strongly with the mapped Allen region; dark
              areas deserve overlay review. The display range is fixed at r = -0.2 to 0.8.
              Flat grey hexagons are unscored: their mapped region has no profile in the
              ABC reference, so there is nothing to correlate against. That is a
              reference-coverage gap, not a registration warning, and lowering the
              minimum cells per bin will not fill it in.
            </div>
            {hexQc.paths?.png && (
              <img src={dl(hexQc.paths.png, job.id)} alt="spatial hex-bin expression correlation"
                   style={{ width: "100%", borderRadius: 8, display: "block",
                            background: "#0b0f14", border: "1px solid #1c2530" }} />
            )}
          </div>
        )}

        </div>{/* end of the shared half-width column */}

        {/* ================= SECTION 3 — Job deliverables ====================
            QC artifacts are no longer listed here: each one now sits beside the
            figure or table it came from. What remains is the whole-job output,
            which belongs to no single section -- and which is the thing someone
            carries into their own analysis, hence the longer name. */}
        <div className="section-head">
          <h2 className="section-title">Job deliverables for downstream analysis</h2>
        </div>
        <div className="dl-grid col-half">
          {man.csv_path && <a className="dl" href={dl(man.csv_path, job.id)}>⬇ curated per-cell labels CSV</a>}
          {man.geojson_path && <a className="dl" href={dl(man.geojson_path, job.id)}>⬇ curated region polygons GeoJSON</a>}
          {warpJson && <a className="dl" href={dl(warpJson, job.id)}>⬇ manual warp parameters JSON</a>}
          {reg.csv_path && !man.csv_path && <a className="dl" href={dl(reg.csv_path, job.id)}>⬇ auto per-cell labels CSV</a>}
          {/* Built on request from the per-cell CSV above (curated when there is
              one): EntityID plus Allen label columns only, the shape the MERSCOPE
              Vizualizer imports. See allen_alignment/vizualizer.py. */}
          {(man.csv_path || reg.csv_path) && (
            <a className="dl" href={`/api/jobs/${job.id}/vizualizer.csv`}
               title="EntityID + Allen region columns only, for colouring cells by region in the MERSCOPE Vizualizer">
              ⬇ MERSCOPE Vizualizer region table CSV
            </a>
          )}
        </div>
      </div>
    </>
  );
}

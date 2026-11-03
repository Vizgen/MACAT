#!/usr/bin/env python
"""Fetch the Allen source data the reference tables are built from.

The Allen-derived files in data/reference/ are CC BY-NC 4.0 / Allen Terms of
Use (see NOTICE), so a public release of this tool ships WITHOUT them and each
user builds them. This script does every step that needs the network or that
used to be done by hand; the two build scripts then do the rest:

    1. download, from the public Allen Brain Cell Atlas bucket over HTTPS (no
       client, no credentials), into the layout the build scripts expect:
         <ABC>/abc_atlas/expression_matrices/MERFISH-C57BL6J-638850/20230830/
               C57BL6J-638850-raw.h5ad                                 7.6 GB
         <ABC>/abc_atlas/metadata/MERFISH-C57BL6J-638850-CCF/20231215/views/
               cell_metadata_with_parcellation_annotation.csv          1.6 GB
    2. drop the panel's blank/control probes, writing
         <ABC>/scratch/C57BL6J-638850-raw_no_blank.h5ad   (500 genes)
    3. download the Allen CCFv3 (2017) ontology used for region hierarchy:
         <OUT>/structure_tree_safe_2017.csv

<ABC> is ALLEN_ABC_SOURCE_DIR or data/abc_source (gitignored); <OUT> is
ALLEN_REFERENCE_OUT or data/reference. Files already present at the right size
are not downloaded again, and an interrupted download resumes.

Run in the `allen_reference` environment (environment_allen_reference.yml, or
requirements-allen-reference.txt with uv or pip); see data/reference/README.md,
"Rebuilding them". Then:

    python scripts/data/fetch_abc_reference.py
    python scripts/data/fetch_abc_section_reference.py
"""
import concurrent.futures as cf
import os
import sys
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

BUCKET = "https://allen-brain-cell-atlas.s3.us-west-2.amazonaws.com"
RAW_KEY = "expression_matrices/MERFISH-C57BL6J-638850/20230830/C57BL6J-638850-raw.h5ad"
CCF_KEY = ("metadata/MERFISH-C57BL6J-638850-CCF/20231215/views/"
           "cell_metadata_with_parcellation_annotation.csv")
# cortex-lab/allenCCF's copy of the Allen CCFv3 2017 ontology; the committed
# file is byte-identical to it (checked 2026-10-19).
TREE_URL = ("https://raw.githubusercontent.com/cortex-lab/allenCCF/master/"
            "structure_tree_safe_2017.csv")


def _dirs():
    data = os.path.join(_REPO, "data")
    abc = os.environ.get("ALLEN_ABC_SOURCE_DIR") or os.path.join(data, "abc_source")
    out = os.environ.get("ALLEN_REFERENCE_OUT") or os.path.join(data, "reference")
    return abc, out


def _remote_size(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"])


def _fetch_range(url, path, start, end):
    """Download bytes [start, end] into `path`, resuming a partial part."""
    have = os.path.getsize(path) if os.path.exists(path) else 0
    want = end - start + 1
    if have >= want:
        return
    req = urllib.request.Request(url, headers={"Range": f"bytes={start + have}-{end}"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "ab") as fh:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    if os.path.getsize(path) != want:
        raise IOError(f"short read on {os.path.basename(path)}")


def download(url, dest, parts=16):
    """Fetch `url` to `dest` in parallel byte ranges; skip if already complete.

    The S3 bucket serves ranges, and one connection runs at ~1 MB/s while
    sixteen run at ~20 MB/s (measured 2026-10-19), which is the difference
    between two hours and six minutes for the 7.6 GB matrix."""
    size = _remote_size(url)
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        print(f"  have   {os.path.basename(dest)} ({size / 1e9:.2f} GB)")
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"  fetch  {os.path.basename(dest)} ({size / 1e9:.2f} GB) ...", flush=True)
    step = -(-size // parts)
    spans = [(i * step, min(size, (i + 1) * step) - 1) for i in range(parts)]
    tmp = [f"{dest}.part{i:02d}" for i in range(parts)]
    with cf.ThreadPoolExecutor(parts) as pool:
        for f in [pool.submit(_fetch_range, url, t, s, e) for t, (s, e) in zip(tmp, spans)]:
            f.result()
    with open(dest + ".tmp", "wb") as out:
        for t in tmp:
            with open(t, "rb") as fh:
                while True:
                    b = fh.read(1 << 24)
                    if not b:
                        break
                    out.write(b)
    if os.path.getsize(dest + ".tmp") != size:
        raise IOError(f"{os.path.basename(dest)}: assembled size is wrong")
    os.replace(dest + ".tmp", dest)
    for t in tmp:
        os.remove(t)
    return dest


def drop_blanks(raw_path, out_path):
    """Write the raw matrix without the blank/control probes.

    Blank probes are part of the 550-probe panel but measure no gene; the
    reference is 500 genes. Reproduces the file the committed tables were built
    from: X as float64 CSR, genes re-indexed by SYMBOL (the release indexes them
    by Ensembl gene id) keeping gene_symbol and transcript_identifier, and obs
    and uns exactly as released -- `cell_label` is the obs INDEX, which the
    builds join on, not a column."""
    import anndata as ad
    import numpy as np
    import scipy.sparse as sp
    if os.path.exists(out_path):
        print(f"  have   {os.path.basename(out_path)}")
        return out_path
    print("  drop   blank probes ...", flush=True)
    a = ad.read_h5ad(raw_path)
    sym = a.var["gene_symbol"].astype(str)
    keep = ~sym.str.lower().str.startswith("blank").to_numpy()
    b = a[:, keep].copy()
    b.X = sp.csr_matrix(b.X, dtype=np.float64)
    b.var = b.var[["gene_symbol", "transcript_identifier"]].copy()
    b.var.index = b.var["gene_symbol"].astype(str).to_numpy()
    if b.n_vars != 500:
        raise ValueError(f"expected 500 genes after dropping blanks, got {b.n_vars}")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    b.write_h5ad(out_path + ".tmp.h5ad")
    os.replace(out_path + ".tmp.h5ad", out_path)
    return out_path


def main():
    abc, out = _dirs()
    print(f"ABC source dir: {abc}\nreference dir:  {out}")
    raw = download(f"{BUCKET}/{RAW_KEY}", os.path.join(abc, "abc_atlas", RAW_KEY))
    download(f"{BUCKET}/{CCF_KEY}", os.path.join(abc, "abc_atlas", CCF_KEY), parts=8)
    drop_blanks(raw, os.path.join(abc, "scratch", "C57BL6J-638850-raw_no_blank.h5ad"))
    download(TREE_URL, os.path.join(out, "structure_tree_safe_2017.csv"), parts=1)
    print("done. Next: scripts/data/fetch_abc_reference.py and "
          "scripts/data/fetch_abc_section_reference.py")


if __name__ == "__main__":
    main()

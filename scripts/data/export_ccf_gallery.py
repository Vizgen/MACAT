#!/usr/bin/env python
"""
Export the FULL CCF atlas coronal slice gallery (2026-07-20).

Reviewers and the tool were using different "slice" numbering (theirs = an interactive/
2D-reference-atlas plate #; ours = the brainglobe allen_mouse_50um CCFv3 AP index).
"slice 75" is a different image in each. Fix: publish OUR reference's every AP
slice, labeled with its CCF index + AP coordinate, so reviewers re-match each
training DAPI against THIS reference. Then both sides share one numbering and the
AP comparison is apples-to-apples.

Outputs (study/):
  ccf_atlas_gallery_montage.png  -- all non-empty AP slices in one overview grid
  ccf_atlas_gallery.pdf          -- multi-page, larger, ~20/page, for matching

Coordinate: brainglobe allen_mouse_50um AP axis is index 0 = ANTERIOR; so
CCF AP (um from anterior) = index * 50.

Run:  conda run -n macat python export_ccf_gallery.py
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from brainglobe_atlasapi import BrainGlobeAtlas

import config

RES = int(config.ATLAS_RES_UM)          # 50 um
OUT_DIR = config.STUDY_DIR


def main():
    atlas = BrainGlobeAtlas(f"allen_mouse_{RES}um", check_latest=False)
    ref = atlas.reference                # (nAP, nSI, nLR)
    nAP = ref.shape[0]

    # keep only AP slices that actually contain tissue (drop empty ends)
    tissue = np.array([ref[i].sum() for i in range(nAP)])
    thr = 0.02 * tissue.max()
    idxs = [i for i in range(nAP) if tissue[i] > thr]
    print(f"atlas allen_mouse_{RES}um: {nAP} AP slices; {len(idxs)} non-empty "
          f"(idx {idxs[0]}..{idxs[-1]}, AP {idxs[0]*RES}..{idxs[-1]*RES} um)")

    def label(i):
        return f"idx {i}  |  AP {i*RES} um ({i*RES/1000:.2f} mm)"

    # ---- 1. one-page montage overview (all non-empty slices) ----
    cols = 12
    rows = int(np.ceil(len(idxs) / cols))
    fig, ax = plt.subplots(rows, cols, figsize=(cols * 1.5, rows * 1.6))
    for k, i in enumerate(idxs):
        a = ax.ravel()[k]
        a.imshow(ref[i], cmap="gray"); a.set_xticks([]); a.set_yticks([])
        a.set_title(f"{i} | {i*RES}um", fontsize=5)
    for j in range(len(idxs), rows * cols):
        ax.ravel()[j].axis("off")
    fig.suptitle(f"CCF allen_mouse_{RES}um -- ALL coronal AP slices "
                 f"(idx | CCF AP um). Match training DAPI against THESE.", fontsize=12)
    fig.tight_layout()
    m = os.path.join(OUT_DIR, "ccf_atlas_gallery_montage.png")
    fig.savefig(m, dpi=150); plt.close(fig)
    print("saved ->", m)

    # ---- 2. multi-page PDF, larger, ~20 per page ----
    per, pc = 20, 5
    pr = per // pc
    pdf_path = os.path.join(OUT_DIR, "ccf_atlas_gallery.pdf")
    with PdfPages(pdf_path) as pdf:
        for p0 in range(0, len(idxs), per):
            chunk = idxs[p0:p0 + per]
            fig, ax = plt.subplots(pr, pc, figsize=(11, 8.5))
            for k, i in enumerate(chunk):
                a = ax.ravel()[k]
                a.imshow(ref[i], cmap="gray"); a.set_xticks([]); a.set_yticks([])
                a.set_title(label(i), fontsize=7)
            for j in range(len(chunk), pr * pc):
                ax.ravel()[j].axis("off")
            fig.suptitle(f"CCF allen_mouse_{RES}um coronal AP slices "
                         f"({chunk[0]}-{chunk[-1]})", fontsize=11)
            fig.tight_layout()
            pdf.savefig(fig); plt.close(fig)
    print("saved ->", pdf_path, f"({int(np.ceil(len(idxs)/per))} pages)")


if __name__ == "__main__":
    main()

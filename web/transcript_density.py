"""Bounded-memory detected-transcript density rendering helpers.

Coordinates are projected onto a registration bundle's existing J-plane grid;
this module never estimates or changes an alignment.
"""
import os

import numpy as np


def find_table(exp_path):
    """Return the preferred detected-transcript table without loading it."""
    for name in ("detected_transcripts.parquet", "detected_transcripts.csv"):
        path = os.path.join(exp_path, name)
        if os.path.exists(path):
            return path
    return None


def xy_batches(path, batch_size=1_000_000):
    """Yield bounded-memory global_x/global_y arrays from CSV or Parquet."""
    columns = ["global_x", "global_y"]
    if path.lower().endswith(".parquet"):
        import pyarrow.parquet as pq
        table = pq.ParquetFile(path)
        missing = [c for c in columns if c not in table.schema_arrow.names]
        if missing:
            raise ValueError(f"detected transcripts missing columns: {missing}")
        for batch in table.iter_batches(columns=columns, batch_size=batch_size):
            yield batch.column(0).to_numpy(), batch.column(1).to_numpy()
    else:
        import pandas as pd
        for batch in pd.read_csv(path, usecols=columns, chunksize=batch_size):
            yield batch[columns[0]].to_numpy(), batch[columns[1]].to_numpy()


def on_plane(bundle, transcript_path, batches=xy_batches):
    """Bin all detected transcripts onto the bundle's exact J-plane grid.

    ``Morig`` is the registration-time affine from oriented tissue coordinates
    back to original experiment coordinates. Its inverse therefore puts the
    transcript global coordinates in the same frame as the registered cells.
    ``r0/c0/drow/dcol`` then map that frame to Jimg pixels exactly as registration
    did for cells. Rows outside the registered plane are intentionally ignored.
    """
    shape = np.asarray(bundle["Jimg"]).shape
    density = np.zeros(shape, dtype=np.float64)
    morig = np.asarray(bundle["Morig"], dtype=np.float64)
    if morig.shape != (3, 2):
        raise ValueError(f"unexpected Morig shape {morig.shape}; expected (3, 2)")
    inv_linear = np.linalg.inv(morig[:2, :])
    offset = morig[2, :]
    r0, c0 = float(bundle["r0"]), float(bundle["c0"])
    drow, dcol = float(bundle["drow"]), float(bundle["dcol"])
    for global_x, global_y in batches(transcript_path):
        original = np.column_stack((global_x, global_y)).astype(np.float64, copy=False)
        oriented = (original - offset) @ inv_linear
        oriented = oriented[np.isfinite(oriented).all(axis=1)]
        cols = np.rint((oriented[:, 0] - c0) / dcol).astype(np.int64)
        rows = np.rint((oriented[:, 1] - r0) / drow).astype(np.int64)
        keep = ((rows >= 0) & (rows < shape[0]) &
                (cols >= 0) & (cols < shape[1]))
        flat = rows[keep] * shape[1] + cols[keep]
        density += np.bincount(flat, minlength=shape[0] * shape[1]).reshape(shape)
    return density

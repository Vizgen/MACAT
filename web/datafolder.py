"""The user's data folder: where step 1's "Your sections" list comes from.

The user points MACAT at ONE folder, local or an ``s3://`` prefix, laid out the
way MERSCOPE writes experiments::

    <data folder>/<experiment>/region_*/cell_metadata.csv, cell_by_gene.csv
                                        images/...          (optional, as written)

Exactly that depth: a region folder is ``<experiment>/region_*`` directly under
the data folder, holding both cell tables. Nothing deeper is searched.

* The choice is saved in ``<output root>/settings.json`` so it survives a
  restart. ``MACAT_DATA_DIR``, when set, overrides it and cannot be changed from
  the page.
* The folder is scanned when it is set and when the user asks for a rescan --
  never on page load -- and the result is saved with the setting, so a slow or
  network drive does not slow step 1 down.
* Listing folders shows the file system to whoever can reach the server, so the
  server only answers these requests from the machine it runs on (see
  ``is_local``); through an SSH tunnel that is still the case.
"""
import json
import os
import time

from web import answer_key, jobs, s3source

ENV = "MACAT_DATA_DIR"
MAX_SECTIONS = 500           # a folder with more is reported as truncated
_REQUIRED = ("cell_metadata.csv", "cell_by_gene.csv")
_DAPI = ("images/mosaic_DAPI_z3.tif", "images/micron_to_mosaic_pixel_transform.csv")
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def is_local(host):
    """True when a request comes from the machine MACAT runs on."""
    return (host or "") in _LOCAL_HOSTS or str(host or "").startswith("127.")


def _settings_path():
    return os.path.join(jobs.OUT_ROOT, "settings.json")


def _read():
    try:
        with open(_settings_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(settings):
    jobs._atomic_write(_settings_path(), settings)


def clean_path(text):
    """A pasted path without surrounding spaces or quotes. Windows' "Copy as
    path" wraps the path in double quotes, and macOS Finder's "Copy as
    Pathname" does not, so a path may arrive either way. Only one matching
    pair around the whole path is removed; quotes inside a name stay."""
    text = str(text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def normalize(folder):
    """A folder as it is stored: s3:// prefixes end in "/", local paths absolute."""
    folder = clean_path(folder)
    if not folder:
        raise ValueError("enter a folder")
    if s3source.is_s3(folder):
        bucket, prefix = s3source.parse(folder)
        return f"s3://{bucket}/{prefix}"
    return os.path.abspath(os.path.expanduser(folder))


def _section(folder, experiment, region, path, dapi, answer=None):
    """One "Your sections" entry. `answer` is the region's expected.json, when
    it has one: its known level and plane are shown beside the name."""
    s = {"experiment": experiment, "region": region, "path": path,
         "dapi": bool(dapi), "name": f"{experiment}_{region}"}
    info = answer_key.summary(answer, jobs.current_res_um())
    if info:
        s.update(info)
    return s


def _scan_local(folder):
    if not os.path.isdir(folder):
        raise ValueError(f"not a folder on this computer: {folder}")
    out = []
    with os.scandir(folder) as exps:
        for e in sorted((x for x in exps if x.is_dir() and not x.name.startswith(".")),
                        key=lambda x: x.name):
            try:
                regions = sorted((r for r in os.scandir(e.path)
                                  if r.is_dir() and r.name.startswith("region_")),
                                 key=lambda r: r.name)
            except OSError:
                continue
            for r in regions:
                if all(os.path.isfile(os.path.join(r.path, f)) for f in _REQUIRED):
                    dapi = all(os.path.isfile(os.path.join(r.path, f)) for f in _DAPI)
                    out.append(_section(folder, e.name, r.name, r.path, dapi,
                                        answer_key.read(r.path)))
    return out


def _scan_s3(folder):
    keys = s3source.list_objects(folder)          # one recursive listing
    found = {}
    for rel in keys:
        parts = rel.split("/")
        if len(parts) < 3 or not parts[1].startswith("region_"):
            continue
        found.setdefault((parts[0], parts[1]), set()).add("/".join(parts[2:]))
    out = []
    for (exp, reg), files in sorted(found.items()):
        if all(f in files for f in _REQUIRED):
            url = f"{folder}{exp}/{reg}/"
            ans = _s3_answer(url) if answer_key.FILE in files else None
            out.append(_section(folder, exp, reg, url,
                                all(f in files for f in _DAPI), ans))
    return out


def _s3_answer(url):
    """A region's expected.json read straight from S3 (a few hundred bytes)."""
    import json
    try:
        d = json.loads(s3source._aws("s3", "cp", url + answer_key.FILE, "-"))
    except (ValueError, OSError):
        return None
    return d if isinstance(d, dict) else None


def scan(folder):
    """Every <experiment>/region_* section directly under `folder`, sorted."""
    secs = _scan_s3(folder) if s3source.is_s3(folder) else _scan_local(folder)
    return secs[:MAX_SECTIONS], len(secs) > MAX_SECTIONS


def _record(folder, locked):
    """Scan `folder` and save the result with the setting."""
    settings = _read()
    entry = {"folder": folder, "scanned_at": time.time(), "error": None,
             "sections": [], "truncated": False}
    try:
        entry["sections"], entry["truncated"] = scan(folder)
    except s3source.NetworkError:
        raise                                       # the server's network: retry
    except (ValueError, OSError) as e:
        entry["error"] = str(e)
    if not locked:
        settings["data_folder"] = folder
    settings["data_folder_scan"] = entry
    _save(settings)
    return entry


def current():
    """What step 1 shows: the folder, where it came from, and its last scan."""
    env = os.environ.get(ENV, "").strip()
    settings = _read()
    folder = normalize(env) if env else settings.get("data_folder")
    out = {"folder": folder, "locked": bool(env), "env": ENV,
           "sections": [], "scanned_at": None, "error": None, "truncated": False}
    if not folder:
        return out
    scan_ = settings.get("data_folder_scan") or {}
    if scan_.get("folder") != folder:
        if not env:
            return out                              # set elsewhere; rescan to list
        scan_ = _record(folder, locked=True)        # first look at an env folder
    out.update({k: scan_.get(k) for k in ("sections", "scanned_at", "error", "truncated")})
    out["sections"] = out["sections"] or []
    return out


def set_folder(folder):
    if os.environ.get(ENV, "").strip():
        raise PermissionError(f"the data folder is set by {ENV} when MACAT starts")
    folder = normalize(folder)
    _record(folder, locked=False)
    return current()


def clear():
    if os.environ.get(ENV, "").strip():
        raise PermissionError(f"the data folder is set by {ENV} when MACAT starts")
    settings = _read()
    settings.pop("data_folder", None)
    settings.pop("data_folder_scan", None)
    _save(settings)
    return current()


def rescan():
    cur = current()
    if not cur["folder"]:
        raise ValueError("no data folder is set")
    _record(cur["folder"], locked=cur["locked"])
    return current()

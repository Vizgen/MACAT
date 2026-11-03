"""Experiments on S3: fetch what the app reads into a local cache, record the source.

A job used to need an experiment folder on this host. Now step 1 also accepts
an ``s3://bucket/.../region_R#/`` path. Nothing in the app learns to read S3:
the files it reads are copied into a gitignored cache that mirrors the bucket
layout, and the job points at that folder. Every reader, cache fingerprint and
image path keeps working unchanged. The job records the S3 URL and each file's
size and ETag, so it can be traced to its source rather than to one host's path.

What is fetched (the AWS CLI does the transfer, with the host's configured
credentials, as in the s3-experiment-downloader skill):

    cell_metadata.csv, cell_by_gene.csv       required; at job creation
    images/micron_to_mosaic_pixel_transform.csv   small; at job creation
    detected_transcripts.parquet               in the background, by default
    images/mosaic_DAPI_z3.tif                  in the background, only if asked
                                               (~12-63 GB per region)

Objects in an ARCHIVE storage class (GLACIER, DEEP_ARCHIVE) are never
requested: a GET would fail, and on Vizgen's output bucket the DAPI mosaic and
detected_transcripts.csv are often archived. They are reported as archived, so
the user knows to restore them in S3. That is also why the transcripts are
read from the parquet, never the CSV.

The cache is ``ALLEN_S3_CACHE`` or ``data/s3_cache/``. Jobs on the same S3 path
share it, and a file already present at the listed size is not fetched again.
``s3_source.json`` in each cached folder holds the record.

Run as ``python -m web.s3source <cached folder>`` to fetch the background files;
the server starts that itself.
"""
import json
import os
import re
import subprocess
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.realpath(os.environ.get("ALLEN_S3_CACHE")
                         or os.path.join(_REPO, "data", "s3_cache"))
RECORD = "s3_source.json"
ARCHIVED = {"GLACIER", "DEEP_ARCHIVE"}     # GLACIER_IR is instant-retrieval: fine
REQUIRED = ("cell_metadata.csv", "cell_by_gene.csv")
SMALL = ("images/micron_to_mosaic_pixel_transform.csv",
         "expected.json")          # the section's answer key, if any (web/answer_key.py)
BACKGROUND = ("detected_transcripts.parquet",)
DAPI = "images/mosaic_DAPI_z3.tif"


def is_s3(path):
    return str(path or "").strip().lower().startswith("s3://")


def parse(url):
    """``s3://bucket/a/b/region_R1`` -> ("bucket", "a/b/region_R1/")."""
    m = re.match(r"^s3://([^/]+)/(.+?)/*$", str(url).strip())
    if not m:
        raise ValueError(f"not an s3://bucket/path URL: {url!r}")
    return m.group(1), m.group(2) + "/"


def local_dir(url):
    bucket, prefix = parse(url)
    return os.path.join(CACHE, bucket, prefix.rstrip("/"))


def auto_key(url):
    """A job key from the S3 path, like the tr_ training keys: the experiment
    folder's timestamp and the region. ``.../202401020900_xyz/region_R1/`` ->
    ``s3_202401020900_R1``. The bare folder name would be ``region_R1`` for every
    region of every experiment, and the key names the job's artifacts."""
    _, prefix = parse(url)
    parts = [p for p in prefix.split("/") if p]
    region = parts[-1]
    exp = parts[-2] if len(parts) > 1 else ""
    ts = exp.split("_", 1)[0] if exp else ""
    region = region[len("region_"):] if region.startswith("region_") else region
    key = "_".join(p for p in ("s3", ts, region) if p)
    return re.sub(r"[^A-Za-z0-9_-]", "_", key)


class NetworkError(ValueError):
    """S3 could not be REACHED from this server: a network or DNS problem, not
    a wrong path or missing access. Its own class so the server can answer 503
    ("try again") rather than 400 ("your request is wrong")."""


# What the AWS CLI prints when it never got an answer. Seen 2026-10-20 when the
# server's DNS (systemd-resolved, one upstream) dropped lookups intermittently:
# the raw "Could not connect to the endpoint URL" read like a bad path.
_NETWORK_SIGNS = ("could not connect to the endpoint url", "could not resolve",
                  "name or service not known", "temporary failure in name resolution",
                  "connect timeout", "read timeout", "connection was closed",
                  "connection reset", "endpointconnectionerror")
RETRY_DELAY_S = 5


def _is_network(msg):
    m = msg.lower()
    return any(s in m for s in _NETWORK_SIGNS)


def _aws(*args):
    """The AWS CLI, with a clear message instead of a traceback on failure.

    A NETWORK failure is retried once after RETRY_DELAY_S, since the outages
    seen were seconds long, and then raised as NetworkError with a message that
    says it is the server's connection, not the path. Any other failure (access
    denied, no such bucket) is raised at once, as before."""
    for attempt in (1, 2):
        try:
            out = subprocess.run(["aws", *args], capture_output=True, text=True,
                                 timeout=3600)
        except FileNotFoundError:
            raise ValueError("the AWS CLI is not installed on this server")
        if out.returncode == 0:
            return out.stdout
        lines = (out.stderr or out.stdout).strip().splitlines()
        msg = lines[-1] if lines else f"aws exited {out.returncode}"
        if not _is_network(msg):
            raise ValueError("S3: " + msg)
        if attempt == 1:
            time.sleep(RETRY_DELAY_S)
    raise NetworkError(
        "could not reach S3 from this server: a network or DNS problem on the "
        "server, not your path or your access. Try again in a minute; if it "
        "keeps happening, check the server's connection (resolvectl status). "
        f"AWS said: {msg}")


def list_objects(url):
    """{relative key: {"size", "storage", "etag"}} for everything under the path."""
    bucket, prefix = parse(url)
    raw = _aws("s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix,
               "--output", "json")
    contents = (json.loads(raw) if raw.strip() else {}).get("Contents", [])
    return {c["Key"][len(prefix):]: {"size": int(c["Size"]),
                                     "storage": c.get("StorageClass", "STANDARD"),
                                     "etag": c.get("ETag", "").strip('"')}
            for c in contents if c["Key"] != prefix}


def _status(rel, objects):
    o = objects.get(rel)
    if o is None:
        return "absent"
    return "archived" if o["storage"] in ARCHIVED else "pending"


def _fetch(url, rel, size, dest_dir):
    """Copy one object unless it is already here at the listed size."""
    bucket, prefix = parse(url)
    dest = os.path.join(dest_dir, rel)
    if os.path.isfile(dest) and os.path.getsize(dest) == size:
        return "fetched"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    _aws("s3", "cp", "--only-show-errors", f"s3://{bucket}/{prefix}{rel}", tmp)
    if os.path.getsize(tmp) != size:
        raise ValueError(f"S3: {rel} arrived truncated")
    os.replace(tmp, dest)
    return "fetched"


def write_record(dest_dir, record):
    tmp = os.path.join(dest_dir, RECORD + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(record, fh, indent=2)
    os.replace(tmp, os.path.join(dest_dir, RECORD))


def read_record(dest_dir):
    try:
        with open(os.path.join(dest_dir, RECORD)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def prepare(url, include_dapi=False):
    """List the S3 path, fetch the required and small files now, and plan the
    rest. Raises ValueError, before fetching anything, if a required file is
    missing or archived. Returns (local folder, record)."""
    objects = list_objects(url)
    if not objects:
        raise ValueError(f"nothing found at {url} (wrong path, or no access)")
    for rel in REQUIRED:
        st = _status(rel, objects)
        if st != "pending":
            raise ValueError(f"{rel} is {st} at {url}" + (
                "; restore it from the archive in S3 first" if st == "archived" else ""))
    dest = local_dir(url)
    os.makedirs(dest, exist_ok=True)
    files = {}
    wanted = list(REQUIRED) + list(SMALL) + list(BACKGROUND) + ([DAPI] if include_dapi else [])
    for rel in wanted:
        o = objects.get(rel, {})
        files[rel] = {"status": _status(rel, objects), "size": o.get("size"),
                      "etag": o.get("etag"), "storage": o.get("storage"),
                      "when": "now" if rel in REQUIRED + SMALL else "background"}
    if not include_dapi and DAPI in objects:
        o = objects[DAPI]
        files[DAPI] = {"status": "not requested", "size": o["size"], "etag": o["etag"],
                       "storage": o["storage"], "when": "background"}
    for rel, f in files.items():
        # already here at the listed size (an earlier job on this path): say so
        path = os.path.join(dest, rel)
        if (f["status"] == "pending" and os.path.isfile(path)
                and os.path.getsize(path) == f["size"]):
            f["status"] = "fetched"
    for rel in list(REQUIRED) + list(SMALL):
        if files[rel]["status"] == "pending":
            files[rel]["status"] = _fetch(url, rel, files[rel]["size"], dest)
    record = {"url": url, "listed": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": files}
    write_record(dest, record)
    return dest, record


def start_background(dest):
    """Fetch the background files in a detached process, so job creation does
    not wait for gigabytes. Safe to call twice: the worker takes a lock."""
    if not any(f["status"] == "pending" and f["when"] == "background"
               for f in (read_record(dest) or {}).get("files", {}).values()):
        return False
    log = open(os.path.join(dest, "s3_fetch.log"), "a")
    subprocess.Popen([sys.executable, "-m", "web.s3source", dest], cwd=_REPO,
                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return True


def _background(dest):
    lock = os.path.join(dest, ".s3_fetch.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print("another fetch is running for this folder; leaving it to that one")
        return
    try:
        record = read_record(dest)
        for rel, f in record["files"].items():
            if f["status"] != "pending" or f["when"] != "background":
                continue
            print(f"fetching {rel} ({(f['size'] or 0) / 1e9:.2f} GB) ...", flush=True)
            f["status"] = "fetching"
            write_record(dest, record)
            try:
                f["status"] = _fetch(record["url"], rel, f["size"], dest)
            except Exception as e:                       # noqa: BLE001
                f["status"] = "failed"
                f["error"] = str(e)
            write_record(dest, record)
            print(f"  {rel}: {f['status']}", flush=True)
    finally:
        os.close(fd)
        os.remove(lock)


if __name__ == "__main__":
    _background(sys.argv[1])

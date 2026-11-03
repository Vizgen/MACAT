"""Images derived from a bundle must not be cacheable across bundle versions.

Jobs on one section SHARE a single manual_bundle_<tag>.npz, and every
registration overwrites it in place. `/api/dapi.png` and
`/api/marker_density.png` carry `max-age=3600`, so their URL is the only thing
standing between a reader and an hour-old image of a DIFFERENT alignment.

The server-side caches key on the bundle's content fingerprint and were always
right. The BROWSER was not: the Results overlay requested those images without a
cache key, so after several jobs on one section it drew correctly-placed regions
over a background from a previous AP -- "curation aligned well, the results look
shifted".

A job timestamp is not a sufficient key, which is why these check the bundle
fingerprint specifically: another job's registration can overwrite the bundle
while this job's `updated` stands still.
"""
import os
import re
import sys
import unittest

_pre = os.path.join(sys.prefix, "lib", "libstdc++.so.6")
if os.path.exists(_pre) and _pre not in os.environ.get("LD_PRELOAD", ""):
    os.environ["LD_PRELOAD"] = _pre + (
        ":" + os.environ["LD_PRELOAD"] if os.environ.get("LD_PRELOAD") else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)

_STUDY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STUDY not in sys.path:
    sys.path.insert(0, _STUDY)

_SRC = os.path.join(_STUDY, "web", "frontend", "src")
CACHED = ("dapi.png", "marker_density.png")


def _read(*p):
    with open(os.path.join(*p)) as fh:
        return fh.read()


def _code(*p):
    """Source with // comments removed.

    The scan below measures how close the cache key sits to the URL it belongs
    to, and a long explanatory comment between them is not a defect -- leaving
    comments in made the first version of this test fail on correct code.
    """
    out = []
    for line in _read(*p).splitlines():
        i = line.find("//")
        out.append(line if i < 0 else line[:i])
    return "\n".join(out)


class FingerprintTests(unittest.TestCase):

    def test_bundle_meta_exposes_a_content_fingerprint(self):
        src = _read(_STUDY, "web", "server.py")
        self.assertRegex(src, r'"fp":',
                         "/api/bundle no longer exposes a fingerprint, so the "
                         "clients have nothing exact to key their image URLs on")

    def test_the_fingerprint_changes_when_the_file_does(self):
        """mtime alone would miss a same-second rewrite; size alone would miss a
        rewrite of the same length. The pair is what makes it exact."""
        import tempfile
        from web.server import _fp
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"a" * 100)
            path = f.name
        try:
            a = _fp(path)
            os.utime(path, (0, 0))
            b = _fp(path)
            self.assertNotEqual(a, b, "a rewritten file kept its fingerprint")
            with open(path, "wb") as fh:
                fh.write(b"a" * 200)
            self.assertNotEqual(b, _fp(path))
        finally:
            os.unlink(path)

    def test_a_missing_file_does_not_raise(self):
        from web.server import _fp
        self.assertIsNone(_fp("/definitely/not/here.npz"))


class ClientCacheKeyTests(unittest.TestCase):
    """Both steps that draw these images must key their URL on the bundle."""

    CLIENTS = (("steps", "ResultsStep.jsx"), ("steps", "CurationStep.jsx"))

    def test_the_cached_images_are_only_requested_with_a_key(self):
        for parts in self.CLIENTS:
            src = _code(_SRC, *parts)
            for ep in CACHED:
                for m in re.finditer(re.escape(ep), src):
                    # the URL is built over the following couple of lines
                    window = src[m.end():m.end() + 300]
                    self.assertIn("&v=", window,
                                  f"{parts[-1]} requests {ep} without a cache "
                                  f"key; the browser will reuse an image from a "
                                  f"previous bundle for up to an hour")

    def test_the_key_is_the_bundle_fingerprint_not_a_job_timestamp(self):
        """`job.updated` looks like it would work and does not: another job's
        registration can overwrite the shared bundle without touching this
        job."""
        for parts in self.CLIENTS:
            src = _code(_SRC, *parts)
            for ep in CACHED:
                for m in re.finditer(re.escape(ep), src):
                    window = src[m.end():m.end() + 300]
                    key = re.search(r"&v=\$\{encodeURIComponent\(([^)]*)\)", window)
                    self.assertIsNotNone(key, f"{parts[-1]}: {ep} has no readable key")
                    self.assertIn("fp", key.group(1),
                                  f"{parts[-1]}: {ep} keys on {key.group(1)!r} "
                                  f"rather than the bundle fingerprint")

    def test_density_png_needs_no_key_because_it_is_not_cached(self):
        """Guard the guard: if density.png ever gains max-age it must join the
        list above rather than quietly going stale."""
        src = _read(_STUDY, "web", "server.py")
        i = src.index('def density_png') if 'def density_png' in src else None
        if i is None:
            self.skipTest("density.png handler not found by name")
        self.assertIn("no-store", src[i:i + 2000],
                      "density.png is now cached; add it to CACHED here and give "
                      "its callers a &v= key")


if __name__ == "__main__":
    unittest.main()

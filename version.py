"""What code produced this.

A report .zip is a deliverable: it goes into a paper, a slide, a handoff. Its
manifest already records which experiment, which atlas and which AP slice, but
until now it recorded nothing about the software -- and the labelling logic
moves. Two archives with identical inputs could differ because the code between
them changed, and nothing in either one said so.

The version is read from GIT TAGS when a checkout is present, rather than from a
constant someone has to remember to bump. A constant drifts: the tag is what a
colleague would check out to reproduce the run, so the tag is what gets
recorded. `_FALLBACK_VERSION` is only for a copy deployed without `.git`, and it
is reported as such so nobody mistakes a guess for a fact.

`dirty` is the field to read first. A report generated from a working tree with
uncommitted changes is NOT reproducible from its SHA, and saying so plainly is
better than an archive that looks authoritative and is not.
"""
import os
import subprocess

# Only used when there is no git checkout to ask. Reported with
# source == "fallback" so it is never mistaken for a tag.
_FALLBACK_VERSION = "0.0.0-unknown"

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _git(*args):
    """Run a git command in the repo, or return None if that is not possible.

    Every failure mode -- no git binary, no .git directory, a non-zero exit --
    is the same answer here: we cannot know. None says that; a partial string
    would not.
    """
    try:
        out = subprocess.run(("git", "-C", _ROOT) + args,
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _compute():
    sha = _git("rev-parse", "HEAD")
    if sha is None:
        return {"version": _FALLBACK_VERSION, "version_source": "fallback",
                "git_sha": None, "git_describe": None, "dirty": None,
                "note": "no git checkout; the version is a compiled-in "
                        "fallback and this report cannot be traced to a commit"}
    tag = _git("describe", "--tags", "--abbrev=0")
    describe = _git("describe", "--tags", "--always", "--dirty")
    # --porcelain is empty exactly when the tree is clean; None means the
    # command failed, which is not the same as clean and must not be reported so
    status = _git("status", "--porcelain")
    dirty = None if status is None and _git("status") is None else bool(status)
    info = {
        "version": tag or _FALLBACK_VERSION,
        "version_source": "git tag" if tag else "fallback (no tag reachable)",
        "git_sha": sha,
        "git_describe": describe,
        "dirty": dirty,
    }
    if dirty:
        info["note"] = ("the working tree had uncommitted changes; this output "
                        "is NOT reproducible from git_sha alone")
    return info


# Computed once, at import. Deliberately not per call: if someone checks out a
# different commit while the server is running, the RUNNING code is still what
# produced the report, and that is what this has to describe.
BUILD = _compute()


def build_info():
    """A copy, so a caller cannot mutate what every later report reports."""
    return dict(BUILD)

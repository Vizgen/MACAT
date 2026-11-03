"""The Allen reference files MACAT reads, and a clear error when one is missing.

They are derived from Allen Institute data (CC BY-NC 4.0 and the Allen Terms of
Use), so a public copy of MACAT does not ship them: each user builds them once
(README, "Build the Allen reference files"). Without this module, a missing file
surfaced as a bare ``FileNotFoundError`` deep inside registration, which named
the file but not what it is or how to get it.
"""
import os

import config

# file -> what needs it, as a user would recognise it
FILES = {
    "structure_tree_safe_2017.csv": "the Allen region hierarchy, used from registration on",
    "allen_abc_region_by_gene.csv": "the expression QC per region",
    "allen_abc_section_by_gene.csv": "the coronal section QC",
    "allen_abc_sagittal_by_gene.csv": "the sagittal section QC",
}

HOW_TO_BUILD = ('Build the Allen reference files once from Allen\'s public release: '
                'see the README, "Build the Allen reference files", or '
                'data/reference/README.md, "Rebuilding them".')


class MissingReferenceFile(FileNotFoundError):
    """An Allen reference file is not where MACAT reads it."""

    def __init__(self, path):
        need = FILES.get(os.path.basename(path))
        self.message = (f"Allen reference file missing: {path}"
                        + (f" (needed for {need})." if need else ".")
                        + " " + HOW_TO_BUILD)
        super().__init__(self.message)
        self.filename = path

    def __str__(self):
        # OSError formats itself from errno/filename once filename is set
        return self.message


def require(path):
    """`path`, or MissingReferenceFile if it is not a file."""
    if not os.path.isfile(path):
        raise MissingReferenceFile(path)
    return path


def missing(folder=None):
    """Full paths of the reference files not present in `folder`."""
    folder = folder or config.REFERENCE_DATA_DIR
    return [os.path.join(folder, f) for f in FILES
            if not os.path.isfile(os.path.join(folder, f))]

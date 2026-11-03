"""Small atlas helpers shared by production preview code."""

import numpy as np


def region_id_set(atlas, acronym):
    """Return annotation IDs for a structure acronym and all descendants."""
    acronyms = [acronym] + list(atlas.get_structure_descendants(acronym))
    ids = []
    for item in acronyms:
        try:
            ids.append(int(atlas.structures[item]["id"]))
        except KeyError:
            pass
    return np.array(sorted(set(ids)))

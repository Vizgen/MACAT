## Contributing & Code of Conduct

Vizgen values community engagement on our open-source tools. Community contributions are encouraged!

Please follow the Contribution Guidelines below:

1. Both bug fixes and new features should be branched from `main`. Name the branch for what it changes, for example `fix/ap-slider-bounds` or `feature/vizualizer-export`.
2. Code contributions should include appropriate tests, and must pass the full check before review. Run the Python suite in the `macat` environment and the JavaScript suites with Node 20 (see [docs/architecture.md](docs/architecture.md#toolchains) for both toolchains):

   ```bash
   python -m unittest discover -s tests -p 'test_*.py'
   for t in tests/*.mjs; do node "$t"; done
   cd web/frontend && npm run build
   ```

   The built page in `web/frontend/dist/` is committed, so users never need Node: after any change under `web/frontend/src/`, run `npm run build` and commit `dist/` together with the source. `node tests/test_frontend_dist.mjs` fails if you forget.

   If you touch the warp math in `allen_alignment/warp.py` or `web/frontend/src/warp.js`, also run `python validation/_grid_parity.py`, the only check that the backend and the frontend still agree. Tests that change behaviour a user sees should exercise it through the job API or the server handlers, not only the helper underneath. [docs/development_testing.md](docs/development_testing.md) covers which suite to run after which edit, and how to test against a scratch job store instead of your real one.
3. Follow the style of the surrounding code. Lines are at most 127 characters, matching the `flake8` setting of Vizgen's other open-source tools. `flake8` is not part of `macat`; install it in your own environment to check.
4. Describe the change in the pull request: the reason, any data invariant it affects, the tests you ran and what they showed, and any new risk or rollback path. Add a line to [CHANGELOG.md](CHANGELOG.md) under an "Unreleased" heading at the top (create it if needed) if users will notice the change.
5. Never commit customer data. Region paths, `data/metadata/regions.local.json`, `data/metadata/excluded_regions.txt`, job outputs under `pipeline_output/`, and experiment or customer names in file names, commit messages and test fixtures all stay out of the repository. Test fixtures use synthetic data.
6. Before creating a pull request, rebase your branch from `main` and resolve any merge conflicts.
7. Create a pull request for review. Releases are tagged on the merged commit as `vMAJOR.MINOR.PATCH`; the tag is what `version.py` stamps into every `report.zip` (see [version.py](version.py)).

By contributing, you agree that your contributions are licensed under the [Apache License, Version 2.0](LICENSE.md).

Click here to access the [Code of Conduct](CODE_OF_CONDUCT.md).

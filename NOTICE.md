# Attribution and provenance / 原作者署名与来源

## Upstream BlueSky

This repository is an unofficial research derivative of **BlueSky — The Open Air Traffic Simulator**, developed by TU Delft researchers and the BlueSky contributors.

- Upstream: https://github.com/TUDelft-CNS-ATM/bluesky
- Principal authors named by the upstream project: **Jacco M. Hoekstra** and **Joost Ellerbroek**.
- Upstream reference commit: `46a2a8a5fa4e0bdd6f6991fefc70dc3512f8fc53`.
- The complete upstream MIT license is retained verbatim in `LICENSE`, including **Copyright (c) 2025 TU Delft** and the permission and warranty notices.
- The upstream README is preserved as `README-UPSTREAM.md`; its citation information and upstream `pyproject.toml` author metadata are retained.
- Existing source-file notices and third-party license files remain in place. This notice does not replace their terms or claim their authorship.

Recommended upstream citation:

J. M. Hoekstra and J. Ellerbroek, “BlueSky ATC Simulator Project: an Open Data and Open Source Approach,” Proceedings of the 7th International Conference for Research in Air Transportation (ICRAT), 2016.

## COORD additions

The **COORD v0.1.0** research additions are published and maintained by [91longli666-rgb](https://github.com/91longli666-rgb). The upstream simulator and its pre-existing algorithms are not claimed as original work of this repository's maintainer.

New contributions include `bluesky/plugins/coord.py`, `bluesky/plugins/coord_core.py`, `tests/coord/`, `research/coord/`, `scenario/COORD/`, the COORD launch script and requirements, and the new project documentation. These additions are made available under the MIT license in `LICENSE-COORD`.

The main README was adapted to describe this derivative. The upstream package-publishing workflow is restricted to the upstream repository. A publication-specific `.gitignore` addition excludes local environments and scratch directories. Changed upstream paths are recorded in `research/coord/UPSTREAM.json`.

The optimisation scope, assumptions, failures and limitations are documented in `README-COORD.md` and the experiment report. No upstream endorsement is implied.

---
title: An interactive documentation site for muscadet - Scoping
type: plan
date: 2026-11-23
topic: docs-site
artifact_readiness: scoping
---

# An interactive documentation site for muscadet

## Problem

Everything a reader can learn about muscadet lives in one 3 000-line README.
It is accurate and reads as a reference: no learning path, no figure produced
by a run, no comparison of the two engines a model runs on. The repository is
public and has no GitHub Pages site, no wiki content, no notebook and no CI.

## Decisions taken (23/11/2026)

| Question | Decision |
|---|---|
| Tool | MkDocs Material, notebooks rendered by `mkdocs-jupyter`, API reference by `mkdocstrings` |
| Interactivity | notebooks executed locally, outputs committed, Plotly figures interactive in the page; CI builds and publishes only |
| Engine comparison | public, black-box measurements only |
| Hosting | GitHub Pages of `edgemind-sas/muscadet`, published by a GitHub Action on every merge to `master` |
| Language | English, figures included (repository rule) |

Why local execution: neither PyCATSHOO (licence: internal use, not
redistributable) nor RAICHU's compiled extension installs on a public runner.
A notebook therefore cannot run in CI, and live in-browser execution
(JupyterLite) is out of reach for the same reason.

## Constraints

- **PyCATSHOO sources.** The site is public. What it says about PyCATSHOO rests
  on the public documentation and on black-box measurement of the distributed
  version ("measured on 1.4.1.0 as distributed"). No name, structure or
  behaviour learnt from the sources. Every engine page gets a review against
  this rule before merge.
- **PyCATSHOO documentation** may not be copied (licence, article 2): link to
  it, never reproduce it.
- **Outputs drift from code.** A committed output is a claim about a version.
  Each notebook records the muscadet and engine versions it ran on, and a
  local command re-executes them all (see Phase 2).

## Site outline

1. **Getting started**: installation (PyCATSHOO installed separately), a first
   reliability block diagram.
2. **Tutorials** (notebooks, one notion each, each ending on a plot):
   - discrete propagation: and/or/k-out-of-n logic over an RBD;
   - failure modes and automata: exponential, delay, common cause, deratings;
   - interactive simulation: `isimu_step_to`, what moves the clock;
   - a first continuous chain: source, consumer, demand and allocation;
   - transformation rules: coefficients as a ratio, the limiting reagent;
   - capacities: fill, serve ceiling, bounds, a commanded discharge;
   - a control loop: sensor, deadband, measurement links;
   - time profiles and their composition with deratings;
   - transfer pairs: the domestic heating circuit;
   - mixtures: the ventilated room, the open volume;
   - declaring a component from data.
3. **Concepts** (Markdown + SVG, recast from the README): smart components;
   discrete flows; the three continuous sweeps (capability, demand,
   production); allocation; capacities; rules; refused loops; the initial
   fixpoint; modelling pitfalls.
4. **Engines**:
   - translation: how one muscadet model is handed to PyCATSHOO and to RAICHU,
     shown from muscadet's side (what muscadet declares through each public
     API);
   - results: one benchmark set run on both, availability and levels side by
     side, with confidence intervals and wall-clock time;
   - known deviations, generated from `muscadet.conformance`.
5. **API reference**, generated from the docstrings.
6. **Changelog**, included from `CHANGELOG.md`.

The site lives in `docs/manual/`, so `docs/plans/`, `docs/review/` and
`docs/theory/` stay out of it. `docs/images/` is shared.

## Phases

| Phase | Content | Done when |
|---|---|---|
| 1. Skeleton | `mkdocs.yml`, Pages workflow, Getting started, API reference, changelog | the site is online with the API reference |
| 2. Tutorials | the notebooks above, plus `make docs-exec` re-running them and checking each one's recorded versions | every tutorial renders with its plots, re-executes cleanly |
| 3. Concepts | README sections recast as concept pages with schematics | every concept the README documents has a page |
| 4. Engines | translation, benchmark, deviations | the benchmark runs on both engines from one command |
| 5. README | README reduced to an introduction pointing at the site | no concept documented in two places |

Phase 5 matters: two long documents describing one behaviour will drift. The
site becomes the reference and the README stops being one.

## Open points

- **RAICHU distribution**: is `pyraichu` installable from a published wheel,
  or only from a checkout? Decides how a reader reproduces the engine pages.
- **Benchmark set**: which models. Candidates: an RBD with a known analytic
  availability, the heated tank, the H2 plant, the ventilated room.
- **Notebook tests**: run them under pytest (`nbmake`, marked `integration`)
  or keep `make docs-exec` alone.

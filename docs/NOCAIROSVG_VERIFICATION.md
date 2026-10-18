# NoCairoSVG pre-flight and the resvg-py swap — verification record

- **Date:** 2026-10-18 (Australia/Brisbane)
- **Task:** ranked work #1 — remove the libcairo requirement from SVG previews.
- **Repository revision:** `7234f8a` (`main`) + uncommitted working-tree change described below.
- **Environment:** Windows 11, Python 3.11.15, DSH `workspace-write` sandbox. No Docker, no WSL.
- **Companion documents:** pre-flight evidence in [NOCAIROSVG_PREFLIGHT.md](NOCAIROSVG_PREFLIGHT.md); harness and raw evidence under `.tmp/nc-preflight/` (git-ignored, session-local).

## Bottom line

1. **`NoCairoSVG` was rejected at pre-flight** (licence is fine; maintenance and dependency constraints are not). It was never added to the project.
2. **The underlying goal was implemented with `resvg-py`** instead, after explicit sign-off: SVG previews now rasterise in-process from a self-contained wheel, with CairoSVG kept as an optional fallback backend. No libcairo is required on the preview host.
3. Nothing was committed or pushed. Only the files listed under "Implemented change" differ from `7234f8a`.

## Phase 1 — NoCairoSVG pre-flight (REJECT)

| Gate | Result | Evidence |
| --- | --- | --- |
| Licence is permissive | **PASS** | MIT, "Copyright (c) 2021 Kieran W" (`NOCAIROSVG_PREFLIGHT.md` §1) |
| Maintained / not abandoned | **FAIL** | last release `2024` on 2024-01-07 (33.3 months); last commit 2024-12-01 (22.5 months); 4 stars, 1 fork (§2) |
| Installable alongside this plugin | **FAIL** | `nocairosvg` 2024 requires `Pillow>=10.2.0,<11`; `hermes-widget` requires `Pillow>=12.3.0,<13` — empty intersection |
| Drop-in SVG→PNG backend | **FAIL** | Chromium install+launch per render; `svg2svg`/`svg2ps` raise `NotImplementedError`; requested output size discarded (§4) |

Executed evidence with exit codes:

| Command (abridged) | Exit | Result |
| --- | --- | --- |
| `pip install --dry-run --ignore-installed "Pillow>=12.3.0,<13" nocairosvg` | **1** | `ERROR: ResolutionImpossible`; `nocairosvg 2024 depends on Pillow<11 and >=10.2.0` |
| `pip install --target …/site --no-deps nocairosvg playwright install-playwright` | **0** | installed only into a workspace target (nocairosvg 2024, playwright 1.64.0, install-playwright 1.0.2) |
| `import nocairosvg` with the browser stack blocked | **1** | `ModuleNotFoundError` — inert without Playwright |
| `nocairosvg.svg2png(...)` (270x120 SVG) | **1** | `PermissionError: [WinError 5]` in `asyncio/windows_utils.py:pipe` — no PNG; harness: render exit **2**, 0/3 rendered |
| `import cairosvg` on a host with no discoverable libcairo | **1** | `cannot load library 'libcairo-2.dll': error 0x7e` |

The requested "swap it in and verify" was therefore not performed: the dependency cannot be installed, and no sample could be rendered with it.

## Phase 2 — resvg-py swap (implemented, approved)

`resvg-py` 0.5.0: MIT wrapper ("Copyright (C) 2024, baseplate-admin"); bundled Rust crates are Apache-2.0 OR MIT / BSD-3-Clause; `cp310-abi3` wheel (1.24 MB) with no system library and no runtime deps; last release 2026-08-24.

### Implemented change

| File | Change |
| --- | --- |
| `hermes-plugin/hermes-widget/pyproject.toml` | `preview` extra is now `["resvg-py>=0.5,<0.6", "CairoSVG>=2.9.1,<3"]` — resvg first, CairoSVG retained only as a fallback/comparison backend |
| `hermes-plugin/hermes-widget/preview.py` | new `_render_svg()` picks resvg-py, then CairoSVG, else returns `None` so the existing `svg-renderer-unavailable` placeholder is emitted; `_resvg_module()` uses the same `sys.modules` sentinel convention as the CairoSVG loader; success still returns the `pillow` renderer because the preview canvas is Pillow-composed |
| `hermes-plugin/hermes-widget/tools.py`, `cli.py`, `server.py` | diagnostic text is backend-agnostic: `No local SVG renderer (resvg-py or CairoSVG)` |
| `hermes-plugin/hermes-widget/tests/test_feature_proposals.py` | missing-renderer test now disables both backends; new test asserts a real render when either backend is importable (skips on a Pillow-only host) |
| `.github/workflows/ci.yml` | optional-backend installs add `resvg-py`; the `preview-renderers` matrix is `resvg-cairosvg` vs `pillow-only`, so the placeholder path stays covered on a backend-free leg |
| `README.md` | preview limitations paragraph now documents resvg-py + `pip install "hermes-widget[preview]"` |
| `CHANGELOG.md` | Unreleased entry for the change |

### Validation after the swap

| Check | Command | Exit | Result |
| --- | --- | --- | --- |
| New extra resolves with the Pillow floor | `pip install --dry-run "Pillow>=12.3.0,<13" "resvg-py>=0.5,<0.6"` | **0** | would install `pillow-12.3.0`, `resvg_py-0.5.0` |
| Preview contract tests, resvg available | `unittest discover -p test_feature_proposals.py` | **0** | 30 tests OK (real SVG render exercised) |
| Preview contract tests, no SVG backend | same, without the backend on `PYTHONPATH` | **0** | 30 tests OK, 1 skipped (backend-required test) |
| Full plugin suite | per-file `unittest discover` | **0** | 10/10 files, 149 tests, 0 failures (1 skip on a backend-free host) |
| Contract parity host↔Android | `python scripts/check-contract-parity.py` | **0** | publication limits, actions, media, event sources, size bands, refresh interval PASS |
| Lint | `ruff check hermes-plugin/hermes-widget` | **0** | All checks passed |
| Workflow YAML | `python scripts/check-workflow-yaml.py` | **0** | every workflow parses and has steps |
| Shipped path end-to-end | `render.py repo` with resvg on `PYTHONPATH` | **0** | 3/3 samples rendered, `renderer="pillow"`, not the placeholder |

`test_release_gates.py` is excluded from the local suite run: the sandbox blocks `git`'s signal pipe (`Win32 error 5`), an environment limitation unrelated to this change; it runs in CI.

### Pixel output vs CairoSVG (genuine baseline: cairosvg 2.9.1 / cairo 1.18.4 via Inkscape's `libcairo-2.dll`)

Measured through the shipped `preview._fit_image` path at 270x120 (`out/report.md` in the harness). Thresholds: MISMATCH if >2% of pixels differ by >8/255 or mean absolute error >4/255.

| sample | mean abs | max abs | %>2/255 | %>8/255 | diff bbox | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| sample1_text | 2.631 | 212 | 6.34% | 5.66% | [10,21]–[207,99] (glyphs only) | **MISMATCH** — font-stack divergence |
| sample2_shapes | 0.304 | 67 | 2.91% | 1.95% | [11,13]–[256,106] | **MATCH** |
| sample3_opacity | 0.271 | 23 | 2.54% | 0.30% | [9,7]–[261,116] | **MATCH** |

Structural checks on the same output: 5/5, 10/10, 8/8 (dimensions, opacity, non-blank, expected colours derived by hand from the SVG source). The text mismatch is confined to the glyph bounding box: `font-family="sans-serif"` resolves to different physical fonts in cairo/fontconfig vs resvg's font database. Shapes, gradients and opacity/compositing match within tolerance.

## Acceptance criteria (re-scoped, per sign-off)

| Criterion | Result |
| --- | --- |
| The dependency is added and CairoSVG removed from the required path | **PASS** — `resvg-py` added; libcairo no longer required. CairoSVG remains listed in the optional `preview` extra as a fallback backend, by design |
| SVG→PNG output matches CairoSVG on ≥3 samples (text, shapes, opacity) | **PARTIAL, measured** — shapes and opacity MATCH; text mismatches only in glyph rasterisation because fonts are host-resolved. Follow-up: pin fonts (`font_files` / `skip_system_fonts`) to remove the class |
| Preview runs on a clean host without libcairo | **PASS** — resvg-py renders in-process (3/3 samples, exit 0) with no libcairo present; the CairoSVG import refusal on such a host is recorded above |

## Known limitations and follow-ups

1. **Text fidelity:** pin the preview font set so text output is stable across hosts (resvg-py supports `font_files`, `font_dirs`, `skip_system_fonts`). No font asset is shipped today, so this needs a product decision.
2. **CI pixel goldens:** CI now exercises both backend-present and backend-absent paths functionally, but does not yet enforce the CairoSVG pixel comparison. The existing `preview-renderers` ubuntu job is the right home for it; this Windows host cannot supply libcairo without an external DLL.
3. **Versioning:** `pyproject.toml` version stays `3.5.0`; a release bump/changelog versioning decision is left to the maintainer (no release was made).
4. **Housekeeping:** the two new `docs/NOCAIROSVG_*.md` files and all product edits are uncommitted; nothing was pushed.

## Reproduction

```powershell
# pre-flight evidence (workspace target only)
$env:PYTHONPATH = "$repo\.tmp\nc-preflight\shim"; $env:TEMP = "$repo\.tmp\nc-preflight\tmp"; $env:TMP = $env:TEMP
python -m pip install --dry-run --ignore-installed "Pillow>=12.3.0,<13" nocairosvg   # exit 1
python .tmp\nc-preflight\probe_nocairosvg.py                                          # exit 1

# renderer harness: samples, genuine CairoSVG baseline, pixel diff
powershell -NoProfile -ExecutionPolicy Bypass -File .tmp\nc-preflight\verify\run.ps1

# plugin suite (workspace-local state so the sandbox can write it)
$env:HERMES_HOME = "$repo\.tmp\nc-preflight\home\.hermes"
python -m unittest discover -s hermes-plugin/hermes-widget/tests
```

## Evidence map

| Artifact | Path |
| --- | --- |
| Pre-flight report (licence, maintenance, alternatives) | `docs/NOCAIROSVG_PREFLIGHT.md` |
| nocairosvg wheel source + metadata | `.tmp/nc-preflight/wheel/` |
| nocairosvg runtime probe (exit 1 + traceback) | `.tmp/nc-preflight/probe-nocairosvg.log`, `probe-nocairosvg.txt` |
| Render harness, samples, reports, diff images | `.tmp/nc-preflight/verify/`, `out/report.md`, `out/report.json`, `out/diffs/` |
| Raw PyPI/GitHub responses with SHA-256 | `.tmp/nc-preflight/forensics/EVIDENCE_INDEX.md` |

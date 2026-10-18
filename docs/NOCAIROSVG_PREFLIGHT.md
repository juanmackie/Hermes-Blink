# NoCairoSVG pre-flight: licence, maintenance and libcairo-free backend scan

- **Status:** complete (source and metadata forensics; no renderer was executed by this report)
- **Outcome (2026-10-18):** verdict applied — `nocairosvg` was not adopted. The recommended
  `resvg-py` backend was implemented after sign-off; see
  [NOCAIROSVG_VERIFICATION.md](NOCAIROSVG_VERIFICATION.md).
- **Date of all retrievals:** 2026-10-18 (Australia/Brisbane)
- **Scope:** `FHPythonUtils/NoCairoSVG` (PyPI `nocairosvg==2024`) considered as a drop-in replacement for
  CairoSVG in the Hermes-Blink widget preview path (`hermes-plugin/hermes-widget/preview.py`, SVG branch).
- **Hermes-Blink revision examined:** `7234f8a99bd5ca9d86d72b138397865e7aaa66a5` (working tree, 2026-10-15).
- **Raw evidence:** `.tmp/nc-preflight/forensics/` — index with sizes and SHA-256 in
  [`EVIDENCE_INDEX.md`](../.tmp/nc-preflight/forensics/EVIDENCE_INDEX.md). `.tmp/` is git-ignored, so treat those
  paths as session-local artifacts, not permanent citations.

## Verdict

**REJECT** NoCairoSVG for Hermes-Blink.

**Single blocking condition:** `nocairosvg` 2024 declares `Pillow (>=10.2.0,<11)`
(`.tmp/nc-preflight/wheel/nocairosvg-2024.dist-info/METADATA:25`), while `hermes-widget` requires `Pillow>=12.3.0,<13`
(`hermes-plugin/hermes-widget/pyproject.toml:5` and `hermes-plugin/hermes-widget/requirements.txt:2`).
Those ranges have an empty intersection, so the library cannot be added to the plugin's dependency set at all:
pip resolves the environment to "no version of Pillow satisfies both". Working around it (`--no-deps`, a local
fork, or a metadata patch) means running an unsupported configuration below the plugin's declared Pillow floor.

The conflict alone decides the question, but the source review below shows NoCairoSVG would remain unsuitable
even with a relaxed Pillow pin: every render shells out to install and launch Chromium, writes fixed-name temp
files inside its own `site-packages` directory, ignores the requested output dimensions, does not implement two
of CairoSVG's six functions, and can hang a request for 30 s on an unloadable SVG.

**Recommended path that keeps the original goal (no libcairo on the preview host):** add `resvg-py==0.5.*`
(MIT, Rust `resvg`/`usvg`/`tiny-skia` inside an abi3 wheel, no system library, last release 2026-08-24) as an
optional preview extra and call `resvg_py.svg_to_bytes(...)` in `_fit_image`'s SVG branch.
Details in [Recommended path](#recommended-path).

> **What was not done.** This report did not import or run `nocairosvg`, did not install Chromium, and did not
> execute any alternative renderer. Runtime-behaviour statements below are either quoted from vendor
> documentation or explicitly labelled **source-level inference**. `render-verifier` owns the execution and
> pixel-diff experiment.

## Evidence summary

| # | Question | Finding | Source (retrieved 2026-10-18) |
| --- | --- | --- | --- |
| 1 | Licence | MIT, permissive, MIT-repo compatible; "Copyright (c) 2021 Kieran W" | `.tmp/nc-preflight/wheel/nocairosvg-2024.dist-info/LICENSE.md:1-3`; [PyPI JSON](https://pypi.org/pypi/nocairosvg/json) |
| 2 | Maintenance | Latest release "2024" on 2024-01-07; last commit 2024-12-01; 4 stars, 1 fork, 0 open issues, not archived | [PyPI JSON](https://pypi.org/pypi/nocairosvg/json); [GitHub repo](https://api.github.com/repos/FHPythonUtils/NoCairoSVG) |
| 3 | Dependency surface | `Pillow (>=10.2.0,<11)`, `playwright (>=1.40.0,<2)`, `install-playwright (>=0.0.1,<2)`; `Requires-Python >=3.8,<4.0`; wheel 9,407 B | `.tmp/nc-preflight/wheel/nocairosvg-2024.dist-info/METADATA:8,25-27`; [PyPI JSON](https://pypi.org/pypi/nocairosvg/json) |
| 4 | Drop-in API | `svg2png` works via Chromium canvas; `svg2svg`/`svg2ps` raise `NotImplementedError`; `svg2pdf`/`svg2eps` rasterise through Pillow | `.tmp/nc-preflight/wheel/nocairosvg/__init__.py:61,232,170-186,279-295` |
| 5 | Rendering architecture | One Playwright browser install + launch per call; fixed `temp.svg` in the package directory; 4000x4000 canvas | `.tmp/nc-preflight/wheel/nocairosvg/__init__.py:384,387,407-408,428-443`; `.tmp/nc-preflight/wheel/nocairosvg/convert.html:2` |
| 6 | Output sizing | `image.resize(...)` result discarded, so `output_width`/`output_height`/`scale` are ignored | `.tmp/nc-preflight/wheel/nocairosvg/__init__.py:351-354`; `hermes-plugin/hermes-widget/preview.py:210-212` |
| 7 | Dependency rot | `install-playwright` 1.0.0 (2025-12-29) changed `install(browser_type)` to `install(browser_types: list)`; `nocairosvg` passes one `BrowserType` and its range allows 1.0.2 | `.tmp/nc-preflight/forensics/install_playwright-0.0.1-py3-none-any.whl` vs `install_playwright-1.0.2-py3-none-any.whl` |
| 8 | Alternative | `resvg-py` 0.5.0: MIT wrapper, permissive Rust crates, no system library, `cp310-abi3-win_amd64` wheel, 2026-08-24 | [`resvg_py` wheel METADATA + LICENSE](https://pypi.org/pypi/resvg-py/json); [crates.io resvg](https://crates.io/api/v1/crates/resvg) |

## 1. Licence

**Finding: MIT. Permissive, non-copyleft, compatible with this MIT repository. No code-copy restriction
beyond MIT's notice requirement.**

Exact text in the wheel (`LICENSE.md`, 21 lines):

```text
MIT License

Copyright (c) 2021 Kieran W
```

Sources:

- `.tmp/nc-preflight/wheel/nocairosvg-2024.dist-info/LICENSE.md:1` — `# MIT License`; `:3` — `Copyright (c) 2021 Kieran W`.
- `.tmp/nc-preflight/wheel/nocairosvg-2024.dist-info/METADATA:6` — `License: MIT`;
  `:12` — `Classifier: License :: OSI Approved :: MIT License`.
- `.tmp/nc-preflight/wheel/nocairosvg-2024.dist-info/METADATA:237-239` — README section repeats
  `MIT License / Copyright (c) FredHappyface` (the same maintainer; GitHub login vs real name).
- GitHub `license` object: `spdx_id: MIT`, `key: mit` —
  [api.github.com/repos/FHPythonUtils/NoCairoSVG](https://api.github.com/repos/FHPythonUtils/NoCairoSVG).
- PyPI metadata `license: "MIT"` — [pypi.org/pypi/nocairosvg/json](https://pypi.org/pypi/nocairosvg/json).

Hermes-Blink itself is MIT (`LICENSE:1-3`, `Copyright (c) 2026 Hermes Blink contributors`), so the licence
raises no compatibility question. Note the MIT only covers the wrapper: the library delegates the actual work
to Playwright/Chromium (Apache-2.0), whose browser binaries are a separate download at render time.

## 2. Maintenance

**Finding: stale by the stated criterion (no release in > 24 months AND no commits in > 12 months). Both
conditions are met with margin. `archived=false` only means the author has not archived the repository; it is
not evidence of maintenance.**

### 2.1 PyPI release history — all versions

Source: [https://pypi.org/pypi/nocairosvg/json](https://pypi.org/pypi/nocairosvg/json), raw copy
`.tmp/nc-preflight/forensics/pypi_nocairosvg.json`. Each version shipped a wheel and an sdist; timestamps are
the wheel upload time.

| Version | Uploaded (UTC) |
| --- | --- |
| 2021 | 2021-01-04T18:56:26Z |
| 2021.0.1 | 2021-01-04T18:57:58Z |
| 2021.0.2 | 2021-01-04T20:30:17Z |
| 2021.0.3 | 2021-01-04T20:35:55Z |
| 2021.1 | 2021-01-05T13:40:01Z |
| 2021.1.1 | 2021-11-14T00:48:15Z |
| 2022 | 2022-01-23T17:40:36Z |
| 2022.0.1 | 2022-02-01T20:13:16Z |
| 2023 | 2023-01-08T21:18:40Z |
| 2023.1 | 2023-08-31T21:01:55Z |
| **2024** | **2024-01-07T21:05:11Z** (current latest) |

The version scheme is the calendar year plus patch numbers; there is no semantic-versioning contract.

### 2.2 GitHub repository metadata

Source: [https://api.github.com/repos/FHPythonUtils/NoCairoSVG](https://api.github.com/repos/FHPythonUtils/NoCairoSVG),
raw copy `.tmp/nc-preflight/forensics/gh_repo.json`.

| Field | Value |
| --- | --- |
| `full_name` | `FHPythonUtils/NoCairoSVG` |
| `created_at` | 2021-01-04T18:59:03Z |
| `stargazers_count` / `forks_count` | 4 / 1 |
| `open_issues_count` | 0 |
| `pushed_at` | 2024-12-01T16:40:07Z |
| `updated_at` | 2024-12-30T22:25:42Z |
| `archived` / `disabled` | `false` / `false` |
| `default_branch` | `master` |
| `license.spdx_id` | `MIT` |
| `topics` | `cairo`, `cairosvg`, `platform-independent`, **`pyppeteer`**, `svg` |

The `pyppeteer` topic is metadata drift: the current source imports `playwright.sync_api`, not pyppeteer
(`.tmp/nc-preflight/wheel/nocairosvg/__init__.py:13`).

### 2.3 Releases and tags

Source: [releases](https://api.github.com/repos/FHPythonUtils/NoCairoSVG/releases?per_page=10),
[tags](https://api.github.com/repos/FHPythonUtils/NoCairoSVG/tags?per_page=20).

| Release / tag | Published (UTC) |
| --- | --- |
| 2024 | 2024-01-07T21:06:02Z |
| 2023.1 | 2023-08-31T21:02:47Z |
| 2023 | 2023-01-08T21:20:01Z |

Three releases total, matching three tags. No release or tag since 2024-01-07.

### 2.4 Last commits

Source: [https://api.github.com/repos/FHPythonUtils/NoCairoSVG/commits?per_page=20](https://api.github.com/repos/FHPythonUtils/NoCairoSVG/commits?per_page=20),
raw copy `.tmp/nc-preflight/forensics/gh_commits.json`. All commits are authored by
`Kieran W <41634689+FredHappyface@users.noreply.github.com>`.

| SHA (10) | Authored (UTC) | Message |
| --- | --- | --- |
| `eb27eb0d8f` | 2024-12-01T16:40:07Z | Create mirror-to-codeberg.yaml |
| `81cdf372fd` | 2024-03-30T15:21:01Z | Create test-lint.yaml |
| `b0f98fa948` | 2024-03-17T21:12:14Z | update dev deps |
| `128e2bd1eb` | 2024-03-17T18:19:57Z | ruff |
| `f6a5d30065` | 2024-03-17T14:17:30Z | use ruff, remove pylint config |
| `f791ffa270` | 2024-02-28T01:01:13Z | Update index.md |
| `f43090719e` | 2024-02-28T00:06:40Z | migrate from blackt to ruff format + bump deps |
| `cc2e3760b9` | 2024-01-07T21:05:17Z | rel 2024 update dependencies (tag `2024`) |
| `15e09a2697` | 2023-08-31T21:01:48Z | 2023.1 |
| `a160a62e2a` | 2023-08-28T16:07:13Z | add 8 hour cache, fix pypi total downloads and remove repo size |

The only commits after the 2024 release are tooling/formatting commits (ruff, dev deps, index docs) and two
GitHub-workflow file additions. No package code has changed since 2024-03-17; the last release tag remains
`cc2e376` from 2024-01-07.

### 2.5 Age calculation and criterion

Criterion fixed by the task: **stale/abandoned if no release for > 24 months AND no commits for > 12 months.**
Measured against 2026-10-18:

| Anchor | Age |
| --- | --- |
| Latest release (2024-01-07) | 1,015 days ≈ 33.3 months (> 24 ✓) |
| Last commit (2024-12-01) | 686 days ≈ 22.5 months (> 12 ✓) |
| Last commit touching packaging/code (2024-03-17) | ≈ 31 months |
| Last release tag commit (`cc2e376`, 2024-01-07) | ≈ 33.3 months |

**Conclusion: abandoned/stale. Both thresholds are exceeded (33.3 > 24 and 22.5 > 12).** The repository is not
archived and has zero open issues, which can look healthy in a badges-only check; the timestamps are the
decisive evidence. A second, independent signal of rot is that the declared dependencies now resolve to
versions the code cannot call (see §3.3).

## 3. Dependency surface

### 3.1 Declared metadata

Sources: `.tmp/nc-preflight/wheel/nocairosvg-2024.dist-info/METADATA:8,25-27`;
[pypi.org/pypi/nocairosvg/json](https://pypi.org/pypi/nocairosvg/json); wheel
`.tmp/nc-preflight/wheel/nocairosvg-2024.dist-info/WHEEL`.

| Field | Value |
| --- | --- |
| `Requires-Python` | `>=3.8,<4.0` (METADATA:8) |
| `Requires-Dist` | `Pillow (>=10.2.0,<11)`, `playwright (>=1.40.0,<2)`, `install-playwright (>=0.0.1,<2)` (METADATA:25-27) |
| Wheel | `nocairosvg-2024-py3-none-any.whl`, 9,407 B, `Root-Is-Purelib: true`, generator poetry-core 1.8.1 |
| SHA-256 | `5828470932c49c3c7cc0dc0a36e4526b51d9d2c51655fb861c0547465929efa6` |
| Sdist | `nocairosvg-2024.tar.gz`, 9,361 B |
| Installed payload | two files only: `nocairosvg/__init__.py` (24,775 B) and `nocairosvg/convert.html` (924 B); see `RECORD` |

The wheel is tiny because it delegates all rendering work to Playwright and Pillow. That does not mean the
install is small: Playwright's own wheel is 38,675,591 B for Windows amd64
([pypi.org/pypi/playwright/json](https://pypi.org/pypi/playwright/json), version 1.64.0), and Playwright
requires browser binaries to be installed separately with its CLI
([playwright.dev/python/docs/browsers](https://playwright.dev/python/docs/browsers): "You will need to use the
Playwright CLI to install these browsers").

### 3.2 Hard conflict with Hermes-Blink's Pillow floor

Hermes-Blink declares and tests against a Pillow floor that excludes every version `nocairosvg` accepts:

- `hermes-plugin/hermes-widget/pyproject.toml:5` — `dependencies = ["defusedxml>=0.7.1,<1", "Pillow>=12.3.0,<13"]`
- `hermes-plugin/hermes-widget/requirements.txt:2` — `Pillow>=12.3.0,<13`
- `.github/workflows/ci.yml:29`, `:70`, `:73`, `:102` — CI installs `"Pillow>=10.4,<13"` for the host tests,
  the CairoSVG backend matrix and the Windows job.
- `.github/workflows/ci.yml:63` and `:94` — CI runs Python 3.13 on Ubuntu and 3.11 on Windows.

Consequences:

1. **Unsatisfiable intersection.** `nocairosvg` requires `Pillow>=10.2.0,<11`; `hermes-widget` requires
   `Pillow>=12.3.0,<13`. No Pillow release satisfies both, so
   `pip install hermes-widget nocairosvg` (or adding `nocairosvg` to the `preview` extra) fails with a
   resolution error rather than installing a suboptimal version.
2. **The version floor is real, not hypothetical.** Pillow 12.3.0 exists on PyPI (uploaded 2026-07-01, with a
   `pillow-12.3.0-cp311-cp311-win_amd64.whl`); 10.2.0 dates to 2024-01-02
   ([pypi.org/pypi/Pillow/json](https://pypi.org/pypi/Pillow/json), raw copy
   `.tmp/nc-preflight/forensics/pypi_Pillow.json`).
3. **CI's wider range masks the conflict rather than fixing it.** CI's `"Pillow>=10.4,<13"` is a superset of
   both ranges, so a CI job that additionally installed `nocairosvg` would resolve to Pillow 10.x and pass
   while the shipped plugin metadata forbids that version. The plugin's own floor (`>=12.3.0`) is what users
   install, so CI would be validating an environment the product does not support.
4. `nocairosvg`'s declared Python range (`>=3.8,<4.0`) is compatible with the plugin's `>=3.11`; Python is
   not a blocker.

### 3.3 Dependency rot: the resolver now picks an `install-playwright` the code cannot call

`nocairosvg` calls `install_playwright.install(p.chromium)` once per render
(`.tmp/nc-preflight/wheel/nocairosvg/__init__.py:429`). The helper's signature changed after this package was
released. I downloaded the small wheels from PyPI and read `install_playwright/__init__.py` in each
(artifacts under `.tmp/nc-preflight/forensics/`):

| install-playwright | Uploaded | `install()` signature | What `nocairosvg` gets |
| --- | --- | --- | --- |
| 0.0.1 | 2023-12-18 | `install(browser_type: SyncBrowserType \| AsyncBrowserType, ...)` | works |
| 0.1.0 | 2024-04-30 | singular `browser_type` | works |
| 0.1.1 | 2025-04-08 | singular `browser_type` | works |
| 1.0.0 | 2025-12-29 | `install(browser_types: list[...], ...)`, then `[bt.name for bt in browser_types]` | **TypeError: BrowserType is not iterable** |
| 1.0.1 | 2026-04-25 | list form | same |
| 1.0.2 | 2026-08-07 | list form (current) | same |

`nocairosvg`'s range `install-playwright (>=0.0.1,<2)` allows 1.0.2, and pip picks the newest on a fresh
install. The call site was never updated. **Source-level inference:** a fresh install today is expected to
raise `TypeError` at the first render, before Chromium launches. This is a direct consequence of the package
being unmaintained; it is not something a Hermes-Blink integration can patch from outside without vendoring or
monkey-patching the dependency. (No execution was performed by this report — the Lead/render-verifier own the
runtime experiment.)

## 4. Architecture and API

### 4.1 Function-by-function comparison with CairoSVG

All line numbers refer to the extracted wheel source
`.tmp/nc-preflight/wheel/nocairosvg/__init__.py` (642 lines) and the wheel `RECORD`.

| CairoSVG function | NoCairoSVG 2024 implementation | Line evidence |
| --- | --- | --- |
| `svg2png()` | Supported: delegates to `svg2bitmap(..., "png")` | `:108-123` |
| `svg2svg()` | `raise NotImplementedError` | `:18-61`, raise at `:61` |
| `svg2pdf()` | Rasterises via `svg2bitmap(..., "pdf", False)` (Pillow PDF save), not vector | `:126-186`, delegate at `:170-186` |
| `svg2ps()` | `raise NotImplementedError` | `:189-232`, raise at `:232` |
| `svg2eps()` | Rasterises via `svg2bitmap(..., "eps", False)` (Pillow EPS save) | `:235-295`, delegate at `:279-295` |
| `svg2bitmap()` | Core: calls `convert()` then Pillow `write()` | `:298-361` |

**Correction to the task brief:** it stated that `svg2pdf`, `svg2svg`, `svg2eps` and `svg2ps` all raise
`NotImplementedError`. The source shows only `svg2svg` and `svg2ps` do. `svg2pdf` and `svg2eps` route through
the raster `svg2bitmap` path and save with Pillow; Pillow documents write support for both PDF and EPS
([Pillow image-file-formats handbook](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html):
"The EPS driver can also write EPS images"). Either way the API is not a drop-in: two functions are missing and
two silently degrade from vector to raster output. The widget preview path only uses `svg2png`
(`hermes-plugin/hermes-widget/preview.py:210-212`), so the missing functions would not be exercised, but the "drop in replacement" claim
does not hold for the library as a whole.

### 4.2 How one render actually runs

Every `svg2png` call goes `svg2bitmap` → `convert` (`:345-349`); `convert` is (`:411-444`):

1. `with sync_playwright() as p:` — starts the Playwright driver.
2. `install(p.chromium)` (`:429`) — runs the `playwright install` CLI. `install-playwright` implements this as a
   `subprocess.run` of the driver's `install` command (read from
   `.tmp/nc-preflight/forensics/install_playwright-1.0.2-py3-none-any.whl`); output is captured and discarded,
   and `nocairosvg` ignores the boolean result. On a host without browsers this is the browser-download path;
   on every later render it re-runs the CLI check.
3. `p.chromium.launch(args=["--no-sandbox", "--disable-web-security", "--allow-file-access-from-files"])`
   (`:430-432`).
4. New page, viewport 4000x4000, `page.goto("file:///{THISDIR}/convert.html")` (`:433-435`).
5. `page.evaluate("convert(...)")` with the SVG file URL and background (`:436-438`).
6. `page.wait_for_selector("div")` (`:439`) — default 30,000 ms timeout
   ([Playwright docs, `page.wait_for_selector`](https://playwright.dev/python/docs/api/class-page): "Defaults to
   `30000` (30 seconds). Pass 0 to disable timeout").
7. Base64 data URL is read from `#div1`, stripped of its 22-character prefix, decoded, opened by Pillow
   (`:440-442`), then the browser closes (`:443`).

`convert.html` (`.tmp/nc-preflight/wheel/nocairosvg/convert.html`):

- `:2` — `<canvas id="canvas1" width="4000" height="4000">`;
- `:4-24` — `convert(width, height, backgroundColour, url)` loads the SVG as an `Image`, then draws it into the
  canvas, fills the background, and appends a `<div id="div1">` containing `canvas.toDataURL("image/png")`
  (`:10-21`, especially `:17-18`).

This is a browser screenshot pipeline, not an SVG rasteriser. The observed fidelity is therefore Chromium's
(or the host font stack's), not CairoSVG's.

### 4.3 Implications for a long-lived plugin HTTP server and preview endpoint

The plugin's HTTP surface is a long-lived threaded server: `hermes-plugin/hermes-widget/server.py:3` ("one
ThreadingHTTPServer, bearer auth"), `:27` (`from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer`),
`:176` (`class _Server(ThreadingHTTPServer)`). The preview endpoint is dispatched at `hermes-plugin/hermes-widget/server.py:491-492` and
handled by `_preview` (`hermes-plugin/hermes-widget/server.py:858-906`), which calls `preview.render_publication_previews(...)`
(`hermes-plugin/hermes-widget/server.py:883`). One request can render up to 16 preview sizes (`hermes-plugin/hermes-widget/preview.py:44`, `_PREVIEW_MAX_COUNT = 16`),
and each SVG preview hits the CairoSVG branch currently (`hermes-plugin/hermes-widget/preview.py:201-213`).

1. **Browser download at first render.** Playwright does not ship browser binaries in its wheel; its docs say the
   CLI must install them ([playwright.dev/python/docs/browsers](https://playwright.dev/python/docs/browsers)).
   Because `install(p.chromium)` runs inside `convert()`, the first preview request performs a large network
   download/verification inside the request path. On an offline or read-only preview host this fails; the
   `install()` result is ignored, so the failure surfaces later as a browser-launch exception.
2. **Per-render browser launch cost.** A fresh Chromium process per SVG (and up to 16 per request) is the
   dominant cost: process start, page load and base64 round-trip for a 4000x4000 canvas. This is a per-render
   cost a real SVG rasteriser does not have.
3. **Concurrency is unsafe.** `ThreadingHTTPServer` serves requests concurrently, but `convert()` writes the
   input to one fixed path, `THISDIR/temp.svg` (`:384`, `:387`), and `write()` writes the output to that same
   fixed path before reading it back (`:407-408`). Two simultaneous previews overwrite each other's temp file,
   so one response can decode the other request's image, or both can fail. There is no lock or unique temp name.
4. **Read-only site-packages breaks it.** `THISDIR` is the installed package directory (`:15`). Writing
   `temp.svg` there fails with `PermissionError` on read-only installs (container images, immutable base
   layers, some system/PEP 668 layouts). A preview renderer should only write to a caller-provided buffer.
5. **Requested output size is ignored.** `svg2bitmap` computes `new_width`/`new_height` and then calls
   `image.resize(...)` without assigning the result (`:351-354`). The `preview.py` call passes
   `output_width=...`/`output_height=...` (`hermes-plugin/hermes-widget/preview.py:210-212`), which the library discards. Output is
   whatever size the canvas produced, not the requested scaled size. (Pillow's `Image.resize` is not in-place;
   the return value must be rebound.) Separately, `ImageOps` is used at `:357` but only `Image` is imported at
   `:12`, so the `invert_images`/`negate_colors` branch would raise `NameError` — another sign the install path
   is untested.
6. **Host font dependence.** Text in the SVG is rendered by Chromium using host fonts. The project already
   avoids host-font dependence for its Pillow visuals (`hermes-plugin/hermes-widget/presentations.py:105`:
   "Pillow's bundled Aileron font is independent of host-installed fonts"). Browser rendering reintroduces
   exactly the dependency the preview path is trying to remove, and makes preview pixels vary by host.
7. **30 s hangs on malformed SVG.** `page.wait_for_selector("div")` has no explicit timeout, so an SVG that
   never loads (malformed XML, bad path, unsupported reference) blocks one request thread for the default
   30,000 ms before raising. With a thread pool serving previews, a few bad SVGs can tie up the server.
8. **Background colour coverage is partial.** `colour2tuple` only recognises a hard-coded dictionary of CSS
   colour names (`:447-642`); arbitrary hex values such as the theme colour `#FEF7FF` fall through to
   transparent (`:635-636`). `preview.py` itself does not pass `background_color` on the SVG branch, so this is
   a latent API gap rather than an immediate bug.
9. **Security posture changes.** The browser is launched with `--no-sandbox`, `--disable-web-security` and
   `--allow-file-access-from-files` (`:431`), so the SVG is parsed and executed by Chromium rather than by the
   project's XML parser. The plugin deliberately gates SVG publications through `defusedxml` plus explicit
   DOCTYPE/ENTITY/processing-instruction/namespace-prefix rejection
   (`hermes-plugin/hermes-widget/publication.py:21,555-569`; `requirements.txt:1`). The preview path then hands
   the same bytes to a browser with file access enabled, outside that gate's control. The preview endpoint
   returns the resulting PNG to callers, so a browser-parsed document is a larger attack surface than a
   CairoSVG parse. No exploit is claimed; this is a design-level concern to weigh.
10. **Windows bytestring path is suspect (source-level inference).** `svg2bitmap` converts only an explicit
    `url=` argument to a `file:///` URL (`:344`). For `bytestring=`/`file_obj=` input it writes `temp.svg` and
    returns the raw `f"{THISDIR}/temp.svg"` path (`:384-388`) with backslashes on Windows. That string is
    interpolated into `page.evaluate` (`:438`) as `img.src`. On POSIX the absolute path works relative to the
    `file://` page; on Windows a bare `C:\...` string is not a valid URL, so `img.onload` may never fire and
    the request waits the full 30 s. Not executed here; the Lead's experiment should settle it.

**Bottom line on §4:** NoCairoSVG is a browser-automation wrapper that happens to expose CairoSVG-shaped names.
It does not meet the preview endpoint's needs (deterministic size, concurrency safety, no write access to the
install directory, bounded latency) and it fails the "no libcairo" goal by replacing a small native library
with a browser distribution.

## 5. Alternative scan

Requirements for a replacement: SVG→PNG, no libcairo on the host, permissive licence (MIT-compatible,
no copyleft), a prebuilt cp311 `win_amd64` wheel, active maintenance, and no browser process per render.
Licences below were verified from PyPI `license`/`license_expression` plus classifiers and, where possible, the
GitHub licence API; versions/timestamps from the PyPI JSON endpoint of each project (all retrieved 2026-10-18,
raw copies in `.tmp/nc-preflight/forensics/`).

### 5.1 Comparison

| Package (latest) | Licence(s) | Last release | System lib needed? | cp311 win_amd64 wheel? | Maintenance signal | Code-copy status |
| --- | --- | --- | --- | --- | --- | --- |
| **`resvg-py` 0.5.0** | **MIT** wrapper (`resvg_py-0.5.0.dist-info/licenses/LICENSE`, "Copyright (C) 2024, baseplate-admin"); bundled Rust crates permissive: `resvg` 0.48.1 **Apache-2.0 OR MIT**, `tiny-skia` **BSD-3-Clause**, `usvg` **Apache-2.0 OR MIT**, `fontdb`/`harfrust`/`imagesize` **MIT**, `skrifa` MIT OR Apache-2.0 | **2026-08-24** | **No** — native code compiled into the wheel | **Yes**: `resvg_py-0.5.0-cp310-abi3-win_amd64.whl`, 1,242,277 B (abi3, runs on 3.11) | Active: 21 releases since 2024-04, repo pushed 2026-10-15, 27 stars, 8 open issues, MIT repo | **Allowed** (MIT/Apache-2.0/BSD-3; keep notices if redistributing the binary) |
| `skia-python` 144.0.post2 | **BSD-3-Clause** (GitHub `license.spdx_id`; PyPI classifier "BSD License"); Skia itself BSD-3-Clause | 2026-03-19 | **No** — Skia compiled into the wheel | **Yes**: `skia_python-144.0.post2-cp311-cp311-win_amd64.whl`, 10,892,933 B | Active: repo pushed 2026-10-15, 328 stars, 53 open issues; release cadence tracks Skia | **Allowed** (BSD-3) |
| `svglib` 2.3.0 (+ `reportlab` 5.0.1) | **LGPL-3.0-or-later** (`license_expression`; classifier LGPLv3; repo API `LGPL-3.0`); reportlab BSD | 2026-10-05 | No for the `svg2rlg` → `renderPM` raster path; the optional `bitmaps` extra (`rlpycairo`) *does* need libcairo, so do not use it | Yes, pure-Python wheels (`svglib-2.3.0-py3-none-any.whl` 50,602 B; `reportlab-5.0.1-py3-none-any.whl` 1,957,258 B) | Active: repo pushed 2026-10-15, 371 stars; frequent releases | **Pattern reference only — copyleft. Do not copy code.** LGPL dependency use is possible but adds a ReportLab drawing stack and is not MIT-compatible for vendoring. |
| `pyvips` 3.2.0 | **MIT** Python binding; bundled `libvips` via `pyvips-binary` 8.18.7 is **LGPL-3.0-or-later** | pyvips 2026-08-29; pyvips-binary 2026-09-28 | Not with `pyvips[binary]` (bundled libvips wheels, incl. `cp37-abi3-win_amd64`, 8,325,477 B); otherwise a system libvips | Yes via the `binary` extra; `pyvips` itself is sdist-only (`pyvips-3.2.0.tar.gz` 72,067 B) | Active: repo pushed 2026-10-17, 813 stars, 2 open issues | **Pattern reference only for `libvips` (LGPL).** Dynamic linking as a dependency is possible; do not copy libvips source into an MIT repo. |

Not tabulated because they do not solve the stated goal:

- **CairoSVG 2.9.1** (LGPL-3.0-or-later, 2026-09-07) is the status quo and needs libcairo — this is the
  dependency the pre-flight is trying to remove ([PyPI JSON](https://pypi.org/pypi/cairosvg/json)).
- **Direct Playwright screenshots** (Apache-2.0) reproduce the same browser-download/per-render-process cost as
  NoCairoSVG without the false "drop-in" promise; it is the same architecture, not an alternative to it.

### 5.2 Why `resvg-py` is the leading candidate

- **No system library and no browser.** `resvg`/`usvg`/`tiny-skia` are compiled into the wheel; there is no
  `libcairo`, no `librsvg`, no Chromium. The shared-object payload is the `.pyd` inside a 1.2 MB wheel.
- **Permissive throughout.** The wrapper is MIT; the bundled crates I checked are MIT / Apache-2.0 / BSD-3. The
  full 70-component SBOM is saved at `.tmp/nc-preflight/forensics/resvg_py_sbom.json`; I sampled the
  rendering-critical crates and found no copyleft, but the remaining crates were not individually audited.
- **Small, direct API that matches the preview path.** `resvg_py.svg_to_bytes(svg_string=..., svg_path=...,
  background=..., width=..., height=..., zoom=..., dpi=..., font_files=..., font_dirs=...) -> bytes`
  (type stub `.tmp/nc-preflight/forensics/resvg_py___init__.pyi`). The docs state it "returns **raw PNG bytes** —
  not base64" ([resvg-py usage docs](https://resvg-py.readthedocs.io/en/latest/usage.html)). `preview.py`
  currently passes `bytestring`, `output_width`, `output_height`; the same values map onto `svg_string`,
  `width`, `height`.
- **Active upstream.** 21 releases from 0.1.0 (2024-04-10) to 0.5.0 (2026-08-24); repository pushed
  2026-10-15; MIT; 27 stars; 8 open issues. Bounded risk for a dependency.
- **Deterministic fonts are possible.** Unlike Chromium, resvg takes `font_files`/`font_dirs` and
  `skip_system_fonts`, so the preview can pin the same font set on every host instead of inheriting host fonts.
- **The one thing not proven here:** visual fidelity vs CairoSVG on the repository's fixture SVGs. resvg is a
  static-SVG renderer, not a browser; complex CSS/filters/text features must be compared. That validation is
  `render-verifier`'s job, and the fallback path (`hermes-plugin/hermes-widget/preview.py:225-226`, `svg-renderer-unavailable`) already
  exists if the optional extra is absent.

## 6. Verdict and recommended path

### 6.1 Verdict

| Decision | Outcome |
| --- | --- |
| Adopt NoCairoSVG? | **No — REJECT** |
| Blocking condition | `Pillow (>=10.2.0,<11)` (`.tmp/nc-preflight/wheel/nocairosvg-2024.dist-info/METADATA:25`) ∩ `Pillow>=12.3.0,<13` (`hermes-plugin/hermes-widget/pyproject.toml:5`, `hermes-plugin/hermes-widget/requirements.txt:2`) = ∅. It cannot be added as a dependency. |
| Secondary disqualifiers (independent of the pin) | Browser install+launch per render; fixed `temp.svg` in `site-packages`; output size ignored; `svg2svg`/`svg2ps` unimplemented; 30 s hangs; host-font dependence; current `install-playwright` API mismatch; unmaintained since 2024. |
| Licence finding | MIT, permissive; licence is **not** the blocker. |
| Maintenance finding | Stale/abandoned by the task's criterion: last release 33.3 months ago, last commit 22.5 months ago. |

The "single blocking condition" is the dependency conflict because it is unconditional: no amount of
configuration on the Hermes-Blink side can satisfy both metadata declarations without patching or forking one
side. The architecture findings are listed separately because they mean the library would still be rejected
for this use case even if the pin were relaxed.

### 6.2 Recommended path

1. **Add `resvg-py` as an optional extra, not a hard dependency**, next to the existing CairoSVG optional
   preview backend (`hermes-plugin/hermes-widget/pyproject.toml:8`):
   `preview-svg = ["resvg-py>=0.5,<0.6"]`. Keep CairoSVG as a comparison backend while fidelity is being
   validated; make the default preview path prefer `resvg_py` when importable.
2. **Change `_fit_image`'s SVG branch** (`hermes-plugin/hermes-widget/preview.py:201-213`) from
   `cairosvg.svg2png(bytestring=..., output_width=..., output_height=...)` to
   `resvg_py.svg_to_bytes(svg_string=data.decode("utf-8"), width=output_width, height=output_height)`, then
   feed the returned PNG bytes to the existing `Image.open(...).convert("RGBA")` line (`hermes-plugin/hermes-widget/preview.py:213`). The
   existing `ImageOps.contain`/canvas composition (`hermes-plugin/hermes-widget/preview.py:219-223`) is unchanged.
3. **Keep the no-renderer fallback** (`hermes-plugin/hermes-widget/preview.py:204`, `:225-226`) and the `svg-renderer-unavailable`
   diagnostic; an optional extra that is absent must not break the preview endpoint.
4. **Pin fonts deterministically** using `font_files`/`font_dirs` (and optionally `skip_system_fonts=True`) so
   previews do not depend on host fonts — this is an improvement over both CairoSVG and NoCairoSVG.
5. **Validate fidelity before flipping the default:** run the fixture SVGs through resvg-py and CairoSVG and
   pixel-diff them (`render-verifier`). If resvg loses materially on the fixtures, `skia-python`'s `SVGDOM`
   (`skia.SVGDOM.MakeFromStream(...)` / `.render(canvas)`, BSD-3-Clause, real cp311 win_amd64 wheel) is the
   next libcairo-free candidate, at the cost of a heavier, lower-level API.
6. **Licence hygiene:** MIT/Apache-2.0/BSD-3 notices only. Do not copy code from `svglib` (LGPL-3.0-or-later),
   `cairosvg` (LGPL-3.0-or-later) or `libvips`/`pyvips-binary` (LGPL-3.0-or-later); use them as pattern
   references or (for LGPL, if ever needed) as dynamically linked dependencies, never as vendored source.
7. **Do not patch or vendor NoCairoSVG.** Forking it would mean owning an unmaintained Chromium wrapper,
   patching the `install-playwright` call, fixing the discarded `resize`, replacing the fixed temp file, and
   then still carrying a browser distribution in the preview host — strictly worse than a 1.2 MB native wheel.

## 7. Limitations and reproducibility

- **Nothing was executed.** This report did not import `nocairosvg` or `resvg_py`, did not install Chromium, and
  did not run any SVG renderer. Claims about runtime failure are labelled as source-level inference; the
  behavioural claims quoted from vendors (Playwright's 30,000 ms default, Playwright's browser-install
  requirement, Pillow's PDF/EPS write support, resvg-py's "raw PNG bytes" return) are cited above.
- **Rendered output was not compared.** The fidelity recommendation is a dependency-level judgement, not a
  pixel-diff result. The `render-verifier` teammate's experiment is authoritative for pixels.
- **Evidence is date-stamped.** Every URL was retrieved 2026-10-18; PyPI and GitHub metadata change. Raw
  responses are in `.tmp/nc-preflight/forensics/` with SHA-256 in
  [`EVIDENCE_INDEX.md`](../.tmp/nc-preflight/forensics/EVIDENCE_INDEX.md); `.tmp/` is git-ignored, so it is
  session evidence rather than a permanent record.
- **Wheel provenance:** the analysis used the Lead's download
  `.tmp/nc-preflight/dl/nocairosvg-2024-py3-none-any.whl`
  (`sha256 5828470932c49c3c7cc0dc0a36e4526b51d9d2c51655fb861c0547465929efa6`) and the extracted tree under
  `.tmp/nc-preflight/wheel/`. PyPI's recorded wheel size (9,407 B) matches the local file.
- **SBOM audit was sampled, not exhaustive.** `.tmp/nc-preflight/forensics/resvg_py_sbom.json` lists 70
  components; the rendering-critical crates and the wrapper were licence-checked, the rest were not.

## Appendix A — Primary source URLs used (all retrieved 2026-10-18)

- NoCairoSVG: [PyPI JSON](https://pypi.org/pypi/nocairosvg/json) · [GitHub repo](https://api.github.com/repos/FHPythonUtils/NoCairoSVG) ·
  [commits](https://api.github.com/repos/FHPythonUtils/NoCairoSVG/commits?per_page=20) ·
  [releases](https://api.github.com/repos/FHPythonUtils/NoCairoSVG/releases?per_page=10) ·
  [tags](https://api.github.com/repos/FHPythonUtils/NoCairoSVG/tags?per_page=20) ·
  [licence API](https://api.github.com/repos/FHPythonUtils/NoCairoSVG/license)
- Dependencies: [install-playwright](https://pypi.org/pypi/install-playwright/json) ·
  [playwright](https://pypi.org/pypi/playwright/json) · [Pillow](https://pypi.org/pypi/Pillow/json) ·
  [Playwright browsers docs](https://playwright.dev/python/docs/browsers) ·
  [page.wait_for_selector docs](https://playwright.dev/python/docs/api/class-page) ·
  [Pillow image formats](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)
- Alternatives: [resvg-py](https://pypi.org/pypi/resvg-py/json) ·
  [resvg-py usage](https://resvg-py.readthedocs.io/en/latest/usage.html) ·
  [resvg-py repo](https://api.github.com/repos/baseplate-admin/resvg-py) ·
  [resvg crate](https://crates.io/api/v1/crates/resvg) · [resvg repo](https://api.github.com/repos/linebender/resvg) ·
  [skia-python](https://pypi.org/pypi/skia-python/json) · [skia-python repo](https://api.github.com/repos/skia-python/skia-python) ·
  [skia-python SVGDOM test](https://raw.githubusercontent.com/skia-python/skia-python/main/tests/test_svgdom.py) ·
  [svglib](https://pypi.org/pypi/svglib/json) · [reportlab](https://pypi.org/pypi/reportlab/json) ·
  [svglib repo](https://api.github.com/repos/deeplook/svglib) ·
  [pyvips](https://pypi.org/pypi/pyvips/json) · [pyvips-binary](https://pypi.org/pypi/pyvips-binary/json) ·
  [pyvips repo](https://api.github.com/repos/libvips/pyvips) ·
  [CairoSVG](https://pypi.org/pypi/cairosvg/json)

## Appendix B — Local repository files cited

- `hermes-plugin/hermes-widget/pyproject.toml:5,8` — Pillow floor and CairoSVG optional extra.
- `hermes-plugin/hermes-widget/requirements.txt:1-2` — defusedxml and Pillow pins.
- `hermes-plugin/hermes-widget/preview.py:43-45,135-168,171-226,288-353` — preview limits, fallback PNG,
  CairoSVG import/caching, `_fit_image`, `render_publication_previews`.
- `hermes-plugin/hermes-widget/server.py:3,27,176,491-492,858-906` — threaded HTTP server and preview endpoint.
- `hermes-plugin/hermes-widget/presentations.py:105` — the project's deliberate host-font independence.
- `hermes-plugin/hermes-widget/publication.py:21,555-569` — defusedxml import and the SVG sanitisation gate
  (DOCTYPE/ENTITY/processing-instruction/namespace-prefix rejection).
- `.github/workflows/ci.yml:29,63,70,73,94,102` — Pillow/CairoSVG installs and Python versions.
- `LICENSE:1-3` — repository is MIT.

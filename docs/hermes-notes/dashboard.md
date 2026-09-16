Extending the Dashboard | Hermes Agent

Skip to main content

On this page
Extending the Dashboard

The Hermes web dashboard (hermes dashboard
) is built to be reskinned and extended without forking the codebase. Three layers are exposed:

Themes — YAML files that repaint the dashboard's palette, typography, layout, and per-component chrome. Drop a file in ~/.hermes/dashboard-themes/
; it appears in the theme switcher.

UI plugins — a directory with manifest.json
+ a JavaScript bundle that registers a tab, replaces a built-in page, augments one via page-scoped slots, or injects components into named shell slots.

Backend plugins — a Python file inside that plugin directory that exposes a FastAPI router
; routes are mounted under /api/plugins/<name>/
and called from the plugin's UI.

All three are drop-in at runtime: no repo clone, no npm run build
, no patching the dashboard source. This page is the canonical reference for all three.

If you just want to use the dashboard, see Web Dashboard. If you want to reskin the terminal CLI (not the web dashboard), see Skins & Themes — the CLI skin system is unrelated to dashboard themes.

Not the desktop app
This page covers the web dashboard (hermes dashboard
) plugin system — window.__HERMES_PLUGIN_SDK__
, a manifest.json
, and a pre-built JS bundle. The native desktop app (hermes desktop
) has its own, unrelated SDK — @hermes/plugin-sdk
, a single ESM file, no build step — documented at Desktop Plugin SDK. Only the backend plugin_api.py
namespace (/api/plugins/<name>
) is shared between them.

How the pieces compose
Themes and plugins are independent but synergistic. A theme can stand alone (just a YAML file). A plugin can stand alone (just a tab). Together they let you build a complete visual reskin with custom HUDs — the example strike-freedom-cockpit
demo (lives in the hermes-example-plugins
companion repo — see Combined theme + plugin demo for install steps) does exactly that.

Table of contents​

Themes

Quick start — your first theme

Palette, typography, layout

Layout variants

Theme assets (images as CSS vars)

Component chrome overrides

Color overrides

Raw customCSS

Built-in themes

Full theme YAML reference

Plugins

Quick start — your first plugin

Directory layout

Manifest reference

The Plugin SDK

Shell slots

Replacing built-in pages (tab.override
)

Augmenting built-in pages (page-scoped slots)

Slot-only plugins (tab.hidden
)

Backend API routes

Custom CSS per plugin

Plugin discovery & reload

Combined theme + plugin demo

API reference

Troubleshooting

Themes​

Themes are YAML files stored in ~/.hermes/dashboard-themes/
. The file name doesn't matter (the theme's name:
field is what the system uses), but convention is <name>.yaml
. Every field is optional — missing keys fall back to the built-in default
theme, so a theme can be as small as one color.

Quick start — your first theme​

mkdir -p ~/.hermes/dashboard-themes

# ~/.hermes/dashboard-themes/neon.yaml

name: neon

label: Neon

description: Pure magenta on black

palette:

background: "#000000"

midground: "#ff00ff"

Refresh the dashboard. Click the palette icon in the header and pick Neon. The background goes black, text and accents go magenta, and every derived color (card, border, muted, ring, etc.) is recomputed from that 2-color triplet via color-mix()
in CSS.

That's the whole onboarding: one file, two colors. Everything below is optional refinement.

Palette, typography, layout​

These three blocks are the heart of a theme. Each is independent — override one, leave the others.

Palette (3-layer)​

The palette is a triplet of color layers plus a warm-glow vignette color and a noise-grain multiplier. The dashboard's design-system cascade derives every shadcn-compatible token (card, popover, muted, border, primary, destructive, ring, etc.) from this triplet via CSS color-mix()
. Overriding three colors cascades into the whole UI.

KeyDescription
palette.background
Deepest canvas color — typically near-black. Drives the page background and card fill.
palette.midground
Primary text and accent. Most UI chrome reads this (foreground text, button outlines, focus rings).
palette.foreground
Top-layer highlight. The default theme sets this to white at alpha 0 (invisible); themes that want a bright accent on top can raise its alpha.
palette.warmGlow
rgba(...)
string used as the vignette color by <Backdrop />
.
palette.noiseOpacity
0–1.2 multiplier on the grain overlay. Lower = softer, higher = grittier.

Each layer accepts either {hex: "#RRGGBB", alpha: 0.0–1.0}
or a bare hex string (alpha defaults to 1.0).

palette:

background:

hex: "#05091a"

alpha: 1.0

midground: "#d8f0ff" # bare hex, alpha = 1.0

foreground:

hex: "#ffffff"

alpha: 0 # invisible top layer

warmGlow: "rgba(255, 199, 55, 0.24)"

noiseOpacity: 0.7

Typography​

KeyTypeDescription
fontSans
stringCSS font-family stack for body copy (applied to html
, body
).
fontMono
stringCSS font-family stack for code blocks, <code>
, .font-mono
utilities.
fontDisplay
stringOptional heading/display stack. Falls back to fontSans
.
fontUrl
stringOptional external stylesheet URL. Injected as <link rel="stylesheet">
in <head>
on theme switch. Same URL is never injected twice. Works with Google Fonts, Bunny Fonts, self-hosted @font-face
sheets — anything linkable.
baseSize
stringRoot font size — controls the rem scale. E.g. "14px"
, "16px"
.
lineHeight
stringDefault line-height. E.g. "1.5"
, "1.65"
.
letterSpacing
stringDefault letter-spacing. E.g. "0"
, "0.01em"
, "-0.01em"
.

typography:

fontSans: '"Orbitron", "Eurostile", "Impact", sans-serif'

fontMono: '"Share Tech Mono", ui-monospace, monospace'

fontDisplay: '"Orbitron", "Eurostile", sans-serif'

fontUrl: "https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;600;700&family=Share+Tech+Mono&display=swap"

baseSize: "14px"

lineHeight: "1.5"

letterSpacing: "0.04em"

Changing the font from the UI (no YAML)​

The theme picker in the dashboard header has a Font section below the
theme list. Pick any font there and it overrides the body font of whatever
theme is active — the choice is independent of the theme and persists across
theme switches (stored in config.yaml
under dashboard.font
). Choose
Theme default to clear the override and fall back to the active theme's
own fontSans
.

The picker offers a curated catalog (system stacks plus a set of Google-Fonts
families across sans / serif / mono). It deliberately does not accept a
free-text font URL — the font's stylesheet is injected as a <link>
, so the
catalog keeps the injected origins fixed. For a fully custom face, set
fontSans
+ fontUrl
in a theme YAML as shown above. The theme's fontMono

(code blocks, terminal) is always left untouched by the UI override.

Layout​

KeyValuesDescription
radius
any CSS length ("0"
, "0.25rem"
, "0.5rem"
, "1rem"
, ...)Corner-radius token. Maps to --radius
and cascades into --radius-sm/md/lg/xl
— every rounded element shifts together.
density
compact
| comfortable
| spacious
Spacing multiplier applied as the --spacing-mul
CSS var. compact = 0.85×
, comfortable = 1.0×
(default), spacious = 1.2×
. Scales Tailwind's base spacing, so padding, gap, and space-between utilities all shift proportionally.

layout:

radius: "0"

density: compact

Layout variants​

layoutVariant
picks the overall shell layout. Defaults to "standard"
when absent.

VariantBehaviour
standard
Single column, 1600px max-width (default).
cockpit
Left sidebar rail (260px) + main content. Populated by plugins via the sidebar
slot — see Shell slots. Without a plugin the rail shows a placeholder.
tiled
Drops the max-width clamp so pages can use the full viewport width.

layoutVariant: cockpit

The current variant is exposed as document.documentElement.dataset.layoutVariant
, so raw CSS in customCSS
can target it via :root[data-layout-variant="cockpit"] ...
.

Theme assets (images as CSS vars)​

Ship artwork URLs with a theme. Each named slot becomes a CSS var (--theme-asset-<name>
) that the built-in shell and any plugin can read. The bg
slot is automatically wired into the backdrop; other slots are plugin-facing.

assets:

bg: "https://example.com/hero-bg.jpg" # auto-wired into <Backdrop />

hero: "/my-images/strike-freedom.png" # for plugin sidebars

crest: "/my-images/crest.svg" # for header-left plugins

logo: "/my-images/logo.png"

sidebar: "/my-images/rail.png"

header: "/my-images/header-art.png"

custom:

scanLines: "/my-images/scanlines.png" # → --theme-asset-custom-scanLines

Values accept:

Bare URLs — wrapped in url(...)
automatically.

Pre-wrapped url(...)
, linear-gradient(...)
, radial-gradient(...)
expressions — used as-is.

"none"
— explicit opt-out.

Every asset is also emitted as --theme-asset-<name>-raw
(the unwrapped URL), in case a plugin needs to pass it to <img src>
instead of background-image
.

Plugins read these with plain CSS or JS:

// In a plugin slot

const hero = getComputedStyle(document.documentElement)

.getPropertyValue("--theme-asset-hero").trim();

Component chrome overrides​

componentStyles
restyles individual shell components without writing CSS selectors. Each bucket's entries become CSS vars (--component-<bucket>-<kebab-property>
) that the shell's shared components read. So card:
overrides apply to every <Card>
, header:
to the app bar, etc.

componentStyles:

card:

clipPath: "polygon(12px 0, 100% 0, 100% calc(100% - 12px), calc(100% - 12px) 100%, 0 100%, 0 12px)"

background: "linear-gradient(180deg, rgba(10, 22, 52, 0.85), rgba(5, 9, 26, 0.92))"

boxShadow: "inset 0 0 0 1px rgba(64, 200, 255, 0.28)"

header:

background: "linear-gradient(180deg, rgba(16, 32, 72, 0.95), rgba(5, 9, 26, 0.9))"

tab:

clipPath: "polygon(6px 0, 100% 0, calc(100% - 6px) 100%, 0 100%)"

sidebar: {}

backdrop: {}

footer: {}

progress: {}

badge: {}

page: {}

Supported buckets: card
, header
, footer
, sidebar
, tab
, progress
, badge
, backdrop
, page
.

Property names use camelCase (clipPath
) and are emitted as kebab (clip-path
). Values are plain CSS strings — anything CSS accepts (clip-path
, border-image
, background
, box-shadow
, animation
, ...).

Color overrides​

Most themes won't need this — the 3-layer palette derives every shadcn token. Use colorOverrides
when you want a specific accent the derivation won't produce (a softer destructive red for a pastel theme, a specific success green for a brand).

colorOverrides:

primary: "#ffce3a"

primaryForeground: "#05091a"

accent: "#3fd3ff"

ring: "#3fd3ff"

destructive: "#ff3a5e"

border: "rgba(64, 200, 255, 0.28)"

Supported keys: card
, cardForeground
, popover
, popoverForeground
, primary
, primaryForeground
, secondary
, secondaryForeground
, muted
, mutedForeground
, accent
, accentForeground
, destructive
, destructiveForeground
, success
, warning
, border
, input
, ring
.

Each key maps 1:1 to the --color-<kebab>
CSS var (e.g. primaryForeground
→ --color-primary-foreground
). Any key set here wins over the palette cascade for the active theme only — switching to another theme clears the overrides.

Raw customCSS
​

For selector-level chrome that componentStyles
can't express — pseudo-elements, animations, media queries, theme-scoped overrides — drop raw CSS into customCSS
:

customCSS: |

/* Scanline overlay — only visible when cockpit variant is active. */

:root[data-layout-variant="cockpit"] body::before {

content: "";

position: fixed;

inset: 0;

pointer-events: none;

z-index: 100;

background: repeating-linear-gradient(to bottom,

transparent 0px, transparent 2px,

rgba(64, 200, 255, 0.035) 3px, rgba(64, 200, 255, 0.035) 4px);

mix-blend-mode: screen;

}

The CSS is injected as a single scoped <style data-hermes-theme-css>
tag on theme apply and cleaned up on theme switch. Capped at 32 KiB per theme.

Built-in themes​

Each built-in ships its own palette, typography, and layout — switching produces visible changes beyond color alone.

ThemePaletteTypographyLayout
Hermes Teal (default
)Dark teal + creamSystem stack, 15px0.5rem radius, comfortable
Hermes Teal (Large) (default-large
)Same as defaultSystem stack, 18px, line-height 1.650.5rem radius, spacious
Midnight (midnight
)Deep blue-violetInter + JetBrains Mono, 14px0.75rem radius, comfortable
Ember (ember
)Warm crimson + bronzeSpectral (serif) + IBM Plex Mono, 15px0.25rem radius, comfortable
Mono (mono
)GrayscaleIBM Plex Sans + IBM Plex Mono, 13px0 radius, compact
Cyberpunk (cyberpunk
)Neon green on blackShare Tech Mono everywhere, 14px0 radius, compact
Rosé (rose
)Pink + ivoryFraunces (serif) + DM Mono, 16px1rem radius, spacious

Themes that reference Google Fonts (all except Hermes Teal) load the stylesheet on demand — the first time you switch to them a <link>
tag is injected into <head>
.

Full theme YAML reference​

Every knob in one file — copy and trim what you don't need:

# ~/.hermes/dashboard-themes/ocean.yaml

name: ocean

label: Ocean Deep

description: Deep sea blues with coral accents

# 3-layer palette (accepts {hex, alpha} or bare hex)

palette:

background:

hex: "#0a1628"

alpha: 1.0

midground:

hex: "#a8d0ff"

alpha: 1.0

foreground:

hex: "#ffffff"

alpha: 0.0

warmGlow: "rgba(255, 107, 107, 0.35)"

noiseOpacity: 0.7

typography:

fontSans: "Poppins, system-ui, sans-serif"

fontMono: "Fira Code, ui-monospace, monospace"

fontDisplay: "Poppins, system-ui, sans-serif" # optional

fontUrl: "https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Fira+Code:wght@400;500&display=swap"

baseSize: "15px"

lineHeight: "1.6"

letterSpacing: "-0.003em"

layout:

radius: "0.75rem"

density: comfortable

layoutVariant: standard # standard | cockpit | tiled

assets:

bg: "https://example.com/ocean-bg.jpg"

hero: "/my-images/kraken.png"

crest: "/my-images/anchor.svg"

logo: "/my-images/logo.png"

custom:

pattern: "/my-images/waves.svg"

componentStyles:

card:

boxShadow: "inset 0 0 0 1px rgba(168, 208, 255, 0.18)"

header:

background: "linear-gradient(180deg, rgba(10, 22, 40, 0.95), rgba(5, 9, 26, 0.9))"

colorOverrides:

destructive: "#ff6b6b"

ring: "#ff6b6b"

customCSS: |

/* Any additional selector-level tweaks */

Refresh the dashboard after creating the file. Switch themes live from the header bar — click the palette icon. Selection persists to config.yaml
under dashboard.theme
and is restored on reload.

Plugins​

A dashboard plugin is a directory with a manifest.json
, a pre-built JS bundle, and optionally a CSS file and a Python file with FastAPI routes. Plugins live next to other Hermes plugins in ~/.hermes/plugins/<name>/
— the dashboard extension is a dashboard/
subfolder inside that plugin directory, so one plugin can extend both the CLI/gateway and the dashboard from a single install.

Plugins don't bundle React or UI components. They use the Plugin SDK exposed on window.__HERMES_PLUGIN_SDK__
. This keeps plugin bundles tiny (typically a few KB) and avoids version conflicts.

Quick start — your first plugin​

Create the directory structure:

mkdir -p ~/.hermes/plugins/my-plugin/dashboard/dist

Write the manifest:

// ~/.hermes/plugins/my-plugin/dashboard/manifest.json

{

"name": "my-plugin",

"label": "My Plugin",

"icon": "Sparkles",

"version": "1.0.0",

"tab": {

"path": "/my-plugin",

"position": "after:skills"

},

"entry": "dist/index.js"

}

Write the JS bundle (a plain IIFE — no build step needed):

// ~/.hermes/plugins/my-plugin/dashboard/dist/index.js

(function () {

"use strict";

const SDK = window.__HERMES_PLUGIN_SDK__;

const { React } = SDK;

const { Card, CardHeader, CardTitle, CardContent } = SDK.components;

function MyPage() {

return React.createElement(Card, null,

React.createElement(CardHeader<span class="token punctuation" style="color:rgb(248, 248, 242)"
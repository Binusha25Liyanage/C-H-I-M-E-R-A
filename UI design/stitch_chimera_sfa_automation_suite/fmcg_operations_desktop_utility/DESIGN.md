---
name: FMCG Operations Desktop Utility
colors:
  surface: '#f8f9fb'
  surface-dim: '#d9dadc'
  surface-bright: '#f8f9fb'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f3f4f6'
  surface-container: '#edeef0'
  surface-container-high: '#e7e8ea'
  surface-container-highest: '#e1e2e4'
  on-surface: '#191c1e'
  on-surface-variant: '#5b403d'
  inverse-surface: '#2e3132'
  inverse-on-surface: '#f0f1f3'
  outline: '#906f6c'
  outline-variant: '#e4beb9'
  surface-tint: '#bb181e'
  primary: '#8c000e'
  on-primary: '#ffffff'
  primary-container: '#b5121b'
  on-primary-container: '#ffc5bf'
  inverse-primary: '#ffb3ac'
  secondary: '#5f5e60'
  on-secondary: '#ffffff'
  secondary-container: '#e2dfe1'
  on-secondary-container: '#636264'
  tertiary: '#004e33'
  on-tertiary: '#ffffff'
  tertiary-container: '#006846'
  on-tertiary-container: '#5debaf'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffdad6'
  primary-fixed-dim: '#ffb3ac'
  on-primary-fixed: '#410003'
  on-primary-fixed-variant: '#93000f'
  secondary-fixed: '#e4e2e4'
  secondary-fixed-dim: '#c8c6c8'
  on-secondary-fixed: '#1b1b1d'
  on-secondary-fixed-variant: '#474649'
  tertiary-fixed: '#6ffbbe'
  tertiary-fixed-dim: '#4edea3'
  on-tertiary-fixed: '#002113'
  on-tertiary-fixed-variant: '#005236'
  background: '#f8f9fb'
  on-background: '#191c1e'
  surface-variant: '#e1e2e4'
typography:
  headline-xl:
    fontFamily: IBM Plex Sans
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-lg:
    fontFamily: IBM Plex Sans
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  headline-md:
    fontFamily: IBM Plex Sans
    fontSize: 15px
    fontWeight: '600'
    lineHeight: 20px
  body-lg:
    fontFamily: IBM Plex Sans
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-md:
    fontFamily: IBM Plex Sans
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  body-sm:
    fontFamily: IBM Plex Sans
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  label-md:
    fontFamily: IBM Plex Sans
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.02em
  label-sm:
    fontFamily: IBM Plex Sans
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.03em
  mono-lg:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
  mono-md:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  mono-sm:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 14px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 0.75rem
  margin: 1rem
  space-xs: 0.25rem
  space-sm: 0.375rem
  space-md: 0.625rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

The design system is engineered for mission-critical IT automation, data warehousing pipelines, and FMCG Sales Operations control centers. The core ethos is industrial precision, absolute reliability, and zero-distraction utility. 

The interface evokes the mechanical certainty of an operations console: crisp contrast, dense data presentation, immediate operational status indicators, and clear diagnostic telemetry. It balances dense tabular workspaces with high-visibility feedback loops, allowing operators to monitor multi-threaded report scraping, extraction tasks, and ETL jobs across hundreds of regional distributor instances without cognitive strain. Decorative flourishes and illustrative elements are strictly rejected in favor of functional density, micro-badges, mono-spaced audit logs, and clear structural segmentation.

## Colors

The system uses an intentional dual-environment model: an ultra-crisp, light-canvas operational interface for high-density tables and forms, paired with a permanent dark-mode telemetry viewport for execution logs and terminal output.

- **Primary (`#B5121B`)**: Cherry Red indicates authoritative action, active automation routines, primary trigger states, and focused navigation anchors.
- **Secondary (`#1C1C1E`)**: Dark Charcoal anchors persistent vertical navigation rails, app frame headers, high-emphasis text, and active icon glyphs.
- **Canvas Base (`#F4F5F7`)**: Calibrated desktop neutral providing low eye fatigue during extended monitor shifts.
- **Surfaces (`#FFFFFF`)**: Pure white data containers delineated by fine borders (`#E5E7EB`).
- **Secondary Text (`#6E6F71`)**: Ash grey for field captions, secondary table attributes, column metadata, and timestamps.
- **Telemetry Shell (`#18181B` to `#0F172A`)**: High-contrast, dark slate console base hosting real-time terminal output, execution timestamps, and scraper traces. Text renders in `#E2E8F0` with semantic token highlights.

### Functional Status System
Statuses map strictly to standardized foreground and background pairs:
- **Success / Loaded**: Emerald `#10B981` text over `#DCFCE7` tint.
- **Failure / Missing**: Red `#EF4444` text over `#FEE2E2` tint.
- **Warning / Running / Retrying**: Amber `#F59E0B` text over `#FEF3C7` tint.
- **Disabled / Queued**: Slate `#6B7280` text over `#F3F4F6` tint.

## Typography

Typography prioritizes tabular clarity and structural efficiency. Proportional fonts use structured geometric sans weights with crisp rendering on low-DPI and high-DPI Windows displays alike. Numeric figures must strictly render with tabular lining enabled (`font-variant-numeric: tabular-nums`) to guarantee alignment down high-volume reporting grids.

Monospace typography is reserved for telemetry traces, raw file paths, automated query parameters, HTTP response codes, and database connection strings. Line heights are intentionally tight (1.25x to 1.38x) to maximize vertical information density without compromising scan speed.

## Layout & Spacing

The application implements a fixed Windows desktop workspace model designed for standard multi-monitor operations (1080p, 1440p, 4K displays):

- **Shell Architecture**: Persistent 64px collapsed / 220px expanded dark sidebar (`#1C1C1E`) on the extreme left, coupled with a fixed 40px top status control bar.
- **Working Area**: Divided into a flexible multi-panel dashboard containing control ribbons, tabular data views, and a collapsible bottom drawer console for live scrape stdout/stderr streams.
- **Grid Density**: Uses a compact 4px base increment. Data table rows are fixed at 32px height (dense mode) or 40px height (standard mode), maximizing row visibility above the fold.
- **Adaptive Canvas**: Content panels resize via split-pane dividers rather than responsive stacking. Breakpoint logic serves window scaling rather than mobile reflows, prioritizing horizontal data tables down to an absolute min-window constraint of 1024×700px.

## Elevation & Depth

Visual separation relies on structural borders and hard tonal boundaries rather than aggressive elevation:

- **Surface Level 0 (Canvas)**: Background `#F4F5F7`. Flat, zero elevation.
- **Surface Level 1 (Work Cards & Grids)**: `#FFFFFF` surfaced with a crisp 1px perimeter border (`#E5E7EB`) and an ambient micro-shadow: `0 1px 2px 0 rgba(0, 0, 0, 0.04)`.
- **Surface Level 2 (Flyouts, Menus & Tooltips)**: `#FFFFFF` elevated with `0 4px 12px 0 rgba(0, 0, 0, 0.08)` and border `#D1D5DB`.
- **Surface Level 3 (Modal Execution Dialogs)**: `#FFFFFF` centered over a 40% `#1C1C1E` tinted backdrop, with `0 12px 32px 0 rgba(0, 0, 0, 0.16)`.
- **Console Telemetry Well**: Inset rendering inside Level 1 cards using a 1px border (`#27272A`) and `#18181B` surface to mimic embedded terminal hardware.

## Shapes

The geometric framework balances contemporary software polish with utilitarian structure. Container cards and primary functional panels enforce an exact 10px corner radius (`rounded-[10px]`), providing a refined industrial finish without wasting peripheral canvas space.

Internal utility controls adopt tighter geometry:
- Form fields, dropdown selectors, and default button controls utilize a disciplined 4px radius.
- System status tags, extraction micro-badges, and toggle switches use a fully rounded capsule shape (`9999px`) to immediately signal state indicators versus interactive rectangular surfaces.

## Components

### Buttons & Action Strips
- **Primary Action (Run Extraction / Save)**: Solid Cherry Red (`#B5121B`) background, `#FFFFFF` text, 4px corner radius, bold tabular text, hover `#990F17`, active `#7F0D13`. 32px height for compact utility.
- **Secondary Action (Configure / Export)**: `#FFFFFF` background, 1px border (`#D1D5DB`), text `#1C1C1E`, hover `#F3F4F6`.
- **Destructive Action (Abort Pipeline)**: `#EF4444` background with pure white text.
- **Icon Utility Actions**: 28×28px square buttons with transparent background, hover `#E5E7EB`, 4px radius.

### Status Micro-Badges & Chips
- Status chips measure 20px in height with 8px horizontal padding and 9999px radius.
- Font styling is set strictly to `label-sm` with letter spacing.
- Includes a 6px circular solid pip alongside the status label for double-encoding state (e.g., `#10B981` dot on `#DCFCE7` pill).

### Input Fields & Selectors
- Height is fixed at 32px with 8px internal horizontal padding.
- 1px border `#D1D5DB`, background `#FFFFFF`, text `#1C1C1E`, 4px radius.
- Focus state invokes a crisp 1px outline in `#B5121B` with no oversized glow rings.
- Inline parameter inputs (such as date range selectors, branch codes, batch counters) display monospace text (`mono-md`).

### Data Tables
- Header row: 32px height, background `#F9FAFB`, text `#6E6F71`, uppercase `label-sm`, 1px border-bottom `#E5E7EB`.
- Data rows: 32px or 36px height, alternating subtle zebra stripe option (`#FFFFFF` to `#FAFAFA`), 1px border-bottom `#F3F4F6`.
- Hover row state triggers a solid `#F0FDF4` (for validated sets) or neutral `#F3F4F6` background.
- Numeric columns strictly align right with `font-variant-numeric: tabular-nums`.

### Terminal Log Viewer
- Embedded panel using `#18181B` background, 8px padding, 10px outer card radius.
- Monospace output (`mono-sm`) with line numbers in `#52525B`.
- Syntax highlighting: info in `#38BDF8`, warnings in `#F59E0B`, errors in `#EF4444`, and success traces in `#10B981`.

### Pill Toggles & Micro Switches
- 32×18px track size. Unchecked background `#D1D5DB`, checked background `#B5121B`.
- 14px white circle slider with smooth 150ms linear transition.
---
name: Chimera Industrial OS
colors:
  surface: '#f9f9fc'
  surface-dim: '#dadadc'
  surface-bright: '#f9f9fc'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f3f3f6'
  surface-container: '#eeeef0'
  surface-container-high: '#e8e8ea'
  surface-container-highest: '#e2e2e5'
  on-surface: '#1a1c1e'
  on-surface-variant: '#5b403d'
  inverse-surface: '#2f3133'
  inverse-on-surface: '#f0f0f3'
  outline: '#906f6c'
  outline-variant: '#e4beb9'
  surface-tint: '#bb181e'
  primary: '#8c000e'
  on-primary: '#ffffff'
  primary-container: '#b5121b'
  on-primary-container: '#ffc5bf'
  inverse-primary: '#ffb3ac'
  secondary: '#5d5e60'
  on-secondary: '#ffffff'
  secondary-container: '#e2e2e4'
  on-secondary-container: '#636466'
  tertiary: '#00457a'
  on-tertiary: '#ffffff'
  tertiary-container: '#005da2'
  on-tertiary-container: '#b8d5ff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffdad6'
  primary-fixed-dim: '#ffb3ac'
  on-primary-fixed: '#410003'
  on-primary-fixed-variant: '#93000f'
  secondary-fixed: '#e2e2e4'
  secondary-fixed-dim: '#c6c6c8'
  on-secondary-fixed: '#1a1c1d'
  on-secondary-fixed-variant: '#454749'
  tertiary-fixed: '#d2e4ff'
  tertiary-fixed-dim: '#a1c9ff'
  on-tertiary-fixed: '#001c37'
  on-tertiary-fixed-variant: '#004880'
  background: '#f9f9fc'
  on-background: '#1a1c1e'
  surface-variant: '#e2e2e5'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  title-sm:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  data-mono:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 16px
  label-caps:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  sidebar-width: 260px
  container-gap: 24px
  section-margin: 32px
  cell-padding-v: 12px
  cell-padding-h: 16px
  stack-gap: 8px
---

## Brand & Style
The design system is engineered for high-stakes industrial export management. It prioritizes precision, confidence, and visual weight to reflect the scale of Lakmee Exports Lanka’s operations.

The aesthetic follows a **Corporate / Modern** direction with a focus on **Information Density**. Unlike consumer applications, this system embraces a structured, "cockpit-like" environment. It utilizes high-contrast elements and a rigid grid to ensure that critical data—shipping logs, inventory metrics, and automation controls—is processed without ambiguity. The interface feels substantial and authoritative, moving away from sterile minimalism toward a functional, durable digital toolset.

## Colors
The palette is anchored by **Cherry Red**, used strategically for primary actions, critical status indicators, and branding accents. It is a color of "active authority." **Ash Grey** serves as the functional backbone, providing structure through borders, secondary text, and inactive states.

*   **Primary (#B5121B):** Reserved for high-priority interactions and safety-critical alerts.
*   **Secondary (#6E6F71):** Used for metadata, iconography, and architectural borders.
*   **Surface & Neutrals:** The background is a cool, surgical grey-white (#F8FAFC) to reduce eye strain during long shifts, while deep neutrals (#1A1C1E) are used for headers and the persistent sidebar to anchor the layout.

## Typography
The system utilizes **Inter** for its exceptional legibility in data-dense environments. For technical values, SKU numbers, and timestamps, **JetBrains Mono** is introduced to provide clear character differentiation (e.g., distinguishing '0' from 'O').

*   **Headlines:** Use tight letter spacing and bold weights to communicate importance.
*   **Data Display:** Use the `data-mono` style for all numerical tables to ensure column alignment and readability.
*   **Labels:** Use `label-caps` for table headers and section overviews to create a clear visual hierarchy between the "frame" and the "content."

## Layout & Spacing
This design system employs a **Fixed Sidebar / Fluid Content** model. The sidebar remains persistent to provide immediate access to global modules (Logistics, Warehouse, Safety, Reports).

*   **The Grid:** Content is housed in a 12-column responsive grid within the main viewport.
*   **Data Density:** Spacing is compact but deliberate. A 4px baseline shift is used for tight stacking, while 24px-32px gaps separate major functional blocks (e.g., separating Stat Cards from the main Data Table).
*   **Desktop Focus:** As an internal tool, the layout prioritizes desktop real estate. On smaller screens (Tablets), the sidebar collapses into an icon-only rail to preserve workspace for data tables.

## Elevation & Depth
The system avoids floating elements or heavy drop shadows in favor of **Tonal Layering** and **Crisp Outlines**.

*   **Canvas:** The base layer is #F8FAFC.
*   **Cards:** Elevated on a white (#FFFFFF) surface with a subtle 1px border (#E2E8F0).
*   **Interactive Depth:** Only "Actionable" elements like buttons or active dropdowns receive a slight 2px soft shadow to indicate "pressability."
*   **Sidebar:** Treated as a recessed or high-contrast dark plane (#1A1C1E) to clearly separate navigation from the work area.

## Shapes
To maintain a professional and "industrial" feel, the design system uses a **Soft (0.25rem)** roundedness. 

*   **Standard Components:** Buttons, inputs, and cards use a 4px radius. This is enough to feel modern without losing the geometric rigor required for a business tool.
*   **Status Indicators:** Small "Data Freshness" dots or "Safety Toggles" may use a fully rounded (pill) shape to distinguish them as status-only or binary-state elements.

## Components

### Stat Cards
High-contrast containers featuring a 4px left-accent border in **Cherry Red**. These display "The Number"—bold, large, and monochrome—accompanied by a small "Trend Indicator" (percentage change) and a "Data Freshness" timestamp in the bottom right corner.

### Professional Data Tables
*   **Zebra Striping:** Even rows use a subtle tint (#F1F5F9).
*   **Hover State:** Row highlight using a light red tint (#FEF2F2).
*   **Headers:** Sticky headers with `label-caps` typography and a solid 2px bottom border in Ash Grey.

### Safety-Critical Controls
Automation toggles are larger than standard UI switches. The "Active" state for safety overrides uses the **Cherry Red** color prominently, often accompanied by a confirmation modal to prevent accidental triggers.

### Inputs & Fields
Inputs use a white background with a 1px Grey border. On focus, the border shifts to Cherry Red with a subtle 2px outer glow (ring) to ensure the user knows exactly where the cursor is placed.

### Data Freshness Indicators
A small, pulsating dot (Green for "Live," Amber for "Delayed > 5m," Red for "Offline") positioned near the page title or within specific data widgets to ensure users are never acting on stale information.
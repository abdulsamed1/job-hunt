---
version: 1.0.0
name: Autonomous Job Hunt Command Center Design System
description: "A dark-mode-first mission-control interface built for high-throughput autonomous job hunting. Rooted in the precision aesthetics of Linear and Vercel, this system uses an ultra-deep void canvas (#07080a), layered slate surfaces (#0e1117, #131821), hairline borders with subtle luminosity (#1f2633), and high-contrast semantic accents: emerald (#10b981) for verified success, violet (#6366f1) for AI operations, amber (#f59e0b) for pending reviews, and crimson (#ef4444) for rejections. Typography pairs Geist / Inter with JetBrains Mono for metrics and ATS payloads."

colors:
  canvas: "#07080a"
  surface-0: "#0b0e14"
  surface-1: "#0e1117"
  surface-2: "#131821"
  surface-3: "#19202c"
  surface-hover: "#1e2634"
  hairline: "#1f2633"
  hairline-strong: "#2c3649"
  hairline-focus: "#6366f1"
  text-primary: "#f8fafc"
  text-secondary: "#94a3b8"
  text-muted: "#64748b"
  text-subtle: "#475569"
  accent-primary: "#6366f1"
  accent-primary-hover: "#4f46e5"
  accent-primary-subtle: "rgba(99, 102, 241, 0.12)"
  semantic-success: "#10b981"
  semantic-success-subtle: "rgba(16, 185, 129, 0.12)"
  semantic-warning: "#f59e0b"
  semantic-warning-subtle: "rgba(245, 158, 11, 0.12)"
  semantic-danger: "#ef4444"
  semantic-danger-subtle: "rgba(239, 68, 68, 0.12)"
  semantic-info: "#38bdf8"
  semantic-info-subtle: "rgba(56, 189, 248, 0.12)"
  badge-linkedin: "#0a66c2"
  badge-greenhouse: "#22c55e"
  badge-ashby: "#a855f7"
  badge-lever: "#0ea5e9"
  badge-workable: "#007a87"
  badge-bamboohr: "#73b53a"

typography:
  font-sans: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
  font-mono: "'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace"
  scales:
    display:
      size: "32px"
      lineHeight: "1.15"
      weight: 700
      letterSpacing: "-0.03em"
    headline:
      size: "24px"
      lineHeight: "1.2"
      weight: 600
      letterSpacing: "-0.02em"
    title:
      size: "18px"
      lineHeight: "1.3"
      weight: 600
      letterSpacing: "-0.01em"
    body-lg:
      size: "15px"
      lineHeight: "1.5"
      weight: 400
      letterSpacing: "-0.005em"
    body:
      size: "13.5px"
      lineHeight: "1.5"
      weight: 400
      letterSpacing: "0em"
    body-sm:
      size: "12px"
      lineHeight: "1.4"
      weight: 400
      letterSpacing: "0.01em"
    mono-code:
      size: "12px"
      lineHeight: "1.6"
      weight: 500
      letterSpacing: "0.02em"
    caption:
      size: "10.5px"
      lineHeight: "1.3"
      weight: 600
      letterSpacing: "0.08em"
      textTransform: "uppercase"

radii:
  sm: "4px"
  md: "6px"
  lg: "10px"
  xl: "14px"
  full: "9999px"

shadows:
  glow-accent: "0 0 20px -3px rgba(99, 102, 241, 0.25)"
  glow-success: "0 0 16px -2px rgba(16, 185, 129, 0.2)"
  card: "0 4px 20px -2px rgba(0, 0, 0, 0.5), 0 0 0 1px #1f2633"
  modal: "0 20px 40px -8px rgba(0, 0, 0, 0.8), 0 0 0 1px #2c3649"
---

# Autonomous Job Hunt Agent — DESIGN.md

An actionable Design System specification following the **getdesign.md** & **Google Stitch** design standards.

---

## 1. Aesthetic Direction & Principles

1. **Mission-Control Density**:
   - Provide software engineers and autonomous operators with dense, actionable information without visual noise.
   - Every metric card, log entry, and badge has high contrast and instant clarity.

2. **Dark-Mode-First Precision**:
   - Canvas stays at `#07080a` (void black) to eliminate eye fatigue during 24/7 continuous operation.
   - Subtle surface elevation layering (`#0b0e14` → `#0e1117` → `#131821`) with 1px hairline borders (`#1f2633`) replaces heavy shadows.

3. **Verifiable Auditability**:
   - Applications and AI match scores must be immediately inspectable:
     - Real-time Playwright screenshot proof modal.
     - FreeLLMAPI reasoning drawer with matched vs. missing skills breakdown.
     - Tailored CV preview with ATS keyword injection proof.

4. **Keyboard & Action Agility**:
   - One-click trigger buttons for Scan, Evaluate, Tailor, Apply, and Full Cycle execution.
   - Instant search and faceted filtering with zero page reload.

---

## 2. Color Palette & Token Usage

| Token Name | Hex / Value | Semantic Role |
|------------|-------------|---------------|
| `canvas` | `#07080a` | Background root canvas |
| `surface-0` | `#0b0e14` | Sub-canvas, table header, sticky nav |
| `surface-1` | `#0e1117` | Card base, filter bars, drawer panels |
| `surface-2` | `#131821` | Active items, hovered rows, nested cards |
| `surface-3` | `#19202c` | Input backgrounds, code block containers |
| `hairline` | `#1f2633` | 1px dividers, card borders, table separators |
| `hairline-strong` | `#2c3649` | Active borders, modal outlines |
| `accent-primary` | `#6366f1` | Primary actions, selected tabs, AI highlights |
| `semantic-success`| `#10b981` | SUBMITTED, 500+ daily target achieved, live daemon |
| `semantic-warning`| `#f59e0b` | ELIGIBLE, pending reviews, retry pending |
| `semantic-danger` | `#ef4444` | REJECTED, PRE_FILTERED_OUT, FAILED |
| `semantic-info`   | `#38bdf8` | DISCOVERED, TAILORED, fresh postings |

---

## 3. Typography & Hierarchy

- **UI Sans**: `Inter`, `-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, `Roboto`
  - Crisp, legible at small sizes, optimal rendering across Linux and macOS.
- **Code & Numbers**: `JetBrains Mono`, `SF Mono`, `monospace`
  - Used for job IDs, timestamps, percentage scores, ATS keyword dumps, and raw logs.

---

## 4. Components & Layout Blueprints

### 4.1 Bento Metrics Grid
- Top-level dashboard cards showing pipeline metrics:
  - **Daily Velocity**: `2,156 / 500` jobs with visual percentage progress bar.
  - **Eligible Matches**: Jobs scored `>= 70%` by FreeLLMAPI.
  - **Tailored CVs**: Generated and stealth ATS keyword-verified.
  - **Live Submissions**: Actual applications delivered with proof screenshots.
  - **Daemon Liveness**: Pulsing emerald indicator with uptime and active loop status.

### 4.2 Interactive Jobs Data Table
- Sticky headers on `#0b0e14` surface.
- Alternating subtle row backgrounds with `#131821` on hover.
- Pill badges for sources (`LinkedIn`, `Greenhouse`, `Ashby`, `Lever`, `SmartRecruiters`, `Workday`, `Workable`, `BambooHR`).
- Score badge:
  - `>= 85%`: Vivid emerald pill with glow.
  - `70-84%`: Amber pill.
  - `< 70%`: Muted zinc pill.
- Direct row click opens the **Job Inspection Drawer**.

### 4.3 Job Inspection Drawer (Flyout / Modal)
- Slide-over or central modal with tabs:
  1. **Match & AI Analysis**:
     - Match percentage gauge.
     - FreeLLMAPI reasoning paragraph.
     - Green chips for matched skills; red chips for missing skills.
     - Full job posting description formatted in clean prose.
  2. **Tailored CV & ATS Stealth**:
     - Candidate PDF download link.
     - ATS keywords injection verification report.
     - Tailored markdown summary.
  3. **Application & Verification Proof**:
     - Playwright browser submission screenshot viewer with zoom modal.
     - Confirmation message & timestamp.
     - Complete immutable audit log trail.

### 4.4 Live Console Drawer
- Bottom or side collapsible terminal styled after Warp / VSCode dark terminal.
- JetBrains Mono font, streaming recent logs from `data/job_hunt.log`.
- Auto-scroll lock with pause toggle.

---

## 5. Interaction Patterns & States

- **Loading states**: Skeleton pulses in `rgba(255, 255, 255, 0.05)`.
- **Button clicks**: Micro-interaction scale `98%` with 150ms ease-out.
- **Pills & Badges**: 1px subtle border with 12% alpha background fill.
- **Toast Notifications**: Slide in from top-right corner with 3s auto-dismiss for action triggers.

# The DevX Doctrine

**A design system for everything we make.**
*Version 1.0 · April 2026*

---

## What this is

The DevX Doctrine is the design language for everything DevX Labs produces externally and internally — executive decks, the marketing site, internal docs, memos, PRDs. It exists so our work looks like it came from one firm with one point of view, not five contractors with five tastes.

It is principles + tokens. The principles tell you what we believe about design; the tokens tell you what to actually type. Anywhere a token would suffocate good judgement, the doctrine says "here's the rule, here's when you can bend it." Surface-specific guidance is at the end.

If you're using this with an LLM (Claude, Cursor, v0, etc.), skip to the **Prompt Block** at the bottom — it's a self-contained paste.

---

## 1. Brand essence (one paragraph)

We design like a top-tier consulting firm that ships code. Our work has the editorial restraint of McKinsey and Bloomberg, the typographic discipline of Stripe and Linear, and none of the visual tropes of an AI startup. We use a lot of white space, one accent color, and let typography do the work that other firms ask of color, gradients, and decoration. When in doubt: less.

---

## 2. The five principles

### 2.1 Editorial restraint over decorative excitement
Our default response to any visual problem is to remove things, not add them. A page with one strong headline, one rule, and one accent feels more confident than a page with five gradients, four illustrations, and three call-to-action buttons. We trust the reader.

### 2.2 Structural grid discipline
Every layout sits on a 12-column grid with generous margins. Hairline rules (1px, neutral gray) divide content instead of boxes and shadows. Stat rows have vertical rules. Section breaks have horizontal rules. The grid is visible, not hidden.

### 2.3 Typography is the primary visual device
We use type to communicate hierarchy, mood, and emphasis — not color, not size-as-volume, not decoration. A display sans for headlines, an italic serif for editorial emphasis, a monospaced face for labels and numerics. The interplay between these three voices is what makes our work look like ours.

### 2.4 One accent, used sparingly
A single blue (`#1E6FFF`) is the only color allowed besides ink-on-paper. It marks emphasis — a key word in italic, a stat callout, an active state, a single rule under a headline. If everything is blue, nothing is. The discipline is to use it 2–4 times per surface, never more.

### 2.5 Monoline, hand-drawn iconography
All icons are 1.25px stroke, no fills, geometric. They're treated like punctuation, not illustration. When in doubt about whether to add an icon: don't. When you do add one, it never carries meaning the text doesn't already carry — it punctuates, it doesn't speak.

---

## 3. Visual tokens

### 3.1 Color

```
Ink             #0A0A0A    Body text, primary
Ink-2           #1A1A1A    Secondary text
Muted           #5C6066    Supporting text, captions
Muted-2         #8A8F96    Metadata, eyebrows on dark surfaces
Rule            #E5E5E5    Hairline dividers
Rule-2          #F0F0F0    Subtle internal rules
Paper           #FFFFFF    Default background
Paper-2         #FAFAF8    Card backgrounds, subtle fills
Paper-3         #F4F4F1    Block quotes, inset panels

Accent          #1E6FFF    The single accent — emphasis, italics, active states
Accent-soft     #E8F0FF    Accent backgrounds (used very rarely)

Warn            #C0392B    Errors, "before" states in case studies
OK              #0A7C53    Success, "after" states in case studies
```

White-on-dark is permitted on inverted blocks (a single dark card on a light surface), using `Ink` as background and `Accent` for emphasis text. Never invert an entire page.

### 3.2 Typography

```
Display + UI    Inter Tight       300, 400, 500, 600, 700
Editorial       Source Serif 4    400, 500 (italic 400 for emphasis)
Monospace       JetBrains Mono    400, 500
```

All three are free Google Fonts. Future paid upgrade path: replace Source Serif 4 with **Tiempos Headline** or **GT Sectra** when budget allows — same role, more luxurious feel.

**Type rules:**

- **Headlines** are display sans (Inter Tight, weight 500), letter-spacing `-0.02em` to `-0.025em`. The weight feels heavier than it reads — confident without shouting.
- **Inside any headline**, one or two key words switch to *italic Source Serif 4 in accent color*. This is the single most recognizable device in the system. Use it once per headline, never more.
- **Body text** is Inter Tight 400/500, 14–16px, line-height 1.5. Color is `Ink-2` or `Muted` depending on hierarchy.
- **Eyebrows and labels** are JetBrains Mono, 10–11px, `letter-spacing: 0.12em–0.16em`, `text-transform: uppercase`, color is `Muted` or `Accent`. Always.
- **Numerics** are Inter Tight with `font-feature-settings: "tnum"` for tabular figures. Stat numbers go large (44–64px, weight 400) with letter-spacing `-0.035em`.
- **Pull quotes** are Source Serif 4 italic, 24–32px, line-height 1.18, with a 2px accent rule on the left.

### 3.3 Spacing scale

```
4   8   12   16   24   32   48   64   96
```

Use these values. Don't invent 18, 20, 28 — they're not in the system. The 8-point base keeps everything visually aligned across surfaces.

### 3.4 Rules and dividers

- **Hairline**: `1px solid #E5E5E5` — default divider
- **Subtle**: `1px solid #F0F0F0` — inside cards, between repeated rows
- **Heavy**: `1px solid #0A0A0A` — separates a section heading from its content (used as the top edge of stat rows)
- **Accent**: `2px solid #1E6FFF`, width 56–96px — single emphatic rule under a section title or to the left of a pull quote

### 3.5 Iconography spec

```
Style         Monoline, geometric, no fills
Stroke        1.25px (1.5px on dark surfaces)
Stroke-cap    round
Stroke-join   round
Size          16–22px inline; 36–44px in icon containers
Container     1px ink border, square, content centered
Color         currentColor (inherits from text)
```

When inside a "card with icon header" pattern, the icon sits in a `36–44px` square container with a `1px solid Ink` border. Never filled, never colored, never with a drop shadow.

### 3.6 Shadows

Almost never. The single approved shadow is the *page shadow* — a deck slide or document floating on a paper-3 canvas:

```
box-shadow: 0 1px 0 rgba(0,0,0,.04), 0 30px 60px -30px rgba(0,0,0,.18);
```

That's it. No card shadows, no button shadows, no hover shadows. Depth is communicated by rules and spacing, not by elevation.

---

## 4. The pattern library

These are recurring devices in our work. They are reusable because they're tested — clients and execs respond to them.

### 4.1 The display headline with italicized emphasis
A confident sans-serif headline where one key phrase switches to accent-blue italic serif. Carries 70% of the brand recognition.

### 4.2 The monospace eyebrow
Above every section title: a small uppercase JetBrains Mono label in accent or muted color. Names the section before you read the headline.

### 4.3 The four-cell stat row
A row of 3–4 large numerics with hairline vertical rules between them, sitting under a single 1px-Ink top rule. Used for credentials, outcomes, before/after numbers.

### 4.4 The slide stamp
Top-right corner: `05 · 11` in monospace. Reads like a printed report.

### 4.5 The breadcrumb crumb
Top-left: `Section 02 / The partner thesis` in monospace, with the section number in accent. Tells the reader where they are without a full nav.

### 4.6 The editorial pull quote
Source Serif 4 italic, large, with a 2px accent rule on the left and a monospace attribution below in caps. Used to set tone, never to summarize.

### 4.7 The "before / after" case study block
Two stacked blocks with colored left rules — `Warn` red for before, `OK` green for after. Headline + 2–4 bullets each. Used in every case study.

### 4.8 The dark inversion card
A single card with `Ink` background, white text, accent for emphasis. Used to break visual rhythm and surface a "stack" or "platform" callout. Never use as a full page background.

### 4.9 The numbered grid
3×2 or 2×2 grid of cards, each with a small `01`–`06` numeral in monospace top-right, an icon in a square container, a headline, body copy, and 2–3 capability bullets prefixed with a short accent line. Used for any "N pillars" pattern.

### 4.10 The bordered container, never the rounded card
We use `1px solid #E5E5E5` containers with sharp corners. We do not use `border-radius: 8px` rounded cards with shadows. The visual distinction reads as "document," not "app."

---

## 5. What we never do

- **No emoji.** Anywhere. Including bullet points.
- **No rounded "friendly" shapes.** Sharp corners on containers, square icon frames. `border-radius: 0`. The single exception is the small "Confidential" pill on a cover, which is `border-radius: 999px`.
- **No gradients.** Not on backgrounds, not on text, not on buttons.
- **No drop shadows on UI elements.** Buttons, cards, inputs all sit flat. The only shadow is the page shadow.
- **No teal, no purple, no neon, no iridescent.** These are AI-startup colors. Our accent is one specific blue, full stop.
- **No center-aligned body text.** Headlines can occasionally be centered on a cover; body copy never.
- **No stock photography of people.** No suited executives shaking hands, no diverse teams looking at laptops, no abstract "innovation" photography. If we need a visual on a hero, it's our own diagram, our own typography, or nothing.
- **No AI-generated illustrations.** No DALL-E, Midjourney, or Imagen art. The visual signature of those tools is exactly what we're trying not to look like.
- **No bullet point overload.** A list of 7 bullets is almost always 3 grouped concepts. Find the groupings.
- **No "click here" or "learn more."** Buttons are verbs that name the action: `Begin co-sell conversation`, `View case study`, `Download proposal`.
- **No exclamation marks.** In any copy we write, anywhere.

---

## 6. Surface-specific guidance

The doctrine is the same across surfaces. The *liberties* differ.

### 6.1 Executive decks & client proposals
The most editorial surface. All principles apply at full strength. Slides are landscape `1280×760`, cover image full-bleed, generous margins (56px), large display type (44–96px). Animation: none. Output as a single self-contained HTML file (deck-as-website pattern). The web-interface chrome — left rail TOC, slide stamps, breadcrumbs, keyboard nav — is part of the brand on this surface. See `devx_labs_google_cloud.html` as the canonical reference.

### 6.2 Marketing website (devxlabs.ai)
Inherits everything from decks, but gains:
- **Subtle motion** is permitted: fade-in on scroll, smooth-scroll anchors, hover state color shifts (text → accent over 150ms ease). No bouncing, no spring physics, no parallax, no scroll-jacking.
- **Larger type** on hero sections — display headlines can run 80–120px on desktop.
- **Sectioned long-scroll** is allowed: alternating Paper / Paper-2 backgrounds, separated by full-bleed hairline rules, never by abrupt color blocks.
- **Buttons** are flat: 1px ink border, ink background, white text. Hover: background flips to `Accent`, border to `Accent`. No other states needed.
- **Forms** use the same input pattern as the deck password gate: 1px rule, no border-radius, focus state is border-color → accent.

### 6.3 Internal docs, memos, PRDs
The least editorial surface. Strip the chrome (no slide rails, no chapter stamps), but **keep the type stack**. A PRD or memo in plain Markdown rendered with Inter Tight + Source Serif 4 italic for emphasis still reads like ours. Specifics:
- Default to flowing prose with H1/H2/H3 hierarchy. Avoid bullet-soup.
- Use the monospace eyebrow above section headers when the doc is a formal deliverable (RFP response, executive memo). Skip it for quick internal notes.
- Tables use 1px hairline rules, no zebra striping, no row backgrounds.
- Code blocks: JetBrains Mono, `Paper-3` background, no border-radius.
- Page width: max-width 720px for prose, 960px for docs with tables. Anything wider is uncomfortable to read.

---

## 7. The CSS variable block (drop into any artifact)

```css
:root {
  --ink:         #0A0A0A;
  --ink-2:       #1A1A1A;
  --muted:       #5C6066;
  --muted-2:     #8A8F96;
  --rule:        #E5E5E5;
  --rule-2:      #F0F0F0;
  --paper:       #FFFFFF;
  --paper-2:     #FAFAF8;
  --paper-3:     #F4F4F1;

  --accent:      #1E6FFF;
  --accent-soft: #E8F0FF;
  --warn:        #C0392B;
  --ok:          #0A7C53;

  --f-display:   'Inter Tight', -apple-system, BlinkMacSystemFont, sans-serif;
  --f-serif:     'Source Serif 4', Georgia, serif;
  --f-mono:      'JetBrains Mono', 'SFMono-Regular', monospace;
}

body {
  font-family: var(--f-display);
  color: var(--ink);
  background: var(--paper);
  font-feature-settings: "ss01","cv11","tnum";
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
```

Google Fonts import (one line):

```html
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@300;400;500;600;700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,500;1,8..60,400&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
```

---

## 8. Reference exemplars

When in doubt about whether something fits the doctrine, check these:

- **McKinsey Quarterly** — editorial typography, restraint, the use of italic emphasis in headlines
- **BCG perspectives reports** — section structure, stat rows, the way they balance gravitas with clarity
- **Stripe.com** — type-led marketing, the exact tradition of "one accent, used sparingly"
- **Linear.app** — product UI restraint, hairline rules, monospace details
- **Bruno Maag's typography work** — what disciplined sans-serif looks like at scale
- **Bloomberg Businessweek** — the editorial italic move, monospace as a system
- **Pentagram's identity work for serious institutions** — the exact tone we're after

We do *not* model after: Lovable.dev, Vercel marketing pages (too playful for our positioning), generic SaaS landing-page templates, anything from the "AI startup" 2024 visual cohort (purple gradients, glassmorphism, bento boxes).

---

## 9. The Prompt Block

**Use this verbatim with any LLM (Claude, GPT-4, Cursor, v0, etc.) when you want output that follows the doctrine.** Paste it as a system instruction or at the top of your prompt.

```
You are designing for DevX Labs, following The DevX Doctrine. Apply this design language to whatever I ask you to make — deck, web page, document, component, anything.

DESIGN ESSENCE
Top-tier consulting firm that ships code. Editorial restraint of McKinsey, typographic discipline of Stripe, none of the visual tropes of an AI startup. White space, one accent color, typography does the work.

PRINCIPLES
1. Editorial restraint — your default move is to remove, not add.
2. Structural grid discipline — 12-column, hairline rules, generous margins.
3. Typography is the primary device — not color, not decoration.
4. One accent (#1E6FFF), used 2–4 times per surface maximum.
5. Monoline icons, 1.25px stroke, no fills, treated as punctuation.

VISUAL TOKENS

Color:
  --ink: #0A0A0A; --ink-2: #1A1A1A; --muted: #5C6066; --muted-2: #8A8F96;
  --rule: #E5E5E5; --rule-2: #F0F0F0;
  --paper: #FFFFFF; --paper-2: #FAFAF8; --paper-3: #F4F4F1;
  --accent: #1E6FFF; --accent-soft: #E8F0FF;
  --warn: #C0392B; --ok: #0A7C53;

Type stack:
  Display/UI:  Inter Tight (300,400,500,600,700)
  Editorial:   Source Serif 4 (400,500; italic 400)
  Mono:        JetBrains Mono (400,500)

Spacing scale: 4, 8, 12, 16, 24, 32, 48, 64, 96. Don't invent 18 or 20.

Rules:
  Hairline:  1px solid #E5E5E5
  Subtle:    1px solid #F0F0F0
  Heavy:     1px solid #0A0A0A
  Accent:    2px solid #1E6FFF, 56–96px wide

KEY PATTERNS
- Display headlines in Inter Tight 500, letter-spacing -0.02em to -0.025em, with one or two key words in italic Source Serif 4 in accent color. This single device is the most recognizable.
- Eyebrows above sections: JetBrains Mono, 10–11px, letter-spacing 0.12–0.16em, uppercase, in accent or muted.
- Stat rows: 3–4 large numerics (44–64px, Inter Tight 400, letter-spacing -0.035em), separated by hairline vertical rules, sitting under a 1px ink top rule.
- Pull quotes: Source Serif 4 italic, 24–32px, with 2px accent left rule, monospace attribution in caps below.
- Cards: 1px solid #E5E5E5, sharp corners (no border-radius), no shadows. Optional 1px solid #0A0A0A icon container, square, 36–44px.
- Inversions: occasionally a single dark card (Ink background, white text, accent emphasis) to break visual rhythm. Never invert a full page.

NEVER DO
- No emoji anywhere.
- No rounded "friendly" shapes (no border-radius on cards/containers, except a Confidential pill at 999px).
- No gradients. No drop shadows except the single page-shadow on document/slide containers.
- No teal, purple, neon, iridescent — these are AI-startup colors.
- No center-aligned body text.
- No stock photography of people, no AI-generated illustrations.
- No "click here" / "learn more" / exclamation marks.
- No bullet-soup (a list of 7 is almost always 3 grouped concepts).

CSS VARIABLES TO DROP INTO ANY ARTIFACT

  :root {
    --ink: #0A0A0A; --ink-2: #1A1A1A; --muted: #5C6066; --muted-2: #8A8F96;
    --rule: #E5E5E5; --rule-2: #F0F0F0;
    --paper: #FFFFFF; --paper-2: #FAFAF8; --paper-3: #F4F4F1;
    --accent: #1E6FFF; --accent-soft: #E8F0FF;
    --warn: #C0392B; --ok: #0A7C53;
    --f-display: 'Inter Tight', sans-serif;
    --f-serif: 'Source Serif 4', Georgia, serif;
    --f-mono: 'JetBrains Mono', monospace;
  }

  Google Fonts import:
  <link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@300;400;500;600;700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,500;1,8..60,400&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />

REFERENCE EXEMPLARS
McKinsey Quarterly, BCG reports, Stripe.com, Linear.app, Bloomberg Businessweek, Pentagram identity work. Do NOT model after Lovable.dev, generic SaaS templates, or 2024 AI-startup visuals (purple gradients, glassmorphism, bento boxes).

When you produce output, embed the CSS variables, import the fonts, and apply the patterns. If a request conflicts with the doctrine (e.g., "make it more playful," "add some emoji"), explain the conflict and propose a version that stays in doctrine. The doctrine wins.
```

---

## 10. Versioning

- **v1.0** · April 2026 — Initial doctrine, derived from the Google Cloud partnership deck.

When the doctrine evolves, bump the version. Doctrine changes are not backwards-compatible — old work doesn't get retrofitted, but new work follows the current version.

---

*The DevX Doctrine* · DevX Labs · Confidential

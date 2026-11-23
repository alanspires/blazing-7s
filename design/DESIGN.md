The Blazing 7s control page feels like the machine's own back-office panel at night: a calm, dark graphite workspace where the only heat is the flame of the Spin button and the gold of a win. It is a tool first and a toy second, so it stays quiet and dense, and lets the machine's state and the money be the loudest things on screen.
Colour. Layer the greys: color-bg for the page, color-surface for panels, color-surface-raised for controls and selected rows, color-surface-sunken for wells such as the reel window and the activity log. Use color-accent (the flame) only for the one main action on a screen, usually Spin, and for focus. Use color-win only for money won and payout amounts. color-ok means online or confirmed, color-warn means "checking what happened" or an armed outcome, color-danger means offline or refused. The tint-* colours are quiet backgrounds behind those states; never paint whole panels in them.
Type. Lato carries every label, row and paragraph at 13 px body, 12 px small and 11 px uppercase labels. URW Gothic, a round geometric face with a casino-marquee feel, is only for figures: the credit balance (type-credits), big result numbers (type-marquee), panel titles (type-title) and key figures such as return % (type-figure). Never set paragraphs in it. Use tabular figures for every number that changes.
Spacing and shape. Spacing steps by 4 px (space-xs) up to 48 px; rows use space-s, panels space-l. Corners are small: radius-s on chips and inputs, radius-m on panels and buttons, radius-l only on sheets and the reel window, radius-pill only on status pills. Panels are separated by color-line borders, not shadows; shadow-sheet is only for sheets and dialogs floating over the page.
Do: keep one main flame action per screen; show every money figure as credits with the dollar value beside it; keep status (online, armed outcome, checking) in one consistent place. Don't: use gradients, glows or neon; colour whole cards; set body text in the display face; add emoji in place of icons; make the phone a squeezed desktop.

<!-- design-loop:tokens -->

## Tokens

### color

| Variable | Type | Value | Description |
| --- | --- | --- | --- |
| `--color-bg` | color | `#121315` | Page background |
| `--color-surface` | color | `#1b1c1f` | Panels and cards |
| `--color-surface-raised` | color | `#242629` | Raised controls, inputs, selected rows |
| `--color-surface-sunken` | color | `#0d0e0f` | Wells such as the reel window and the log |
| `--color-line` | color | `#313337` | Dividers and borders |
| `--color-line-strong` | color | `#46494e` | Focused or hovered borders |
| `--color-ink` | color | `#ecedef` | Main text |
| `--color-ink-muted` | color | `#9b9fa5` | Secondary text and labels |
| `--color-ink-faint` | color | `#6b6f75` | Timestamps, disabled text |
| `--color-accent` | color | `#ef5531` | The Blazing 7 flame: the main action (Spin) and focus, used sparingly |
| `--color-accent-strong` | color | `#fc6a43` | Main action on hover or press |
| `--color-on-accent` | color | `#121315` | Text on the accent |
| `--color-win` | color | `#f4c244` | Wins, payouts and credits won |
| `--color-ok` | color | `#5cc485` | Online, confirmed, done |
| `--color-warn` | color | `#e7a84a` | Checking what happened, armed outcome, attention |
| `--color-danger` | color | `#e75e5e` | Offline, refused, errors |
| `--color-tint-accent` | color | `color(srgb 0.937 0.333 0.192 / 0.14)` | Quiet background behind accent items |
| `--color-tint-win` | color | `color(srgb 0.957 0.761 0.267 / 0.14)` | Quiet background behind wins |
| `--color-tint-ok` | color | `color(srgb 0.361 0.769 0.522 / 0.14)` | |
| `--color-tint-warn` | color | `color(srgb 0.906 0.659 0.29 / 0.14)` | |
| `--color-tint-danger` | color | `color(srgb 0.906 0.369 0.369 / 0.14)` | |
| `--color-scrim` | color | `color(srgb 0 0 0 / 0.6)` | Behind sheets and dialogs |

### font

| Variable | Type | Value | Description |
| --- | --- | --- | --- |
| `--font-ui` | fontFamily | `"Lato", "Helvetica Neue", "Arial", sans-serif` | |
| `--font-display` | fontFamily | `"URW Gothic", "Century Gothic", "Avenir Next", sans-serif` | |

### weight

| Variable | Type | Value | Description |
| --- | --- | --- | --- |
| `--weight-regular` | fontWeight | `400` | |
| `--weight-medium` | fontWeight | `500` | |
| `--weight-bold` | fontWeight | `700` | |
| `--weight-black` | fontWeight | `900` | |

### size

| Variable | Type | Value | Description |
| --- | --- | --- | --- |
| `--size-xs` | dimension | `11px` | |
| `--size-s` | dimension | `12px` | |
| `--size-m` | dimension | `13px` | |
| `--size-l` | dimension | `15px` | |
| `--size-xl` | dimension | `18px` | |
| `--size-xxl` | dimension | `24px` | |
| `--size-hero` | dimension | `40px` | |
| `--size-jumbo` | dimension | `56px` | |

### line

| Variable | Type | Value | Description |
| --- | --- | --- | --- |
| `--line-tight` | number | `1.1` | |
| `--line-snug` | number | `1.3` | |
| `--line-body` | number | `1.45` | |

### space

| Variable | Type | Value | Description |
| --- | --- | --- | --- |
| `--space-2xs` | dimension | `2px` | |
| `--space-xs` | dimension | `4px` | |
| `--space-s` | dimension | `8px` | |
| `--space-m` | dimension | `12px` | |
| `--space-l` | dimension | `16px` | |
| `--space-xl` | dimension | `24px` | |
| `--space-2xl` | dimension | `32px` | |
| `--space-3xl` | dimension | `48px` | |

### radius

| Variable | Type | Value | Description |
| --- | --- | --- | --- |
| `--radius-s` | dimension | `3px` | |
| `--radius-m` | dimension | `6px` | |
| `--radius-l` | dimension | `10px` | |
| `--radius-pill` | dimension | `999px` | |

### shadow

| Variable | Type | Value | Description |
| --- | --- | --- | --- |
| `--shadow-sheet` | shadow | `0px 8px 32px 0px color(srgb 0 0 0 / 0.5)` | Only for sheets and dialogs that float over the page |

### duration

| Variable | Type | Value | Description |
| --- | --- | --- | --- |
| `--duration-fast` | duration | `120ms` | |
| `--duration-base` | duration | `220ms` | |

### type

| Variable | Type | Value | Description |
| --- | --- | --- | --- |
| `--type-body-font-family` | fontFamily | `var(--font-ui)` | |
| `--type-body-font-size` | dimension | `var(--size-m)` | |
| `--type-body-font-weight` | fontWeight | `var(--weight-regular)` | |
| `--type-body-letter-spacing` | dimension | `0px` | |
| `--type-body-line-height` | number | `var(--line-body)` | |
| `--type-small-font-family` | fontFamily | `var(--font-ui)` | |
| `--type-small-font-size` | dimension | `var(--size-s)` | |
| `--type-small-font-weight` | fontWeight | `var(--weight-regular)` | |
| `--type-small-letter-spacing` | dimension | `0px` | |
| `--type-small-line-height` | number | `var(--line-snug)` | |
| `--type-label-font-family` | fontFamily | `var(--font-ui)` | |
| `--type-label-font-size` | dimension | `var(--size-xs)` | |
| `--type-label-font-weight` | fontWeight | `var(--weight-bold)` | |
| `--type-label-letter-spacing` | dimension | `0.6px` | |
| `--type-label-line-height` | number | `var(--line-snug)` | |
| `--type-heading-font-family` | fontFamily | `var(--font-ui)` | |
| `--type-heading-font-size` | dimension | `var(--size-l)` | |
| `--type-heading-font-weight` | fontWeight | `var(--weight-bold)` | |
| `--type-heading-letter-spacing` | dimension | `0px` | |
| `--type-heading-line-height` | number | `var(--line-snug)` | |
| `--type-title-font-family` | fontFamily | `var(--font-display)` | |
| `--type-title-font-size` | dimension | `var(--size-xl)` | |
| `--type-title-font-weight` | fontWeight | `var(--weight-bold)` | |
| `--type-title-letter-spacing` | dimension | `0.4px` | |
| `--type-title-line-height` | number | `var(--line-tight)` | |
| `--type-figure-font-family` | fontFamily | `var(--font-display)` | |
| `--type-figure-font-size` | dimension | `var(--size-xxl)` | |
| `--type-figure-font-weight` | fontWeight | `var(--weight-bold)` | |
| `--type-figure-letter-spacing` | dimension | `0px` | |
| `--type-figure-line-height` | number | `var(--line-tight)` | |
| `--type-credits-font-family` | fontFamily | `var(--font-display)` | |
| `--type-credits-font-size` | dimension | `var(--size-hero)` | |
| `--type-credits-font-weight` | fontWeight | `var(--weight-bold)` | |
| `--type-credits-letter-spacing` | dimension | `0px` | |
| `--type-credits-line-height` | number | `var(--line-tight)` | |
| `--type-marquee-font-family` | fontFamily | `var(--font-display)` | |
| `--type-marquee-font-size` | dimension | `var(--size-jumbo)` | |
| `--type-marquee-font-weight` | fontWeight | `var(--weight-bold)` | |
| `--type-marquee-letter-spacing` | dimension | `-0.5px` | |
| `--type-marquee-line-height` | number | `var(--line-tight)` | |

<!-- /design-loop:tokens -->

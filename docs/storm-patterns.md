# Storm UI Patterns for STITCH

## Layout Rules

1. **Box default is `flexDirection="column"`** - always set `flexDirection="row"` for horizontal
2. **Text does NOT nest inline** - each `<Text>` is block-level. For colored spans on same line, use siblings inside `<Box flexDirection="row">`
3. **Welcome accepts `children`** - rendered between shortcuts and prompt
4. **Digits renders block letters** - supports A-Z, 0-9, colon, dot, dash, space. Uses `███` blocks.

## Correct Horizontal Layout

```tsx
// WRONG - stacks vertically
<Box>
  <Text>Label:</Text>
  <Text>Value</Text>
</Box>

// CORRECT - side by side
<Box flexDirection="row" gap={1}>
  <Text>Label:</Text>
  <Text bold>Value</Text>
</Box>
```

## Welcome + Digits Combo

Welcome supports `logo` prop (renders as gradient text) AND `children` prop (custom content).
But the `logo` prop renders the string as-is. For Digits, pass as children:

```tsx
<Welcome
  title=""
  description="..."
  // No logo prop - use children instead for Digits
>
  <Digits value="STITCH" color="#82AAFF" bold />
</Welcome>
```

Children render between the main content and the bottom divider/prompt.

## Welcome Internal Structure

Welcome renders sections in this order:
1. Logo (if logo prop set) - centered with gradient
2. Logo spacer
3. Title (with optional titleGradient)
4. Version
5. Description
6. Divider
7. Actions (selectable list)
8. Shortcuts (grid)
9. **children** (custom content)
10. Bottom divider
11. Prompt text

## Table Component

Works correctly with simple string/number data.
Do NOT use renderCell - it breaks column layout.
Column widths must be set explicitly.

## GradientProgress

Use 3 color stops for smooth gradient: `colors={["#82AAFF", "#89DDFF", "#C792EA"]}`
For solid color at 100%, repeat the same color: `colors={["#C3E88D", "#C3E88D", "#89DDFF"]}`

## Border Styles

Box supports: `borderStyle="single"`, `borderStyle="round"`
With `borderColor` for color.

## Forcing Re-renders

Use `useInterval` from Storm (not React) to force periodic re-renders.
Storm's reconciler needs this for timer/progress animations.

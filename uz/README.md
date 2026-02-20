# z2w UX Sandbox (`uz`)

Next.js 16 UI sandbox for prototyping and developing new components for the z2w stack.

## Stack

- **Next.js 16** · App Router
- **Tailwind CSS v4**
- **Base UI** (@base-ui/react) for accessible primitives
- **shadcn/ui** component library
- **next-themes** for dark/light mode
- **Lucide React** icons

## Development

```bash
npm install
npm run dev      # starts on http://localhost:3000
```

## Build & Lint

```bash
npm run build
npm run lint
```

## Key source paths

| Path | Purpose |
|---|---|
| `app/` | App Router pages and layouts |
| `components/ui/` | shadcn/ui base components |
| `components/providers/` | Theme and context providers |
| `lib/utils.ts` | Shared utilities (cn, etc.) |

## Purpose

This app serves as a development sandbox — use it to prototype components, test theming, and validate design patterns before promoting them to the main `explorer/` app or the Flask admin panel templates.

---

*Part of the [z2w](../README.md) stack*

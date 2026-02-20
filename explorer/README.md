# z2w Ticket Explorer

Next.js 16 frontend for browsing and inspecting Zendesk tickets offloaded to Wasabi by the z2w daemon.

## Stack

- **Next.js 16** · App Router
- **Tailwind CSS v4**
- **shadcn/ui** component library
- **Radix UI** primitives
- **Lucide React** icons

## Development

```bash
npm install
npm run dev      # starts on http://localhost:3000
```

## Build

```bash
npm run build    # production build → .next/
npm run start    # serve production build
```

## Lint

```bash
npm run lint     # ESLint (eslint-config-next)
```

## Key source paths

| Path | Purpose |
|---|---|
| `src/app/` | App Router pages and layouts |
| `src/components/ExplorerShell.tsx` | Main shell with sidebar and panels |
| `src/components/panels/` | Individual panel components |
| `src/components/ui/` | shadcn/ui base components |
| `src/lib/api.ts` | API calls to the Flask backend |
| `src/lib/storage.ts` | Local state persistence |
| `src/hooks/` | Custom React hooks |

## Configuration

The Explorer communicates with the Flask backend at `/opt/z2w/main.py`. Update `src/lib/api.ts` with the correct base URL if running on a different host or port.

---

*Part of the [z2w](../README.md) stack*

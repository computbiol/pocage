# Pocket Agent Frontend

`frontend` is the Pocket Agent (pocage) web client built with React + Vite. It provides session navigation, connector selection, workspace setup, and chat interaction UI.

## Stack

- React 19
- TypeScript
- Vite 7
- ESLint 9

## Directory Structure

```text
src
├── api.ts
├── App.tsx
├── assets
├── components
│   ├── ChatArea.tsx
│   └── Sidebar.tsx
├── index.css
├── main.tsx
├── runTimeline.ts
└── utils.ts
```

## Run

```bash
cd frontend
npm install
npm run dev
```

Default dev URL: `http://localhost:5173`

## Environment Variables

- `VITE_API_URL`: backend base URL, default `http://localhost:8000`

The frontend uses the repository root as `envDir`:

- local development reads root `.env.local`
- Docker Compose builds use root `.env.compose` through `docker-compose.yml`

Templates for those files live at root:

- `.env.local.example`
- `.env.compose.example`

Example:

```bash
VITE_API_URL=http://127.0.0.1:8000 npm run dev
```

## Common Commands

```bash
npm run lint
npm run build
npm run preview
```

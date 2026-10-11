# Фронтенд

React 19 + Vite + TypeScript + Tailwind 4.

```bash
npm ci
npm run dev      # http://localhost:5173, /api проксируется на http://localhost:8000 (VITE_API_PROXY)
npm run build    # проверка типов и сборка в dist/
npm run lint     # oxlint
```

В Docker сборку раздаёт nginx (`nginx.conf`), он же проксирует `/api/` в сервис `api`.

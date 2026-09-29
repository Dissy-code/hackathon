import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/* Компонент и его стили лежат на уровень выше, рядом со статической
   версией страницы, — специально, чтобы не было двух копий. Поэтому
   открываем сборщику доступ к родительской папке. */
export default defineConfig({
  plugins: [react()],
  // сборку отдаёт FastAPI: /configurator.html -> app/index.html, ассеты — /app/assets/…
  base: '/app/',
  build: { outDir: '../app', emptyOutDir: true },
  server: {
    fs: { allow: ['..'] },
    // API — на uvicorn (uvicorn app.main:app --port 8000 из backend/)
    proxy: { '/api': 'http://localhost:8000' },
  },
});

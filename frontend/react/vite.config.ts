import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/* Компонент и его стили лежат на уровень выше, рядом со статической
   версией страницы, — специально, чтобы не было двух копий. Поэтому
   открываем сборщику доступ к родительской папке. */
export default defineConfig({
  plugins: [react()],
  server: { fs: { allow: ['..'] } },
});

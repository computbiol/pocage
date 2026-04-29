import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const repoRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  envDir: repoRoot,
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173
  }
});

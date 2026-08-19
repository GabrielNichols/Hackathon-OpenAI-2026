import { cloudflare } from '@cloudflare/vite-plugin'
import { sites } from '@openai/sites-vite-plugin'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [
    react(),
    sites(),
    cloudflare({
      viteEnvironment: { name: 'server' },
      config: {
        main: './worker/index.ts',
        compatibility_date: '2026-05-22',
        assets: {
          binding: 'ASSETS',
          not_found_handling: 'single-page-application',
          run_worker_first: false,
        },
      },
    }),
  ],
})

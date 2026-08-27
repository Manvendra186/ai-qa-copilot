import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

/**
 * Dev-only mock SSE endpoint (build bible §19 S0.7: "SSE client (mocked)").
 *
 * Serves `/mock/events` with the same shape the real jobs API will send at
 * S0.9 (`GET /events`): `job.started` → per stage `stage.started`,
 * `progress` ×4, `stage.completed` → `job.completed`. Standard SSE framing
 * (`event:` + `data:` JSON) so the browser client (`EventSource`) is
 * contract-compatible.
 */
function mockSse(): Plugin {
  // Mirrors PIPELINE_STAGES in src/lib/pipeline.ts (keep in sync).
  const stages = [
    'requirement',
    'test_design',
    'automation',
    'execution',
    'failure_analysis',
    'fix',
  ];

  const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

  return {
    name: 'qa-copilot:mock-sse',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/mock/events', (req, res) => {
        if (req.method !== 'GET') {
          res.statusCode = 405;
          res.end();
          return;
        }
        res.statusCode = 200;
        res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
        res.setHeader('Cache-Control', 'no-cache, no-transform');
        res.setHeader('Connection', 'keep-alive');
        res.setHeader('X-Accel-Buffering', 'no');
        res.flushHeaders();
        res.on('error', () => undefined); // client disconnecting mid-stream is normal

        const send = (event: string, data: Record<string, unknown>) => {
          if (!res.writable) return;
          res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
        };

        const jobId = `job-mock-${Date.now().toString(36)}`;
        let closed = false;
        req.on('close', () => {
          closed = true;
        });

        void (async () => {
          send('job.started', { job_id: jobId, stages });
          for (const stage of stages) {
            if (closed) return;
            await sleep(300);
            send('stage.started', { job_id: jobId, stage });
            for (let tick = 1; tick <= 4; tick += 1) {
              if (closed) return;
              await sleep(250);
              send('progress', { job_id: jobId, stage, value: tick / 4 });
            }
            if (closed) return;
            send('stage.completed', { job_id: jobId, stage });
          }
          if (closed) return;
          await sleep(200);
          send('job.completed', { job_id: jobId });
          res.end();
        })();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), mockSse()],
  server: {
    port: 5173,
    // Future API calls (S0.9+) go through the dev proxy to FastAPI.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});

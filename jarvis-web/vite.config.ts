import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit(),
		{
			// Dev-only: attach the same HA WebSocket proxy the production server uses,
			// so `vite dev` supports /ws without a separate process.
			name: 'jarvis-ws-proxy-dev',
			async configureServer(server) {
				// mrmime, which sirv types static files with, has no '.ico' entry, so
				// favicon.ico is served with an empty Content-Type in both dev and
				// prod. sirv keeps a type already set on the response. The production
				// launcher does the same thing — see scripts/postbuild.mjs.
				server.middlewares.use((req, res, next) => {
					if (req.url?.split('?')[0].endsWith('.ico')) {
						res.setHeader('Content-Type', 'image/x-icon');
					}
					next();
				});

				const { attachWsProxy } = await import('./server/ws-proxy.js');
				// Vite types `httpServer` as http.Server OR http2.Http2SecureServer,
				// because a dev server configured for https/2 is the latter. This one
				// is not — there is no https block in this config — and the relay only
				// ever asks it for 'upgrade', which both of them have.
				if (server.httpServer) attachWsProxy(server.httpServer as import('node:http').Server);
			}
		}
	],
	test: {
		environment: 'node',
		include: ['src/**/*.test.ts']
	}
});

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
				const { attachWsProxy } = await import('./server/ws-proxy.js');
				if (server.httpServer) attachWsProxy(server.httpServer);
			}
		}
	],
	test: {
		environment: 'node',
		include: ['src/**/*.test.ts']
	}
});

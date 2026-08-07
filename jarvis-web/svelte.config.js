import adapter from '@sveltejs/adapter-node';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	preprocess: vitePreprocess(),
	kit: {
		adapter: adapter({ out: 'build' }),
		csp: {
			mode: 'auto',
			directives: {
				'default-src': ['self'],
				'script-src': ['self'],
				'connect-src': ['self', 'ws:', 'wss:'],
				'img-src': ['self', 'data:'],
				'style-src': ['self', 'unsafe-inline']
			}
		}
	}
};

export default config;

import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: '../tests/web',
	testMatch: 'e2e.spec.ts',
	timeout: 60_000,
	fullyParallel: false,
	workers: 1,
	reporter: [['list']],
	use: {
		baseURL: 'http://127.0.0.1:8199',
		browserName: 'chromium',
		headless: true,
		launchOptions: {
			executablePath: '/opt/pw-browsers/chromium',
			args: [
				'--no-sandbox',
				'--use-fake-device-for-media-stream',
				'--use-fake-ui-for-media-stream',
				'--autoplay-policy=no-user-gesture-required'
			]
		}
	},
	webServer: {
		command: 'node ../tests/web/serve-e2e.mjs',
		url: 'http://127.0.0.1:8199/healthz',
		reuseExistingServer: false,
		stdout: 'pipe',
		stderr: 'pipe',
		timeout: 60_000
	}
});

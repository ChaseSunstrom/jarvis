import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: 'e2e',
	// Every *.spec.ts in e2e/, not one named file. It WAS one named file, which
	// meant a new spec added beside it was collected by nothing and reported as
	// passing by being absent.
	testMatch: '*.spec.ts',
	timeout: 60_000,
	fullyParallel: false,
	workers: 1,
	reporter: [['list']],
	use: {
		baseURL: 'http://127.0.0.1:8199',
		browserName: 'chromium',
		headless: true,
		launchOptions: {
			// No `executablePath`. Pinning one to a developer container's layout
			// is why this suite launched nothing on CI: `/opt/pw-browsers/chromium`
			// exists in that container and nowhere else, so every test failed with
			// "executable doesn't exist" the moment the specs became discoverable.
			//
			// Playwright finds its own browser: on CI from the default cache that
			// `npx playwright install --with-deps chromium` populates, and in a
			// container from PLAYWRIGHT_BROWSERS_PATH, which is the supported way
			// to point it at a preinstalled one.
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

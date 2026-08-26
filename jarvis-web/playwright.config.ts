import { defineConfig } from '@playwright/test';

// The port is a knob because 8199 is also where a running install's HUD
// listens (docker-compose.yml, host networking). On the box that runs Jarvis,
// `reuseExistingServer: false` below would refuse to start — or, flipped on,
// would silently test the live HUD instead of the mock-backed build. So a
// verify run sets E2E_PORT to something free; the default stays 8199 so CI
// and the README are unchanged. serve-e2e.mjs reads PORT, which is passed
// through here.
const port = process.env.E2E_PORT ?? '8199';

export default defineConfig({
	testDir: 'e2e',
	// Every *.spec.ts in e2e/, not one named file. It WAS one named file, which
	// meant a new spec added beside it was collected by nothing and reported as
	// passing by being absent.
	testMatch: '*.spec.ts',
	timeout: 60_000,
	fullyParallel: false,
	workers: 1,
	// On CI the `github` reporter annotates every failed test on the check run
	// — readable through the public API without a token, which the job log is
	// not — and the html report is what the workflow's upload-artifact step
	// has been looking for (with `list` alone it found nothing, twice). Locally,
	// the list is enough and an html report would open a browser tab.
	reporter: process.env.CI
		? [['list'], ['github'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
		: [['list']],
	use: {
		baseURL: `http://127.0.0.1:${port}`,
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
		url: `http://127.0.0.1:${port}/healthz`,
		env: { PORT: port },
		reuseExistingServer: false,
		stdout: 'pipe',
		stderr: 'pipe',
		timeout: 60_000
	}
});

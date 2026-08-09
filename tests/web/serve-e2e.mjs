// Playwright webServer command: start the mock HA, then the built jarvis-web
// app (node build) pointed at it. Dies with its children.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { startMockHA, MOCK_TOKEN } from './mock-ha.mjs';

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'jarvis-web');
const mock = await startMockHA({ log: console.log });

const child = spawn(process.execPath, ['build'], {
	cwd: webRoot,
	stdio: 'inherit',
	env: {
		...process.env,
		PORT: process.env.PORT ?? '8199',
		HOST: '127.0.0.1',
		// Exercise the jarvis-core path: JARVIS_* must win over HA_*, so the
		// deliberately-wrong HA vars below double as a precedence regression test
		// — if resolveBackend picked them, every e2e test would fail to connect.
		JARVIS_BACKEND: 'core',
		JARVIS_URL: mock.url,
		JARVIS_TOKEN: MOCK_TOKEN,
		HA_URL: 'http://127.0.0.1:1',
		HA_TOKEN: 'wrong-token',
		JARVIS_PIPELINE: 'Jarvis',
		JARVIS_TTS_VOICE: 'en_GB-alan-medium'
	}
});

const shutdown = async () => {
	child.kill('SIGTERM');
	await mock.close();
	process.exit(0);
};
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, shutdown);
child.on('exit', (code) => {
	mock.close();
	process.exit(code ?? 0);
});

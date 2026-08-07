// Replaces adapter-node's build/index.js launcher with one that also attaches
// the /ws Home Assistant WebSocket proxy, so `node build` (Docker CMD) serves
// both HTTP (via the SvelteKit handler) and the authenticated WS relay.
import { copyFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const build = join(root, 'build');

copyFileSync(join(root, 'server', 'ws-proxy.js'), join(build, 'ws-proxy.js'));

writeFileSync(
	join(build, 'index.js'),
	`import http from 'node:http';
import { handler } from './handler.js';
import { attachWsProxy } from './ws-proxy.js';

const port = Number(process.env.PORT ?? 3000);
const host = process.env.HOST ?? '0.0.0.0';

const server = http.createServer(handler);
attachWsProxy(server);

server.listen(port, host, () => {
	console.log(\`jarvis-web listening on http://\${host}:\${port}\`);
});

for (const sig of ['SIGINT', 'SIGTERM']) {
	process.on(sig, () => {
		server.close(() => process.exit(0));
		setTimeout(() => process.exit(0), 3000).unref();
	});
}
`
);

console.log('postbuild: installed ws-proxy launcher into build/');

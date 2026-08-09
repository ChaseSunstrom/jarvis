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

// adapter-node serves static/ with sirv, which types files through mrmime —
// and mrmime has no entry for '.ico', so /favicon.ico went out with a literally
// empty Content-Type. Browsers sniff their way past that; proxies and Safari
// are less forgiving. sirv keeps a Content-Type that is already on the
// response (see its send(): \`if (tmp = res.getHeader('content-type'))\`), so
// setting it before the handler runs is the entire fix. vite.config.ts does
// the same for \`vite dev\`.
const server = http.createServer((req, res) => {
	if (req.url && req.url.split('?')[0].endsWith('.ico')) {
		res.setHeader('Content-Type', 'image/x-icon');
	}
	handler(req, res);
});
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

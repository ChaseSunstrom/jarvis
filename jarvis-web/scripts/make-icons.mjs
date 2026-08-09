#!/usr/bin/env node
/**
 * Write the tab icon into `static/`, from the single description in
 * `icons.mjs`.
 *
 *   npm run icons            regenerate
 *   npm run icons -- --check exit 1 if the committed files are stale
 *
 * The generated files are committed rather than built, because they belong in
 * `static/` before `vite build` runs and a favicon that only exists after a
 * build step is a favicon that is missing in `vite dev`. `--check` and
 * `src/lib/icons.test.ts` are what stop the committed copies drifting from the
 * description they came from.
 */

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { buildIcons } from './icons.mjs';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const staticDir = join(root, 'static');
const check = process.argv.includes('--check');

mkdirSync(staticDir, { recursive: true });

let stale = 0;
for (const { name, data } of buildIcons()) {
	const path = join(staticDir, name);
	const bytes = typeof data === 'string' ? Buffer.from(data, 'utf8') : data;

	if (check) {
		let current;
		try {
			current = readFileSync(path);
		} catch {
			console.error(`missing: static/${name}`);
			stale++;
			continue;
		}
		if (!current.equals(bytes)) {
			console.error(`stale: static/${name} (run \`npm run icons\`)`);
			stale++;
		}
		continue;
	}

	writeFileSync(path, bytes);
	console.log(`wrote static/${name} (${bytes.length} bytes)`);
}

if (check) {
	if (stale) process.exit(1);
	console.log('icons: up to date');
}

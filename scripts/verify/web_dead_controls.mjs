// scripts/verify/web_dead_controls.mjs — every visible control must do something.
//
// Static half of the "no dead buttons" check (the Playwright spec is the
// dynamic half): a <button> with no click handler that is not a form submit,
// and an <a> with no href, are reported. Exit 1 on any hit.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

const roots = process.argv.slice(2).length ? process.argv.slice(2) : ['jarvis-web/src'];
function walk(dir, out) {
	for (const name of readdirSync(dir)) {
		const p = join(dir, name);
		if (statSync(p).isDirectory()) {
			if (name === 'node_modules' || name === 'build' || name === '.svelte-kit') continue;
			walk(p, out);
		} else if (extname(name) === '.svelte') out.push(p);
	}
	return out;
}
const files = roots.flatMap((r) => { try { return walk(r, []); } catch { return []; } });
const hits = [];
for (const file of files) {
	const text = readFileSync(file, 'utf8');
	const tagRe = /<(button|a)\b([^>]*)>/gs;
	let m;
	while ((m = tagRe.exec(text))) {
		const [, tag, attrs] = m;
		const line = text.slice(0, m.index).split('\n').length;
		if (tag === 'button') {
			const handled = /\b(on:?click|on:?mousedown|on:?pointerdown|on:?keydown|use:)/.test(attrs);
			const submit = /type\s*=\s*["']?submit/.test(attrs) || /\bform\s*=/.test(attrs);
			const spread = /\{\.\.\./.test(attrs);
			if (!handled && !submit && !spread) hits.push(`${file}:${line}: <button> without a handler`);
		} else if (!/\bhref\s*=/.test(attrs) && !/\{\.\.\./.test(attrs)) {
			hits.push(`${file}:${line}: <a> without href`);
		}
	}
}
if (hits.length) {
	hits.forEach((h) => console.log(h));
	console.log(`\n${hits.length} dead control(s)`);
	process.exit(1);
}
console.log(`no dead controls in ${files.length} files`);

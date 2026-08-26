// Copy the motion-review recordings to docs/motion-review/, named.
//
// `e2e/motion-review.spec.ts` records four videos under test-results/, one per
// test, in Playwright's own folder-per-test layout with a random file name.
// This maps each test's folder to the name the review README lists, so the
// verify script can regenerate the recordings and they are current by
// construction.
//
//   node scripts/collect-motion-review.mjs
import { copyFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const RESULTS = 'test-results';
const OUT = '../docs/motion-review';
const NAMES = [
	['motion-review-boot', '1-boot.webm'],
	['motion-review-idle-to-listening', '2-orb-states.webm'],
	['motion-review-a-task-running', '3-task-running.webm'],
	['motion-review-moving-between-pages', '4-navigation.webm'],
	['motion-review-jarvis-at-work', '5-at-work.webm']
];

if (!existsSync(RESULTS)) {
	console.error('no test-results/ — run `npx playwright test motion-review.spec.ts` first');
	process.exit(1);
}
let copied = 0;
for (const dir of readdirSync(RESULTS)) {
	const full = join(RESULTS, dir);
	if (!statSync(full).isDirectory()) continue;
	const target = NAMES.find(([prefix]) => dir.startsWith(prefix));
	if (!target) continue;
	const video = readdirSync(full).find((name) => name.endsWith('.webm'));
	if (!video) continue;
	copyFileSync(join(full, video), join(OUT, target[1]));
	console.log(`${dir}/${video} -> ${target[1]}`);
	copied++;
}
if (!copied) {
	console.error('no recordings found under test-results/');
	process.exit(1);
}

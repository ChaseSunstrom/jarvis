import { test, expect, type Page } from '@playwright/test';
import { SCREENS, sectionsOf } from '../src/lib/screens';

/**
 * Every screen, in every state it can be driven into.
 *
 * The four states are the ones a user actually meets — loading, empty, error,
 * offline — and the failure this suite exists to stop is the one nobody writes
 * a test for: a screen that renders a blank rectangle because its data has not
 * arrived, or a stale list because the socket died an hour ago.
 *
 * How each state is driven, and why:
 *
 *   ready    the screen's own `probe` element from `src/lib/screens.ts`.
 *   loading  the relay is held open but silent, so the page is connected and
 *            has nothing yet — exactly the window a skeleton exists for.
 *   offline  the socket is closed under the page, which is what a jarvis-core
 *            restart looks like from here.
 *   empty    only on the screens the mock backend can empty (tasks, code).
 *            Emptying Devices would mean a backend with no entities at all,
 *            which the mock is not built to be, and a fake one would prove
 *            nothing about the real page.
 *
 * `error` is driven on the style guide (`styleguide.spec.ts`), where the state
 * is a control rather than a failure to manufacture.
 */

const gotoScreen = async (page: Page, path: string) => {
	await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
	await page.goto(path);
};

/**
 * The screens that OWN their states.
 *
 * A destination is a layout and a redirect to its first section (M48): it has
 * no connection of its own, so it has no offline state of its own, and driving
 * one through this test raced — its lede is static markup that appears before
 * the section beneath it has opened a socket, so the sockets were closed
 * before there were any. Its sections are all here, which is what "no screen
 * is forgotten" actually means.
 */
const STATEFUL = SCREENS.filter((screen) => sectionsOf(screen.path).length === 0);

for (const screen of STATEFUL) {
	test(`${screen.name} renders, and says so when the link drops`, async ({ page }) => {
		const sockets: { close: () => void }[] = [];
		// Once the link is "dropped" it stays dropped: a new connection is
		// closed at once instead of relayed. Closing only the sockets open at
		// that moment left the mock reachable, the client reconnected within
		// its first backoff, and whether the offline state was ever painted
		// depended on how busy the machine was — Console, Assistant and Tools
		// each failed once that way, here and on CI. A jarvis-core that has
		// gone away does not accept the reconnect either; this is that.
		let down = false;
		await page.routeWebSocket(/\/ws$/, (ws) => {
			if (down) {
				ws.close();
				return;
			}
			const server = ws.connectToServer();
			ws.onMessage((message) => server.send(message));
			server.onMessage((message) => ws.send(message));
			sockets.push(ws);
		});

		await gotoScreen(page, screen.path);
		await expect(page.getByTestId(screen.probe), `${screen.name} never became ready`).toBeVisible({
			timeout: 15_000
		});
		await expect(page.getByTestId('link-dropped')).toHaveCount(0);

		down = true;
		for (const socket of sockets.splice(0)) socket.close();

		const dropped = page.getByTestId('link-dropped');
		await expect(dropped, `${screen.name} did not report the dropped link`).toBeVisible({
			timeout: 15_000
		});
		await expect(page.getByTestId('reconnect')).toBeVisible();
	});
}

// The console screens only. The voice screen has no loading state and should
// not: it dials when you speak, so it is ready the moment it paints — and its
// own first-paint sequence is the boot animation, which the other tests skip.
// It is in the bar now (M49), so it is excluded by its `hud` flag, not by `nav`.
for (const screen of STATEFUL.filter((s) => (s.within || s.nav) && !s.hud)) {
	test(`${screen.name} shows it is loading rather than a blank`, async ({ page }) => {
		// Connected, and told nothing: the exact window a skeleton is for.
		await page.routeWebSocket(/\/ws$/, () => {
			/* held open, never answered */
		});
		await gotoScreen(page, screen.path);
		// Either a skeleton, or the screen's own boot/connecting chrome — what is
		// forbidden is an empty page with no sign that anything is coming.
		const busy = page.locator('[aria-busy="true"], [data-testid="skeleton"], [data-state="offline"]');
		await expect(busy.first(), `${screen.name} showed nothing while waiting`).toBeVisible({
			timeout: 15_000
		});
	});
}

test('an emptied backend gives the empty state, not a blank list', async ({ page }) => {
	await gotoScreen(page, '/tasks');
	await expect(page.getByTestId('tasks-lede')).toBeVisible({ timeout: 15_000 });
	await page.evaluate(async () => {
		const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
		await new Promise((resolve) => socket.addEventListener('open', resolve));
		socket.send(JSON.stringify({ id: 1, type: 'jarvis/test/task_reset' }));
		await new Promise((resolve) => setTimeout(resolve, 300));
		socket.close();
	});
	await page.reload();
	await expect(page.getByTestId('tasks-empty')).toBeVisible({ timeout: 15_000 });
});

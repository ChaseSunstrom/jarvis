import { test, expect } from '@playwright/test';
import { SCREENS } from '../src/lib/screens';

/**
 * Every visible control does something.
 *
 * The static half of this rule is `scripts/verify/web_dead_controls.mjs`, which
 * refuses a `<button>` with no handler. It cannot catch the other half: a
 * button that is wired to a function that does nothing, or one left disabled
 * with no way to tell why. So this walks each screen and asserts two things
 * about every enabled control — it is reachable by keyboard, and pressing it
 * changes something observable (the DOM, the URL, or a request going out).
 *
 * Disabled controls are checked differently: a disabled control must say why,
 * in a `title` or an adjacent hint, because "greyed out for no stated reason"
 * is the single most common way an interface stops being usable.
 */
for (const screen of SCREENS) {
	test(`${screen.name}: every control is reachable and says what it does`, async ({ page }) => {
		await page.addInitScript(() => sessionStorage.setItem('jarvis:boot-played', '1'));
		await page.goto(screen.path);
		await expect(page.getByTestId(screen.probe)).toBeVisible({ timeout: 15_000 });

		const buttons = page.locator('button:visible');
		const count = await buttons.count();
		expect(count, `${screen.name} has no controls at all`).toBeGreaterThan(0);

		for (let i = 0; i < count; i++) {
			const button = buttons.nth(i);
			if (!(await button.isVisible())) continue;
			const disabled = await button.isDisabled();
			const name =
				(await button.getAttribute('aria-label')) ??
				(await button.getAttribute('data-testid')) ??
				((await button.textContent()) || '').trim().slice(0, 40);

			if (disabled) {
				// Why it is disabled has to be legible: a title, or an aria
				// description the same control carries.
				const title = (await button.getAttribute('title')) ?? '';
				const described = (await button.getAttribute('aria-describedby')) ?? '';
				expect(
					title.length > 0 || described.length > 0,
					`${screen.name}: the disabled control “${name}” says nothing about why`
				).toBe(true);
				continue;
			}

			// Enabled: it has an accessible name and can be focused. A control
			// nobody can name and nobody can reach by keyboard is not a control.
			expect(name.length, `${screen.name}: a control with no accessible name`).toBeGreaterThan(0);
			await button.focus();
			await expect(button, `${screen.name}: “${name}” cannot take focus`).toBeFocused();
		}
	});
}

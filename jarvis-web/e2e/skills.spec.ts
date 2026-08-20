import { test, expect, type Page } from '@playwright/test';

/**
 * The skills page.
 *
 * ## What is worth an end-to-end test here
 *
 * One thing above all: an installed skill is instructions written by a
 * stranger, and it has to arrive switched OFF. There is no version of
 * "install this but do not do what it says", so the whole safety story is
 * that a person reads the body on this page and then presses ON. If the
 * install path ever lands a skill in the enabled state by accident, that is
 * a remote-code-execution-shaped hole in the prompt, and this file is where
 * it gets caught.
 *
 * After that: the allow-list is refused in the browser rather than after a
 * round trip (a person pasting a random repo should be told before they wait
 * for it), the body only appears when it is asked for — progressive
 * disclosure is the entire argument for the feature — and the cost line
 * moves, because "the prompt stays small" is a claim somebody should be able
 * to watch.
 */

const OPEN = { timeout: 15_000 };

async function tell(page: Page, frame: Record<string, unknown>): Promise<void> {
	await page.evaluate(
		(payload) =>
			new Promise((resolve) => {
				const ws = new WebSocket(
					`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
				);
				ws.onopen = () => ws.send(JSON.stringify({ id: 91, ...payload }));
				ws.onmessage = () => {
					ws.close();
					resolve(null);
				};
			}),
		frame
	);
}

async function openSkills(page: Page): Promise<void> {
	await page.goto('/skills');
	await expect(page.getByTestId('skills-lede')).toHaveAttribute('data-redialling', 'false', OPEN);
}

test.beforeEach(async ({ page }) => {
	await page.goto('/skills');
	await tell(page, { type: 'jarvis/test/skills_reset' });
});

test('an installed skill arrives switched off, and says why', async ({ page }) => {
	await openSkills(page);

	const state = page.getByTestId('skill-state-pdf');
	await expect(state).toHaveAttribute('data-enabled', 'false', OPEN);
	await expect(page.getByTestId('skill-disabled-pdf')).toContainText('read it');
	await expect(page.getByTestId('skill-source-pdf')).toContainText('anthropics/skills');

	await page.getByTestId('skill-toggle-pdf').click();
	await expect(state).toHaveAttribute('data-enabled', 'true', OPEN);
	await expect(page.getByTestId('skill-disabled-pdf')).toHaveCount(0);
});

test('the body is not on the page until it is asked for', async ({ page }) => {
	// Progressive disclosure, made visible: the catalogue line is always there,
	// the ~3000 characters behind it are not.
	await openSkills(page);
	await expect(page.getByTestId('skill-description-pdf')).toContainText('PDF');
	await expect(page.getByTestId('skill-body')).toHaveCount(0);

	await page.getByTestId('skill-open-pdf').click();
	const body = page.getByTestId('skill-body');
	await expect(body).toBeVisible(OPEN);
	await expect(body).toContainText('pdfplumber');

	await page.getByTestId('skill-open-pdf').click();
	await expect(page.getByTestId('skill-body')).toHaveCount(0);
});

test('a repository nobody allowed is refused in the browser', async ({ page }) => {
	// Before the round trip, so the answer is instant and the server still
	// refuses it too — see tests/test_skills.py.
	await openSkills(page);
	await page.getByTestId('skill-reference').fill('some-rando/evil-skills/skills/rm-rf');

	const problem = page.getByTestId('skill-reference-problem');
	await expect(problem).toBeVisible(OPEN);
	await expect(problem).toContainText('some-rando/evil-skills');
	await expect(page.getByTestId('skill-install')).toBeDisabled();
});

test('installing from a permitted repository lands it off, and costs nothing yet', async ({
	page
}) => {
	await openSkills(page);
	const before = await page.getByTestId('skills-cost').textContent();

	await page.getByTestId('skill-reference').fill('anthropics/skills/skills/docx');
	await expect(page.getByTestId('skill-reference-problem')).toHaveCount(0);
	await page.getByTestId('skill-install').click();

	await expect(page.getByTestId('skill-state-docx')).toHaveAttribute('data-enabled', 'false', OPEN);
	// Off means off: it has not joined the catalogue the model reads.
	await expect(page.getByTestId('skills-cost')).toHaveText(String(before), OPEN);

	await page.getByTestId('skill-toggle-docx').click();
	await expect(page.getByTestId('skill-state-docx')).toHaveAttribute('data-enabled', 'true', OPEN);
	await expect(page.getByTestId('skills-cost')).not.toHaveText(String(before), OPEN);
});

test('a skill that ships with Jarvis cannot be removed, only switched off', async ({ page }) => {
	await openSkills(page);
	await expect(page.getByTestId('skill-forget-n8n-workflows')).toHaveCount(0);
	await expect(page.getByTestId('skill-toggle-n8n-workflows')).toBeVisible();
	await expect(page.getByTestId('skill-forget-bin-night')).toBeVisible();
});

test('removing takes two presses', async ({ page }) => {
	await openSkills(page);
	const remove = page.getByTestId('skill-forget-bin-night');
	await remove.click();
	await expect(remove).toHaveText('REMOVE?', OPEN);
	await expect(page.getByTestId('skill-bin-night')).toBeVisible();

	await remove.click();
	await expect(page.getByTestId('skill-bin-night')).toHaveCount(0, OPEN);
});

test('a skill that would not load is named, not swallowed', async ({ page }) => {
	// The failure mode this replaces: a file in <config>/skills/ that silently
	// does nothing, and nobody can tell whether Jarvis read it.
	await openSkills(page);
	await expect(page.getByTestId('skill-problem-half-written')).toContainText('description');
	await expect(page.getByTestId('skill-toggle-half-written')).toHaveCount(0);
});

test('SKILLS is in the nav and reachable by its chord', async ({ page }) => {
	await page.goto('/tasks');
	await expect(page.getByTestId('tasks-lede')).toHaveAttribute('data-redialling', 'false', OPEN);
	// The boot animation swallows the first key — see e2e/code.spec.ts.
	await expect(page.getByTestId('boot')).toHaveCount(0, OPEN);
	await page.keyboard.press('g');
	await page.keyboard.press('l');
	await expect(page).toHaveURL(/\/skills$/, { timeout: 10_000 });
});

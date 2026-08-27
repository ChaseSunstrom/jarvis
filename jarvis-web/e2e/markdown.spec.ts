import { test, expect } from '@playwright/test';

/**
 * M106 — notes read as they were written. Against the built app and the mock,
 * whose "Cheap rate report" note carries headings, a list, bold, code and a
 * stray <b> that must stay text; and whose reply to "… in markdown" is a
 * heading, bold and a list.
 */

test('a markdown note opens rendered — headings, a list, bold — with raw HTML kept as text', async ({ page }) => {
	await page.goto('/notes');
	await page.getByTestId('note-row-cheap-rate-report').click();
	await expect(page.getByTestId('note-title')).toHaveText('Cheap rate report');
	const read = page.getByTestId('note-read');
	await expect(read).toBeVisible();
	await expect(read.locator('h1')).toHaveText('Cheap rate report');
	await expect(read.locator('h2')).toHaveText('Findings');
	await expect(read.locator('li').first()).toHaveText('dishwasher: 2.2 kW');
	// M113: a table with alignment, a nested list, task items — all three drawn.
	await expect(read.locator('table th')).toHaveCount(3);
	await expect(read.locator('table td').first()).toHaveText('Dishwasher');
	await expect(read.locator('table td').nth(1)).toHaveAttribute('style', /text-align:\s*right/);
	await expect(read.locator('ul ul li')).toHaveCount(2);
	await expect(read.locator('input[type=checkbox]')).toHaveCount(2);
	await expect(read.locator('input[type=checkbox]:checked')).toHaveCount(1);
	await expect(read.locator('strong')).toHaveText('cheap rate');
	await expect(read.locator('code')).toHaveText('make test');
	// `<b>not html</b>` in the note is text on the page, never an element.
	await expect(read.locator('b')).toHaveCount(0);
	await expect(read).toContainText('<b>not html</b>');
	// No textarea while reading.
	await expect(page.getByTestId('note-body')).toHaveCount(0);
});

test('EDIT shows the markdown itself; READ comes back once the edit is saved', async ({ page }) => {
	await page.goto('/notes');
	await page.getByTestId('note-row-cheap-rate-report').click();
	await page.getByTestId('note-mode-edit').click();
	const body = page.getByTestId('note-body');
	await expect(body).toHaveValue(/## Findings/);
	await expect(page.getByTestId('note-read')).toHaveCount(0);
	await body.fill('## Findings\n\n- nothing yet');
	// An unsaved edit cannot be read rendered: the button says why.
	await expect(page.getByTestId('note-mode-read')).toBeDisabled();
	await page.getByTestId('note-save').click();
	await expect(page.getByTestId('note-save')).toHaveText('SAVED');
	await page.getByTestId('note-mode-read').click();
	await expect(page.getByTestId('note-read').locator('li')).toHaveText('nothing yet');
});

test('a reply with markdown in it reads as written in the chat, once it has settled', async ({ page }) => {
	await page.goto('/?mode=chat');
	const input = page.getByTestId('chat-input');
	await input.fill('list the lights in markdown');
	await input.press('Enter');
	const bubble = page.locator('[data-role="assistant"]').last().getByTestId('chat-text');
	await expect(bubble.locator('h2')).toHaveText('Lights', { timeout: 15_000 });
	await expect(bubble.locator('strong')).toHaveText('lab lights');
	await expect(bubble.locator('li')).toHaveCount(2);
});

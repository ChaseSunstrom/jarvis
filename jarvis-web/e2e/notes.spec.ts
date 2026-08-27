import { test, expect } from '@playwright/test';

/**
 * Notes: the folder of markdown, as a page.
 *
 * What the page adds over opening the folder in an editor is the two things a
 * folder cannot do: search across every note, and the link graph. Both are
 * covered here, along with the plain business of writing one and saving it.
 */

test('every note is listed, with its tags', async ({ page }) => {
	await page.goto('/notes');
	await expect(page.getByTestId('notes-lede')).toBeVisible({ timeout: 15_000 });

	const list = page.getByTestId('notes-list');
	await expect(list).toContainText('Boiler serviced');
	// Research writes its reports here rather than into memory — a four-page
	// report in memory would be in front of every "turn the lights off".
	await expect(list).toContainText('Research — heat pumps');
});

test('a note opens, edits and saves', async ({ page }) => {
	await page.goto('/notes');
	await page.getByTestId('note-row-boiler-serviced').click();
	// A note opens READ (M106); the textarea is a click away.
	await page.getByTestId('note-mode-edit').click();

	const editor = page.getByTestId('note-editor');
	await expect(editor).toBeVisible();
	await expect(page.getByTestId('note-title')).toHaveText('Boiler serviced');
	await expect(page.getByTestId('note-body')).toHaveValue(/1\.2 bar/);

	// SAVE is disabled until something changes, so the button says something
	// true rather than inviting a no-op write to a file.
	await expect(page.getByTestId('note-save')).toBeDisabled();
	await page.getByTestId('note-body').fill('Pressure was 1.4 bar cold.');
	await expect(page.getByTestId('note-save')).toBeEnabled();
	await page.getByTestId('note-save').click();
	await expect(page.getByTestId('note-save')).toHaveText('SAVED', { timeout: 10_000 });
});

test('the link graph is shown both ways', async ({ page }) => {
	await page.goto('/notes');
	await page.getByTestId('note-row-boiler-serviced').click();
	await expect(page.getByTestId('note-links')).toContainText('heating');
});

test('search narrows to the notes that contain the words', async ({ page }) => {
	await page.goto('/notes');
	await expect(page.getByTestId('notes-list')).toBeVisible({ timeout: 15_000 });

	await page.getByTestId('notes-search').fill('heat pumps');
	await page.getByTestId('notes-search').press('Enter');
	await expect(page.getByTestId('notes-list')).toContainText('heat pumps');
	await expect(page.getByTestId('note-row-boiler-serviced')).toHaveCount(0);
});

test('a new note can be written from here', async ({ page }) => {
	await page.goto('/notes');
	await expect(page.getByTestId('notes-list')).toBeVisible({ timeout: 15_000 });

	await page.getByTestId('notes-new').click();
	await page.getByTestId('notes-new-title').fill('Gate code');
	await page.getByTestId('notes-create').click();

	await expect(page.getByTestId('note-title')).toHaveText('Gate code', { timeout: 10_000 });
	await expect(page.getByTestId('notes-list')).toContainText('Gate code');
});

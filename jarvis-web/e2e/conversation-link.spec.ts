import { expect, test } from '@playwright/test';

// M93: a conversation has a URL. The address bar follows the open thread, a
// link with `?conversation=<id>` reopens it with its transcript, and a link to
// an id the archive does not have yet starts the thread under that id — which
// is how the rig's browser transport holds one across turns.

test('the address bar follows the thread, and the link reopens it', async ({ page }) => {
	await page.goto('/');
	await page.getByTestId('text-input').fill('remember the boiler was serviced today');
	await page.getByTestId('text-send').click();
	await expect(page.getByTestId('transcript')).toContainText('boiler was serviced', { timeout: 15_000 });

	const screen = page.getByTestId('voice-screen');
	await expect(screen).not.toHaveAttribute('data-conversation-id', '', { timeout: 15_000 });
	const id = await screen.getAttribute('data-conversation-id');
	expect(id).toBeTruthy();
	await expect.poll(() => new URL(page.url()).searchParams.get('conversation')).toBe(id);

	// A fresh page, the link: the thread is back, transcript and all.
	// In chat mode, where the transcript of a reopened thread is drawn.
	await page.goto(`/?conversation=${encodeURIComponent(id!)}&mode=chat`);
	await expect(page.getByTestId('chat-panel')).toHaveAttribute('data-conversation-id', id!, { timeout: 15_000 });
	await expect(page.getByTestId('chat-panel')).toContainText('boiler was serviced', { timeout: 15_000 });
});

test('a link to a thread that has not started yet starts it under that id', async ({ page }) => {
	const wanted = `rig-thread-${Date.now()}`;
	await page.goto(`/?conversation=${wanted}`);
	await expect(page.getByTestId('voice-screen')).toHaveAttribute('data-conversation-id', wanted, { timeout: 15_000 });
	await page.getByTestId('text-input').fill('hello there');
	await page.getByTestId('text-send').click();
	await expect(page.getByTestId('transcript')).toContainText('hello there', { timeout: 15_000 });
	// Still the same thread after the turn — the client did not mint another.
	await expect(page.getByTestId('voice-screen')).toHaveAttribute('data-conversation-id', wanted);
	expect(new URL(page.url()).searchParams.get('conversation')).toBe(wanted);
});

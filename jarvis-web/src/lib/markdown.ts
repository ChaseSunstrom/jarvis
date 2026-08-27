/**
 * Markdown, rendered safely, for prose Jarvis wrote — a note, a reply, a
 * task's result, a notification's body.
 *
 * Escaped FIRST, then drawn: the text is untrusted (a note the model wrote
 * from a hostile page, a reply that quoted one), so raw HTML in it stays
 * text, and only a conservative subset becomes markup — headings, paragraphs,
 * bullet and numbered lists, bold, italic, inline and fenced code,
 * blockquotes, rules, and links to http(s) only, opened in a new tab with
 * `rel="noopener noreferrer"`. A `javascript:` or `data:` link is drawn as its
 * text. No dependency, on purpose: the console ships offline and a renderer
 * is a small thing to own and a large thing to audit in someone else's.
 *
 * What it does not do: tables, images (a remote image is a request the
 * console would make on the note's behalf), nested lists beyond one level,
 * HTML passthrough of any kind.
 */

export function escapeHtml(text: string): string {
	return text
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;');
}

const SAFE_HREF = /^https?:\/\/[^\s<>"']+$/i;

/** Inline markup on one already-escaped line: code, bold, italic, links. */
export function renderInline(escaped: string): string {
	let out = '';
	// Inline code first, so nothing inside it is read as markup.
	const parts = escaped.split(/(`[^`]+`)/g);
	for (const part of parts) {
		if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
			out += `<code>${part.slice(1, -1)}</code>`;
			continue;
		}
		let text = part;
		text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label: string, href: string) => {
			// The href was escaped with the rest; `&amp;` in a query string is
			// what an attribute wants anyway.
			if (!SAFE_HREF.test(href.replace(/&amp;/g, '&'))) return label;
			return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
		});
		text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
		text = text.replace(/(^|[^*\w])\*([^*\n]+)\*(?=[^*\w]|$)/g, '$1<em>$2</em>');
		text = text.replace(/(^|[^\w])_([^_\n]+)_(?=[^\w]|$)/g, '$1<em>$2</em>');
		out += text;
	}
	return out;
}

/** The whole text as HTML. Safe to place with `{@html}` — see the module docstring. */
export function renderMarkdown(source: string): string {
	const lines = escapeHtml(String(source ?? '').replace(/\r\n?/g, '\n')).split('\n');
	const html: string[] = [];
	let paragraph: string[] = [];
	let list: { kind: 'ul' | 'ol'; items: string[] } | null = null;
	let quote: string[] = [];
	let fence: string[] | null = null;

	const flushParagraph = () => {
		if (paragraph.length) {
			html.push(`<p>${renderInline(paragraph.join(' '))}</p>`);
			paragraph = [];
		}
	};
	const flushList = () => {
		if (list) {
			html.push(`<${list.kind}>${list.items.map((i) => `<li>${renderInline(i)}</li>`).join('')}</${list.kind}>`);
			list = null;
		}
	};
	const flushQuote = () => {
		if (quote.length) {
			html.push(`<blockquote>${renderInline(quote.join(' '))}</blockquote>`);
			quote = [];
		}
	};
	const flushAll = () => {
		flushParagraph();
		flushList();
		flushQuote();
	};

	for (const raw of lines) {
		const line = raw.replace(/\s+$/, '');
		if (fence !== null) {
			if (/^```/.test(line)) {
				html.push(`<pre><code>${fence.join('\n')}</code></pre>`);
				fence = null;
			} else {
				fence.push(line);
			}
			continue;
		}
		if (/^```/.test(line)) {
			flushAll();
			fence = [];
			continue;
		}
		if (!line.trim()) {
			flushAll();
			continue;
		}
		const heading = /^(#{1,6})\s+(.*)$/.exec(line);
		if (heading) {
			flushAll();
			const level = Math.min(heading[1].length, 6);
			html.push(`<h${level}>${renderInline(heading[2].trim())}</h${level}>`);
			continue;
		}
		if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
			flushAll();
			html.push('<hr>');
			continue;
		}
		const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
		const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
		if (bullet || numbered) {
			flushParagraph();
			flushQuote();
			const kind: 'ul' | 'ol' = bullet ? 'ul' : 'ol';
			if (!list || list.kind !== kind) {
				flushList();
				list = { kind, items: [] };
			}
			list.items.push((bullet ?? numbered)![1].trim());
			continue;
		}
		const quoted = /^&gt;\s?(.*)$/.exec(line);
		if (quoted) {
			flushParagraph();
			flushList();
			quote.push(quoted[1]);
			continue;
		}
		if (list && /^\s{2,}\S/.test(raw)) {
			// A wrapped list item continues on an indented line.
			list.items[list.items.length - 1] += ' ' + line.trim();
			continue;
		}
		flushList();
		flushQuote();
		paragraph.push(line.trim());
	}
	if (fence !== null) html.push(`<pre><code>${fence.join('\n')}</code></pre>`);
	flushAll();
	return html.join('\n');
}

/** Whether the text carries any markup worth rendering — a plain sentence is left as it is. */
export function looksLikeMarkdown(source: string): boolean {
	const s = String(source ?? '');
	return /(^|\n)\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|```|&gt;|>\s)|\*\*|`|\[[^\]]+\]\([^)]+\)/.test(s);
}

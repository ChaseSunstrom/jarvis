/**
 * Markdown, rendered completely and safely (M106, M113).
 *
 * Everything the model, a note or a page can reasonably write: headings
 * (`#` and setext), paragraphs with hard breaks, bullet and numbered lists
 * nested by indentation, task lists, blockquotes (nested), fenced and
 * indented code, GFM tables with alignment, rules, and the inline set —
 * code, bold, italic, strikethrough, links and autolinks. The operator's
 * report of 27 Aug 2026: "tables and stuff isn't rendering correctly as
 * markdown, make sure markdown renders completely".
 *
 * Safety first, in this order: the source is HTML-escaped BEFORE any
 * structure is read, so raw HTML in the text stays text; links are drawn
 * only for http(s) targets, opened in a new tab with no referrer; an image
 * is drawn as a link to itself, never fetched — a remote image is a
 * request the reader never agreed to make. What this does not do: raw
 * HTML passthrough, footnotes, math.
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

function link(href: string, label: string): string {
	if (!SAFE_HREF.test(href.replace(/&amp;/g, '&'))) return label;
	return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
}

/** Inline markup on one already-escaped line: code, links, emphasis, strikethrough, breaks. */
export function renderInline(escaped: string): string {
	let out = '';
	const parts = escaped.split(/(`+[^`]+`+)/g);
	for (const part of parts) {
		if (/^`+[^`]+`+$/.test(part)) {
			out += `<code>${part.replace(/^`+|`+$/g, '')}</code>`;
			continue;
		}
		let text = part;
		// An image is a link to itself: `![alt](src)` → the alt, linked.
		text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (_m, alt: string, src: string) =>
			link(src, alt || src)
		);
		text = text.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (_m, label: string, href: string) =>
			link(href, label)
		);
		// Autolinks: `<https://…>` (escaped) and bare URLs.
		text = text.replace(/&lt;(https?:\/\/[^&\s]+)&gt;/gi, (_m, href: string) => link(href, href));
		text = text.replace(/(^|[\s(])((?:https?:\/\/)[^\s<>"'&)]+[^\s<>"'&).,;:!?])/gi, (_m, pre: string, href: string) =>
			`${pre}${link(href, href)}`
		);
		text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
		text = text.replace(/__([^_]+)__/g, '<strong>$1</strong>');
		text = text.replace(/~~([^~]+)~~/g, '<del>$1</del>');
		text = text.replace(/(^|[^*\w])\*([^*\n]+)\*(?=[^*\w]|$)/g, '$1<em>$2</em>');
		text = text.replace(/(^|[^\w])_([^_\n]+)_(?=[^\w]|$)/g, '$1<em>$2</em>');
		out += text;
	}
	// A hard break: two spaces or a backslash at the end of a line.
	return out.replace(/(?: {2,}|\\)\n/g, '<br>\n');
}

// --- blocks ------------------------------------------------------------------

interface Block {
	html: string;
}

const FENCE = /^(`{3,}|~{3,})\s*([\w+-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^(?:-\s*){3,}$|^(?:\*\s*){3,}$|^(?:_\s*){3,}$/;
const BULLET = /^(\s*)([-*+])\s+(.*)$/;
const NUMBERED = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;
const TASK = /^\[([ xX])\]\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

function cells(line: string): string[] {
	let row = line.trim();
	if (row.startsWith('|')) row = row.slice(1);
	if (row.endsWith('|') && !row.endsWith('\\|')) row = row.slice(0, -1);
	return row.split(/(?<!\\)\|/).map((c) => c.replace(/\\\|/g, '|').trim());
}

function alignments(sep: string): (string | null)[] {
	return cells(sep).map((c) => {
		const left = c.startsWith(':');
		const right = c.endsWith(':');
		return left && right ? 'center' : right ? 'right' : left ? 'left' : null;
	});
}

/** The blocks of an escaped, line-split document. Recursive for lists and quotes. */
function renderBlocks(lines: string[]): string {
	const html: string[] = [];
	let i = 0;
	let paragraph: string[] = [];
	const flushParagraph = () => {
		if (paragraph.length) {
			html.push(`<p>${renderInline(paragraph.join('\n'))}</p>`);
			paragraph = [];
		}
	};
	while (i < lines.length) {
		const line = lines[i].replace(/\s+$/, '');
		// Blank: paragraph boundary.
		if (!line.trim()) {
			flushParagraph();
			i += 1;
			continue;
		}
		// Fenced code.
		const fence = FENCE.exec(line);
		if (fence) {
			flushParagraph();
			const close = new RegExp(`^${fence[1][0]}{${fence[1].length},}\\s*$`);
			const body: string[] = [];
			i += 1;
			while (i < lines.length && !close.test(lines[i])) {
				body.push(lines[i]);
				i += 1;
			}
			i += 1;
			const lang = fence[2] ? ` class="language-${fence[2]}"` : '';
			html.push(`<pre><code${lang}>${body.join('\n')}</code></pre>`);
			continue;
		}
		// ATX heading.
		const heading = HEADING.exec(line);
		if (heading) {
			flushParagraph();
			html.push(`<h${heading[1].length}>${renderInline(heading[2])}</h${heading[1].length}>`);
			i += 1;
			continue;
		}
		// Setext heading: a paragraph line followed by === or ---.
		if (paragraph.length === 1 && /^(=+|-+)\s*$/.test(line)) {
			const level = line.trim().startsWith('=') ? 1 : 2;
			html.push(`<h${level}>${renderInline(paragraph[0])}</h${level}>`);
			paragraph = [];
			i += 1;
			continue;
		}
		// Rule.
		if (RULE.test(line.trim()) && !paragraph.length) {
			html.push('<hr>');
			i += 1;
			continue;
		}
		// Table: a header row, a separator row, then body rows.
		if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && cells(lines[i + 1]).length >= 1) {
			flushParagraph();
			const head = cells(line);
			const align = alignments(lines[i + 1]);
			const width = head.length;
			const attr = (c: number) => (align[c] ? ` style="text-align:${align[c]}"` : '');
			let out = '<table><thead><tr>';
			head.forEach((h, c) => (out += `<th${attr(c)}>${renderInline(h)}</th>`));
			out += '</tr></thead>';
			i += 2;
			const rows: string[] = [];
			while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
				const row = cells(lines[i]);
				while (row.length < width) row.push('');
				rows.push(`<tr>${row.slice(0, width).map((c, k) => `<td${attr(k)}>${renderInline(c)}</td>`).join('')}</tr>`);
				i += 1;
			}
			if (rows.length) out += `<tbody>${rows.join('')}</tbody>`;
			out += '</table>';
			html.push(out);
			continue;
		}
		// Blockquote: consecutive `>` lines, rendered recursively.
		if (/^&gt;/.test(line)) {
			flushParagraph();
			const inner: string[] = [];
			while (i < lines.length && /^&gt;/.test(lines[i])) {
				inner.push(lines[i].replace(/^&gt; ?/, ''));
				i += 1;
			}
			html.push(`<blockquote>${renderBlocks(inner)}</blockquote>`);
			continue;
		}
		// Lists: items at one indent, with their indented continuation lines
		// (nested lists, wrapped text) rendered recursively.
		const bullet = BULLET.exec(line);
		const numbered = NUMBERED.exec(line);
		if (bullet || numbered) {
			flushParagraph();
			const kind = bullet ? 'ul' : 'ol';
			const indent = (bullet ?? numbered)![1].length;
			const start = numbered ? Number(numbered[2]) : 1;
			const items: string[] = [];
			while (i < lines.length) {
				const b = BULLET.exec(lines[i]);
				const n = NUMBERED.exec(lines[i]);
				const m = b ?? n;
				if (!m || m[1].length !== indent || (b ? 'ul' : 'ol') !== kind) break;
				const first = m[3];
				const body: string[] = [];
				i += 1;
				// Continuation: deeper-indented lines, and blank lines followed by one.
				while (i < lines.length) {
					const next = lines[i];
					if (!next.trim()) {
						if (i + 1 < lines.length && /^\s+\S/.test(lines[i + 1]) && leading(lines[i + 1]) > indent) {
							body.push('');
							i += 1;
							continue;
						}
						break;
					}
					if (leading(next) > indent) {
						body.push(next.slice(Math.min(leading(next), indent + 2)));
						i += 1;
						continue;
					}
					break;
				}
				const task = TASK.exec(first);
				// Plain continuation lines (no blank, no block mark) are the item's
				// own wrapped text; anything else is blocks inside the item.
				const plain = body.length > 0 && body.every((l) => l.trim() && !isBlockStart(l));
				const text = plain ? `${first}\n${body.map((l) => l.trim()).join('\n')}` : first;
				const label = task
					? `<input type="checkbox" disabled${task[1] === ' ' ? '' : ' checked'}> ${renderInline(text.replace(TASK, '$2'))}`
					: renderInline(text);
				const rest = body.length && !plain ? renderBlocks(body) : '';
				items.push(`<li${task ? ' class="task"' : ''}>${label}${rest}</li>`);
			}
			const attrs = kind === 'ol' && start !== 1 ? ` start="${start}"` : '';
			html.push(`<${kind}${attrs}>${items.join('')}</${kind}>`);
			continue;
		}
		// Indented code (four spaces), outside a list.
		if (/^ {4}|^\t/.test(lines[i]) && !paragraph.length) {
			const body: string[] = [];
			while (i < lines.length && (/^ {4}|^\t/.test(lines[i]) || !lines[i].trim())) {
				body.push(lines[i].replace(/^ {4}|^\t/, ''));
				i += 1;
			}
			while (body.length && !body[body.length - 1].trim()) body.pop();
			html.push(`<pre><code>${body.join('\n')}</code></pre>`);
			continue;
		}
		paragraph.push(lines[i].replace(/^\s+/, ''));
		i += 1;
	}
	flushParagraph();
	return html.join('\n');
}

function isBlockStart(line: string): boolean {
	return (
		BULLET.test(line) ||
		NUMBERED.test(line) ||
		FENCE.test(line.trim()) ||
		HEADING.test(line.trim()) ||
		/^\s*&gt;/.test(line) ||
		(line.includes('|') && /\|.*\|/.test(line)) ||
		RULE.test(line.trim())
	);
}

function leading(line: string): number {
	return line.length - line.replace(/^\s+/, '').length;
}

/** Whole-document markdown → HTML. Escaped first; see the module doc. */
export function renderMarkdown(source: string): string {
	const text = escapeHtml(String(source ?? '').replace(/\r\n?/g, '\n').replace(/\t/g, '    '));
	return renderBlocks(text.split('\n'));
}

/** Whether a text is worth rendering: any block or inline mark, or a table. */
export function looksLikeMarkdown(source: string): boolean {
	const text = String(source ?? '');
	return /(^|\n)\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s?|```|~~~|\|.*\|)|\*\*|__|~~|`|\[[^\]]+\]\([^)]+\)|https?:\/\//.test(text);
}

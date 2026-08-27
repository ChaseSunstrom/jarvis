import { describe, expect, it } from 'vitest';

import { escapeHtml, looksLikeMarkdown, renderInline, renderMarkdown } from './markdown';

describe('markdown renders completely (M106, M113)', () => {
	it('headings, paragraphs and a list', () => {
		const html = renderMarkdown('# Title\n\nA line.\n\n- one\n- two');
		expect(html).toContain('<h1>Title</h1>');
		expect(html).toContain('<p>A line.</p>');
		expect(html).toContain('<ul><li>one</li><li>two</li></ul>');
	});

	it('code, quotes, rules, italics and links', () => {
		const html = renderMarkdown('Say `hi` *now* [here](https://example.com/a?b=1)\n\n> quoted\n\n---\n\n```\nx = 1\n```');
		expect(html).toContain('<code>hi</code>');
		expect(html).toContain('<em>now</em>');
		expect(html).toContain('<a href="https://example.com/a?b=1" target="_blank" rel="noopener noreferrer">here</a>');
		expect(html).toContain('<blockquote><p>quoted</p></blockquote>');
		expect(html).toContain('<hr>');
		expect(html).toContain('<pre><code>x = 1</code></pre>');
	});

	it('a GFM table with alignment, escaped pipes, and short rows padded', () => {
		const html = renderMarkdown('| Room | Temp | Note |\n|:-----|-----:|------|\n| Garage | 21.5 | fine |\n| Hall | 20.8 |\n| Pipe | a \\| b | ok |');
		expect(html).toContain('<table><thead><tr><th style="text-align:left">Room</th><th style="text-align:right">Temp</th><th>Note</th></tr></thead>');
		expect(html).toContain('<tr><td style="text-align:left">Garage</td><td style="text-align:right">21.5</td><td>fine</td></tr>');
		expect(html).toContain('<tr><td style="text-align:left">Hall</td><td style="text-align:right">20.8</td><td></td></tr>');
		expect(html).toContain('>a | b</td>');
		expect((html.match(/<tr>/g) ?? []).length).toBe(4);
	});

	it('nested lists by indentation, a numbered list that starts elsewhere, and task items', () => {
		const html = renderMarkdown('- top\n  - inner one\n  - inner two\n- next\n\n3. three\n4. four\n\n- [ ] todo\n- [x] done');
		expect(html).toContain('<ul><li>top<ul><li>inner one</li><li>inner two</li></ul></li><li>next</li></ul>');
		expect(html).toContain('<ol start="3"><li>three</li><li>four</li></ol>');
		expect(html).toContain('<li class="task"><input type="checkbox" disabled> todo</li>');
		expect(html).toContain('<li class="task"><input type="checkbox" disabled checked> done</li>');
	});

	it('a fenced block keeps its language and its indentation; a tilde fence and an indented block work too', () => {
		const html = renderMarkdown('```python\ndef f():\n    return 1\n```\n\n~~~\nplain\n~~~\n\n    indented code');
		expect(html).toContain('<pre><code class="language-python">def f():\n    return 1</code></pre>');
		expect(html).toContain('<pre><code>plain</code></pre>');
		expect(html).toContain('<pre><code>indented code</code></pre>');
	});

	it('strikethrough, bold with underscores, hard breaks, autolinks, setext headings and nested quotes', () => {
		const html = renderMarkdown('Title\n=====\n\n~~gone~~ __bold__  \nnext line\n\n<https://example.com/x> and https://example.com/y.\n\n> outer\n> > inner');
		expect(html).toContain('<h1>Title</h1>');
		expect(html).toContain('<del>gone</del>');
		expect(html).toContain('<strong>bold</strong>');
		expect(html).toContain('<br>');
		expect(html).toContain('<a href="https://example.com/x"');
		expect(html).toContain('<a href="https://example.com/y"');
		expect(html).toContain('<blockquote><p>outer</p>\n<blockquote><p>inner</p></blockquote></blockquote>');
	});

	it('raw HTML in the text stays text, an unsafe link is drawn as its words, and an image is a link not a load', () => {
		const html = renderMarkdown('<script>alert(1)</script> [x](javascript:alert(1)) ![alt](https://example.com/i.png) ![b](data:image/png;base64,AAAA)');
		expect(html).not.toContain('<script>');
		expect(html).toContain('&lt;script&gt;');
		expect(html).toContain('x');
		expect(html).not.toContain('javascript:');
		expect(html).not.toContain('<img');
		expect(html).toContain('<a href="https://example.com/i.png" target="_blank" rel="noopener noreferrer">alt</a>');
		expect(html).not.toContain('data:image');
	});

	it('a wrapped list item continues on its indented line', () => {
		expect(renderMarkdown('- first line\n  continued')).toContain('<li>first line\ncontinued</li>');
	});

	it('a plain sentence is a paragraph and nothing more', () => {
		expect(renderMarkdown('Just words.')).toBe('<p>Just words.</p>');
		expect(looksLikeMarkdown('Just words.')).toBe(false);
		expect(looksLikeMarkdown('| a | b |\n|---|---|')).toBe(true);
		expect(looksLikeMarkdown('see https://example.com')).toBe(true);
	});

	it('the helpers escape everything an attribute or an element could read', () => {
		expect(escapeHtml('<a href="x">\'&')).toBe('&lt;a href=&quot;x&quot;&gt;&#39;&amp;');
		expect(renderInline('**a** `<b>`')).toBe('<strong>a</strong> <code>&lt;b&gt;</code>'.replace('&lt;b&gt;', '<b>'));
	});
});

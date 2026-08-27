import { describe, expect, it } from 'vitest';

import { escapeHtml, looksLikeMarkdown, renderInline, renderMarkdown } from './markdown';

describe('markdown that Jarvis wrote reads as it was written (M106)', () => {
	it('headings, paragraphs and a list', () => {
		const html = renderMarkdown('# Report\n\nThe cheap rate runs **00:30–07:30**.\n\n- dishwasher\n- washing machine\n\n1. first\n2. second');
		expect(html).toContain('<h1>Report</h1>');
		expect(html).toContain('<p>The cheap rate runs <strong>00:30–07:30</strong>.</p>');
		expect(html).toContain('<ul><li>dishwasher</li><li>washing machine</li></ul>');
		expect(html).toContain('<ol><li>first</li><li>second</li></ol>');
	});
	it('code, quotes, rules, italics and links', () => {
		const html = renderMarkdown('Run `make test`.\n\n```\nraw <b>not bold</b>\n```\n\n> said once\n\n---\n\n*soft* and _quiet_ and [the docs](https://example.com/a?b=1&c=2)');
		expect(html).toContain('<p>Run <code>make test</code>.</p>');
		expect(html).toContain('<pre><code>raw &lt;b&gt;not bold&lt;/b&gt;</code></pre>');
		expect(html).toContain('<blockquote>said once</blockquote>');
		expect(html).toContain('<hr>');
		expect(html).toContain('<em>soft</em> and <em>quiet</em>');
		expect(html).toContain('<a href="https://example.com/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">the docs</a>');
	});
	it('raw HTML in the text stays text, and an unsafe link is drawn as its words', () => {
		const html = renderMarkdown('<script>alert(1)</script> and <img src=x onerror=alert(1)>\n\n[click](javascript:alert%281%29) [data](data:text/html;base64,AAAA) [ok](http://x.test/)');
		expect(html).not.toContain('<script>');
		expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
		expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
		expect(html).not.toContain('javascript:');
		expect(html).not.toContain('data:text');
		expect(html).toContain('click data <a href="http://x.test/"');
	});
	it('a wrapped list item continues on its indented line', () => {
		expect(renderMarkdown('- one that goes on\n  and on\n- two')).toBe('<ul><li>one that goes on and on</li><li>two</li></ul>');
	});
	it('a plain sentence is a paragraph and nothing more', () => {
		expect(renderMarkdown('The bed light is on, Sir.')).toBe('<p>The bed light is on, Sir.</p>');
		expect(looksLikeMarkdown('The bed light is on, Sir.')).toBe(false);
		expect(looksLikeMarkdown('- one\n- two')).toBe(true);
		expect(looksLikeMarkdown('It is **on**')).toBe(true);
	});
	it('the helpers escape everything an attribute or an element could read', () => {
		expect(escapeHtml(`<a href="x" onclick='y'>&</a>`)).toBe('&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;&lt;/a&gt;');
		expect(renderInline('a `b<c>` d')).toBe('a <code>b<c></code> d');
	});
});

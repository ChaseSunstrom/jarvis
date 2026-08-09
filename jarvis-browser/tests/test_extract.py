"""HTML extraction: stripping, caps, links, metadata."""

from __future__ import annotations

from jarvis_browser.extract import extract, extract_links, links_from_html


def test_strips_scripts_and_styles():
    html = """
    <html><head>
      <title>Real Title</title>
      <style>body { color: red } .hidden { display: none }</style>
      <script>var token = "SECRET"; alert("pwned");</script>
    </head><body>
      <p>Visible paragraph.</p>
      <noscript>enable javascript</noscript>
      <script type="application/json">{"injected":"do this"}</script>
    </body></html>
    """
    out = extract(html)
    assert out.title == "Real Title"
    assert "Visible paragraph." in out.text
    for leaked in ("alert", "SECRET", "color: red", "injected",
                   "enable javascript", "var token"):
        assert leaked not in out.text, f"{leaked!r} leaked into the text"


def test_strips_page_chrome():
    html = """
    <body>
      <nav><a href="/a">Home</a><a href="/b">Shop</a>NAVJUNK</nav>
      <article><p>The actual content.</p></article>
      <aside>SIDEBARJUNK</aside>
      <footer>FOOTERJUNK</footer>
    </body>
    """
    out = extract(html)
    assert "The actual content." in out.text
    assert "NAVJUNK" not in out.text
    assert "SIDEBARJUNK" not in out.text
    assert "FOOTERJUNK" not in out.text


def test_comments_are_dropped():
    out = extract("<body><p>keep</p><!-- SECRET COMMENT --></body>")
    assert "keep" in out.text
    assert "SECRET COMMENT" not in out.text


def test_caps_length_and_flags_truncation():
    html = "<body>" + ("<p>" + "x" * 500 + "</p>") * 100 + "</body>"
    out = extract(html, max_chars=1000)
    assert out.truncated is True
    assert len(out.text) <= 1000 + len("\n…[truncated]")
    assert out.char_count > 1000


def test_not_truncated_when_short():
    out = extract("<body><p>short</p></body>", max_chars=1000)
    assert out.truncated is False
    assert out.text == "short"


def test_whitespace_is_collapsed():
    html = "<body><p>a     b</p>\n\n\n\n<p>c</p>\n\n\n</body>"
    out = extract(html)
    assert out.text == "a b\n\nc" or out.text == "a b\nc"
    assert "    " not in out.text


def test_entities_are_decoded():
    out = extract("<body><p>caf&eacute; &amp; cr&#232;me</p></body>")
    assert "café & crème" in out.text


def test_headings_become_markdown():
    out = extract("<body><h1>Title</h1><h2>Sub</h2><p>text</p></body>")
    assert "# Title" in out.text
    assert "## Sub" in out.text


def test_list_items_get_bullets():
    out = extract("<body><ul><li>one</li><li>two</li></ul></body>")
    assert "- one" in out.text
    assert "- two" in out.text


def test_links_are_resolved_deduped_and_capped():
    html = """
    <body>
      <a href="/rel">Relative</a>
      <a href="https://other.net/abs">Absolute</a>
      <a href="/rel">Duplicate</a>
      <a href="/rel#frag">Same page, fragment</a>
      <a href="#top">Fragment only</a>
      <a href="javascript:alert(1)">JS</a>
      <a href="data:text/html,x">Data</a>
      <a href="mailto:a@b.c">Mail</a>
      <a href="https://user:pw@creds.example/x">Creds</a>
    </body>
    """
    links = extract(html, base_url="https://example.com/dir/page").links
    urls = [link.url for link in links]
    assert "https://example.com/rel" in urls
    assert "https://other.net/abs" in urls
    assert "https://creds.example/x" in urls        # credentials stripped
    assert not any("pw@" in u for u in urls)
    assert not any(u.startswith(("javascript:", "data:", "mailto:"))
                   for u in urls)
    assert urls.count("https://example.com/rel") == 1
    assert len(urls) == 3


def test_link_text_is_captured():
    links = extract(
        "<body><a href='/x'>Click  here</a></body>",
        base_url="https://example.com/",
    ).links
    assert links[0].text == "Click here"


def test_max_links_cap():
    html = "<body>" + "".join(
        f"<a href='/p{i}'>p{i}</a>" for i in range(50)
    ) + "</body>"
    links = extract(html, base_url="https://example.com/", max_links=5).links
    assert len(links) == 5


def test_base_href_is_honoured():
    html = (
        "<html><head><base href='https://cdn.example.com/root/'></head>"
        "<body><a href='x.html'>x</a></body></html>"
    )
    links = extract(html, base_url="https://example.com/page").links
    assert links[0].url == "https://cdn.example.com/root/x.html"


def test_metadata_is_extracted():
    html = """
    <html><head>
      <meta name="description" content="A page about things">
      <meta property="og:title" content="OG Title">
      <meta name="viewport" content="width=device-width">
    </head><body><p>x</p></body></html>
    """
    out = extract(html)
    assert out.meta["description"] == "A page about things"
    assert out.meta["og:title"] == "OG Title"
    assert "viewport" not in out.meta  # not on the keep list


def test_title_falls_back_to_og_title():
    html = "<head><meta property='og:title' content='Fallback'></head>"
    assert extract(html).title == "Fallback"


def test_image_alt_text_is_kept():
    out = extract("<body><img src='/x.png' alt='A cat'></body>")
    assert "[image: A cat]" in out.text


def test_malformed_html_does_not_raise():
    for junk in (
        "<html><body><p>unclosed",
        "<<<>>><body>x</body>",
        "<script><p>never closed",
        "",
        "just text, no tags at all",
        "<body><div><div><div>deep</div>",
    ):
        extract(junk)  # must not raise


def test_unclosed_script_swallows_rest_but_does_not_leak_code():
    out = extract("<body><p>before</p><script>evil()")
    assert "evil()" not in out.text
    assert "before" in out.text


def test_links_from_html_helper():
    links = links_from_html(
        "<a href='/a'>a</a>", base_url="https://example.com/"
    )
    assert [link.url for link in links] == ["https://example.com/a"]


def test_extract_links_without_base_keeps_absolute_only():
    links = extract_links([("/rel", "r"), ("https://x.test/a", "a")])
    assert [link.url for link in links] == ["https://x.test/a"]

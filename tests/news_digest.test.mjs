import test from "node:test";
import assert from "node:assert/strict";
import { parseRss, formatDigest } from "../news_digest.mjs";

const XML = `<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>Belgique : nouvelle hausse des prix</title>
    <link>https://example.be/a</link>
    <pubDate>Mon, 23 Nov 2026 08:00:00 GMT</pubDate>
    <source url="https://example.be">Example</source>
  </item>
  <item>
    <title>Leader du secteur)</title>
    <link>https://example.be/b</link>
    <source url="https://example.be">Example</source>
  </item>
  <item>
    <title>Belgique : nouvelle hausse des prix</title>
    <link>https://example.be/c</link>
    <source url="https://example.be">Example</source>
  </item>
</channel></rss>`;

test("parseRss extrait les titres, liens et sources, sans doublon", () => {
  const items = parseRss(XML);
  assert.equal(items.length, 2);
  assert.equal(items[0].title, "Belgique : nouvelle hausse des prix");
  assert.equal(items[0].link, "https://example.be/a");
  assert.equal(items[0].source, "Example");
});

test("formatDigest ajoute le titre, les liens et le disclaimer", () => {
  const text = formatDigest(parseRss(XML));
  assert.match(text, /📰.*actualité Belgique/);
  assert.match(text, /https:\/\/example\.be\/a/);
  assert.match(text, /pas un conseil en investissement/);
});

test("formatDigest gère un flux vide sans message", () => {
  assert.equal(formatDigest([]), "");
});

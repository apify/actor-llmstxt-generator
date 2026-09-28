# 🗂️ /llms.txt Generator

[![Agent Actor Inspector](https://apify.com/actor-badge?actor=jakub.kopecky/llmstxt-generator)](https://apify.com/jakub.kopecky/llmstxt-generator)
[![GitHub Repo stars](https://img.shields.io/github/stars/apify/actor-llmstxt-generator)](https://github.com/apify/actor-llmstxt-generator/stargazers)

Turn any documentation site into an [llms.txt](https://llmstxt.org/) file in a few seconds. Paste a URL and it crawls the site, curates the pages, and writes a Markdown index — plus an optional **llms-full.txt** with the full content of every page — that AI coding agents can read instead of your whole site.

## 🌟 What is llms.txt?

A Markdown file that tells AI tools where a website's useful content is: a title, a one-line summary, and sections of links with short descriptions. Coding agents and AI IDEs (Claude Code, Cursor, Windsurf, GitHub Copilot) read it when pointed at a documentation site. There's no evidence search engines or chat assistants use it for ranking or citations — treat it as context for agents, not SEO.

## ✨ What it does

- Crawls from your start URL, following redirects, ordered the way the site's own navigation lists pages
- Cleans up titles and descriptions, and drops duplicate pages
- Groups pages into sections, with a spec-compliant `Optional` section for changelogs, blog posts and old doc versions
- Links to a page's Markdown version when the site publishes one, so agents read it faster and cheaper
- Can also write `llms-full.txt` with the full text of every page
- Can hand off to an AI model to organize sections by topic instead of URL structure
- Tells you if the site already publishes its own `llms.txt`

## 🚀 How to use it

1. Enter the **Website address**.
2. Click **Start**. Download `llms.txt` (and `llms-full.txt`) from the **Output** tab when it finishes.

Everything else lives under **Advanced settings**, with defaults tuned for most documentation sites: up to 100 pages, `llms-full.txt` on, AI organizing off.

### Output example

This is the `llms.txt` generated for `https://docs.apify.com/cli/docs`, shortened:

```markdown
# Apify CLI overview | CLI | Apify Documentation

> An introduction to Apify CLI, a command-line interface for creating, developing, building, and running Apify Actors and managing the Apify cloud platform.

## Docs

- [Quick start](https://docs.apify.com/cli/docs/quick-start.md): Learn how to create, run, and deploy Actors using Apify CLI.
- [Installation](https://docs.apify.com/cli/docs/installation.md): Learn how to install Apify CLI using installation scripts, Homebrew, or NPM.

## Optional

- [Changelog](https://docs.apify.com/cli/docs/changelog.md): All notable changes to this project will be documented in this file.
```

The run also saves a dataset item with links to both files, the crawl stats, and the site's existing `llms.txt` URL if it has one.

## 🤖 AI curation (optional)

Turn on **Organize with AI** to have an LLM group pages by what a developer wants to do ("Getting started", "API reference") instead of URL structure, and write a real summary. It scales to large sites by batching the request, costs about $0.001 per 100 pages on a paid Apify plan (10x on the free plan), and never pushes the run past its timeout — if it fails or runs out of time, you still get the regular URL-based file.

## ⚠️ Limitations

- **No JavaScript rendering.** Sites fully rendered in the browser (e.g. Docsify) fail with a clear explanation instead of an empty file.
- **Large sites take longer, but always finish.** Bigger crawls (up to 5,000 pages) automatically get more memory; if a run is about to hit its timeout, it saves whatever it has and says so.
- **Descriptions come from the site.** Pages without a meta description get a title only.
- **Treat the output as a first draft.** Review it, trim what your users don't need, before publishing at `https://your-site.com/llms.txt`.

## 💡 Tips

- Start from the docs root, not the marketing homepage — documentation pages have better titles and descriptions.
- Use **Skip pages** to leave out old versions, translations or generated API pages.
- Only enable the proxy if the site blocks the crawler — most documentation sites don't need it, and skipping it is faster.

## 📖 Resources

- [llms.txt proposal](https://llmstxt.org/)
- [Source code on GitHub](https://github.com/apify/actor-llmstxt-generator)
- [Crawlee for Python](https://crawlee.dev/python)

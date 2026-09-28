# /llms.txt Generator

[![Agent Actor Inspector](https://apify.com/actor-badge?actor=jakub.kopecky/llmstxt-generator)](https://apify.com/jakub.kopecky/llmstxt-generator)
[![GitHub Repo stars](https://img.shields.io/github/stars/apify/actor-llmstxt-generator)](https://github.com/apify/actor-llmstxt-generator/stargazers)

Generate an [llms.txt](https://llmstxt.org/) file for any documentation site in a few seconds. Give it a start URL and it crawls the pages under it, then writes a Markdown index of the pages with their titles and descriptions, grouped into sections. It can also write **llms-full.txt** with the full content of every page.

## What is llms.txt?

llms.txt is a Markdown file that tells AI tools where the useful content of a website is. It has a title, a one-line summary, and sections of links, each with a short description. An `Optional` section at the end lists pages an agent can skip when it is short on context.

Most of the value today is in developer tooling. Coding agents and AI IDEs such as Claude Code, Cursor, Windsurf and GitHub Copilot read `llms.txt` and `llms-full.txt` when you point them at a documentation site. There is no evidence that search engines or AI chat assistants use llms.txt for ranking or citations, so treat it as context for agents, not as SEO.

## What this Actor does

- **Crawls the site from your start URL.** It stays under the start URL's path and follows redirects. For example, `docs.pydantic.dev/latest/` redirects to `pydantic.dev/docs/validation/latest/`, and the Actor keeps crawling there.
- **Picks pages in navigation order.** Pages are ordered the way the site's menus and sidebars list them, so the most prominent pages come first and the output is the same on every run.
- **Cleans up titles and descriptions.** It uses the page heading as the title and strips repeated site-name suffixes such as `| Supabase Docs`. It skips meta descriptions that are Markdown dumps, contain leaked HTML or repeat the same site-wide text on every page.
- **Removes duplicates.** It drops copies of the same page, for example old versions of the docs.
- **Groups pages into sections.** Sections follow the site's URL structure. Changelogs, blog posts, legal pages and old documentation versions go to the `Optional` section.
- **Links to Markdown when available.** Many documentation sites publish a Markdown version of each page, such as `/docs/page.md`. When a page advertises one, the Actor links to it, because agents read Markdown faster and with fewer tokens.
- **Writes llms-full.txt.** This file contains the full text of all pages in one Markdown file. It uses each page's Markdown version when there is one, and converts the HTML otherwise.
- **Can curate with AI (optional).** An LLM groups the pages by what a developer wants to do, such as "Getting started" or "API reference". It also writes a factual summary and moves secondary pages to `Optional`.
- **Tells you if the site already has one.** When the site already publishes `/llms.txt`, the Actor links it in the output, so you can compare.

## How to use it

1. Enter the **Website address**, usually the root of the documentation, such as `https://docs.apify.com/cli/docs`.
2. Click **Start**. When the run finishes, download `llms.txt` and `llms-full.txt` from the **Output** tab.

Everything else is under **Advanced settings** and has defaults that work for most documentation sites: up to 100 pages, the pages linked from the start page (on most docs sites, the whole sidebar), `llms-full.txt` on, AI off.

### Input example

```json
{
  "startUrl": "https://docs.apify.com/cli/docs",
  "maxCrawlDepth": 1,
  "maxCrawlPages": 100,
  "excludeUrlGlobs": ["*/docs/1.*"],
  "generateLlmsFullTxt": true,
  "aiCuration": false
}
```

### Output example

This is the `llms.txt` generated for `https://docs.apify.com/cli/docs`, shortened:

```markdown
# Apify CLI overview | CLI | Apify Documentation

> An introduction to Apify CLI, a command-line interface for creating, developing, building, and running Apify Actors and managing the Apify cloud platform.

## Docs

- [Quick start](https://docs.apify.com/cli/docs/quick-start.md): Learn how to create, run, and deploy Actors using Apify CLI.
- [Installation](https://docs.apify.com/cli/docs/installation.md): Learn how to install Apify CLI using installation scripts, Homebrew, or NPM.
- [Environment variables](https://docs.apify.com/cli/docs/vars.md): Learn how to define environment variables for your Actors using the Apify CLI.
- [Command reference](https://docs.apify.com/cli/docs/reference.md): The Apify CLI provides tools for managing your Apify projects and resources from the command line.

## Optional

- [Changelog](https://docs.apify.com/cli/docs/changelog.md): All notable changes to this project will be documented in this file.
```

The run also saves a dataset item with links to both files, the number of crawled pages and links, and the URL of the site's existing `llms.txt` if there is one.

## AI curation

Without AI, sections follow the site's URL structure. That works well for most documentation, but a large site can end up with one long section. With **Organize with AI** turned on, the Actor sends the page titles, paths and descriptions to an LLM through the [OpenRouter Actor](https://apify.com/apify/openrouter). The LLM returns the section structure, the summary and the list of secondary pages. It only rearranges the pages the Actor found and cannot add new links. Pages already recognized as secondary by URL alone (changelogs, blog posts, old documentation versions, ...) are never sent to the model; they go straight to `Optional`.

- **Scales to large sites.** The OpenRouter proxy caps every response at 2,048 tokens, so a single request can't safely place thousands of pages at once. Above 60 pages, the Actor splits them into batches (10 at a time). One small extra call then picks the final sections for the whole site, and a few more assign each section a batch proposed to one of them. A request whose answer still doesn't fit is split in half and retried. Small sites use a single call.
- **Cost:** The LLM usage is billed to your Apify account at OpenRouter prices, plus a 10x markup for Apify's free plan (paid plans pay the raw OpenRouter price). With the default model it's about $0.001 per 100 pages on a paid plan (measured: ~1,300 pages cost under $0.01), so even the 5,000-page maximum stays well under $0.10.
- **Model:** The default is `qwen/qwen3-30b-a3b-instruct-2507`, an open-weight (Apache 2.0) model chosen for cheap, reliable, non-reasoning JSON output. You can also pick `qwen/qwen3-235b-a22b-2507` or `openai/gpt-4.1-mini` (about 4x the price). The list is limited to tested non-reasoning models: a reasoning model can spend the 2,048-token response budget on hidden reasoning before writing the JSON, and an expensive model could turn a large site into a surprising bill.
- **Time limit:** The AI step gets its share of the run time and never pushes the run past its timeout. If it doesn't finish in time, the Actor saves the file organized by URL paths instead.
- **Fallback:** If any AI call fails, or its response doesn't parse, the Actor keeps the structure based on URL paths, so you still get a file.

AI curation works only when the Actor runs on the Apify platform.

## Limitations

- **No JavaScript rendering.** The Actor downloads plain HTML and doesn't run a browser, which keeps it fast and cheap. Sites rendered fully in the browser, such as Docsify sites, have no links in their HTML. On those sites the run fails and explains why, instead of producing an empty file.
- **Large sites take a while.** Runs of up to 300 pages use 256 MB of memory, larger crawls automatically get more (1 GB up to 1,500 pages, 4 GB above), because on Apify more memory also means more CPU. Measured speeds: 256 MB crawls about 10-40 pages a minute (heavy pages such as FastAPI's docs are the slow end), 4 GB about 120-220 pages a minute (1,731 pages of docs.apify.com in 8 minutes). The maximum is 5,000 pages. You can always set the memory yourself in the run options.
- **The run always finishes with a result.** Close to the run timeout, the Actor stops crawling and saves what it has, and the status message says the crawl was cut short. Increase the timeout (or the memory) to crawl more.
- **`llms-full.txt` has a size limit per run.** It may use up to 15% of the run's memory (about 38 MB at 256 MB). Pages beyond that are left out of `llms-full.txt` with a note at the end of the file; `llms.txt` still lists them. Run with more memory to include them.
- **Descriptions come from the site.** Link descriptions are the pages' meta descriptions. Pages without a meta description get a title only.
- **Treat the output as a first draft.** A good llms.txt is short and curated. Review the generated file, remove what your users don't need, and add a sentence or two of context before you publish it at `https://your-site.com/llms.txt`.

## Tips

- **Start from the docs root, not the marketing homepage.** Documentation pages have better titles and descriptions.
- **Keep the default page limit.** 100 pages is usually enough for a useful index. Raise it for large sites, and use **Skip pages** (`excludeUrlGlobs`) to leave out old versions, translations or generated API pages.
- **Enable Apify Proxy only when you need it.** Most documentation sites work without a proxy, and crawling without one is faster. Enable **Apify Proxy** in the input if the site blocks the crawler.

## Resources

- [llms.txt proposal](https://llmstxt.org/)
- [Source code on GitHub](https://github.com/apify/actor-llmstxt-generator)
- [Crawlee for Python](https://crawlee.dev/python)

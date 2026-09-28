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

1. Enter the **Start URL**, usually the root of the documentation, such as `https://docs.apify.com/cli/docs`.
2. Optionally change **Max crawl depth** and **Max crawl pages**. With the default depth of 1, the Actor crawls the pages linked from the start page, which on most docs sites is the whole sidebar.
3. Click **Start**. When the run finishes, download `llms.txt` and `llms-full.txt` from the **Output** tab.

### Input example

```json
{
  "startUrl": "https://docs.apify.com/cli/docs",
  "maxCrawlDepth": 1,
  "maxCrawlPages": 50,
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

Without AI, sections follow the site's URL structure. That works well for most documentation, but a large site can end up with one long section. With **Curate with AI** turned on, the Actor sends the page titles, paths and descriptions to an LLM through the [OpenRouter Actor](https://apify.com/apify/openrouter). The LLM returns the section structure, the summary and the list of secondary pages. It only rearranges the pages the Actor found and cannot add new links.

- **Cost:** The LLM usage is billed to your Apify account at OpenRouter prices. For about 50 pages on a paid plan, this is usually well under $0.01 per run. Free plans pay more per token.
- **Model:** The default is `openai/gpt-4.1-mini`. You can use any [OpenRouter model ID](https://openrouter.ai/models).
- **Fallback:** If the AI call fails, the Actor keeps the structure based on URL paths, so you still get a file.

AI curation works only when the Actor runs on the Apify platform.

## Limitations

- **No JavaScript rendering.** The Actor downloads plain HTML and doesn't run a browser, which keeps it fast and cheap. Sites rendered fully in the browser, such as Docsify sites, have no links in their HTML. On those sites the run fails and explains why, instead of producing an empty file.
- **Descriptions come from the site.** Link descriptions are the pages' meta descriptions. Pages without a meta description get a title only.
- **Treat the output as a first draft.** A good llms.txt is short and curated. Review the generated file, remove what your users don't need, and add a sentence or two of context before you publish it at `https://your-site.com/llms.txt`.

## Tips

- **Start from the docs root, not the marketing homepage.** Documentation pages have better titles and descriptions.
- **Keep the default page limit.** 50 pages is usually enough for a useful index. Raise it for large sites, and use `excludeUrlGlobs` to skip old versions, translations or generated API pages.
- **Enable Apify Proxy only when you need it.** Most documentation sites work without a proxy, and crawling without one is faster. Enable **Apify Proxy** in the input if the site blocks the crawler.

## Resources

- [llms.txt proposal](https://llmstxt.org/)
- [Source code on GitHub](https://github.com/apify/actor-llmstxt-generator)
- [Crawlee for Python](https://crawlee.dev/python)

# llms.txt generator 🚀📄

The **llms.txt generator** is an Apify Actor that helps you extract essential website content and generate an [llms.txt](https://llmstxt.org/) file, making your content ready for AI-powered applications such as fine-tuning, indexing, and integrating large language models (LLMs) like GPT-4, ChatGPT, or LLaMA. This Actor leverages the [Website Content Crawler](https://apify.com/apify/website-content-crawler) actor to perform deep crawls and extract text content from web pages, ensuring comprehensive data collection. The Website Content Crawler is particularly useful because it supports output in multiple formats, including markdown, which is used by the **llms.txt**.

## 🌟 What is llms.txt?

The **llms.txt** format is a markdown-based standard for providing AI-friendly content. It contains:

- **Brief background information** and guidance.
- **Links to additional resources** in markdown format.
- **AI-focused** structure to help coders, researchers, and AI models easily access and use website content.

Proposed structure:

```
# Title

> Optional description

Optional details go here

## Section name

- [Link title](https://link_url): Optional link details

## Optional

- [Link title](https://link_url)
```

By adding an **llms.txt** file to your website, you make it easy for AI systems to understand, index, and use your content effectively.

---

## 🎯 Features of llms.txt generator

Our Actor is designed to simplify and automate the creation of **llms.txt** files. Here are its key features:

- **Deep website crawling**: Extracts content from multi-level websites using the powerful [Crawlee](https://crawlee.dev) library and the [Website Content Crawler](https://apify.com/apify/website-content-crawler) Actor.
- **Content extraction**: Retrieves key metadata such as titles, descriptions, and URLs for seamless integration.
- **File generation**: Saves the output in the standardized **llms.txt** format.
- **Downloadable output**: The **llms.txt** file can be downloaded from the **key-value store** in the storage section of the Actor run details.
- **Resource management**: Limits the crawler Actor to 4 GB of memory to ensure compatibility with the free tier, which has an 8 GB limit. Note that this may slow down the crawling process.

---

## 🚀 How it works

1. **Input**: Provide the start URL of the website to crawl.
2. **Configuration**: Set the maximum crawl depth and other options (optional).
3. **Output**: The Actor generates a structured **llms.txt** file with extracted content, ready for AI applications.

### Input example

```json
{
  "startUrl": "https://docs.apify.com",
  "maxCrawlDepth": 1
}
```

### Output example (llms.txt)

```
# docs.apify.com

## Index

- [Home | Platform | Apify Documentation](https://docs.apify.com/platform): Apify is your one-stop shop for web scraping, data extraction, and RPA. Automate anything you can do manually in a browser.
- [Web Scraping Academy | Academy | Apify Documentation](https://docs.apify.com/academy): Learn everything about web scraping and automation with our free courses that will turn you into an expert scraper developer.
- [Apify Documentation](https://docs.apify.com/api)
- [API scraping | Academy | Apify Documentation](https://docs.apify.com/academy/api-scraping): Learn all about how the professionals scrape various types of APIs with various configurations, parameters, and requirements.
- [API client for JavaScript | Apify Documentation](https://docs.apify.com/api/client/js/)
- [Apify API | Apify Documentation](https://docs.apify.com/api/v2)
- [API client for Python | Apify Documentation](https://docs.apify.com/api/client/python/)
...

```


---

## ✨ Why use llms.txt generator?

- **Save time**: Automates the tedious process of extracting, formatting, and organizing web content.
- **Boost AI performance**: Provides clean, structured data for LLMs and AI-powered tools.
- **Future-proof**: Follows a standardized format that’s gaining adoption in the AI community.
- **User-friendly**: Easy integration into customer-facing products, allowing users to generate **llms.txt** files effortlessly.

---

## 🔧 Technical highlights

- Built on the [Apify SDK](https://docs.apify.com/sdk/python), leveraging state-of-the-art web scraping tools.
- Designed to handle JavaScript-heavy websites using headless browsers.
- Equipped with anti-scraping features like proxy rotation and browser fingerprinting.
- Extensible for custom use cases.

---

## 📖 Learn more

- [Apify platform](https://apify.com)
- [Apify SDK documentation](https://docs.apify.com/sdk/python)
- [Crawlee library](https://crawlee.dev)
- [llms.txt proposal](https://llmstxt.org/)

---

Start generating **llms.txt** files today and empower your AI applications with clean, structured, and AI-friendly data! 🌐🤖

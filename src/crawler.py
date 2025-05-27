from apify import ProxyConfiguration
from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext

from src.helpers import get_description_from_soup, get_h1_from_soup, is_description_suitable
from src.mytypes import CrawledPage

STATUS_CODE_OK = 200


async def run_crawler(
    url: str, max_crawl_depth: int = 1, max_crawl_pages: int = 50, proxy: ProxyConfiguration | None = None
) -> list[CrawledPage]:
    """Runs the crawler and returns the results."""
    results: list[CrawledPage] = []

    async def request_handler(context: BeautifulSoupCrawlingContext) -> None:
        links = await context.extract_links()
        links = [link for link in links if link.url.startswith(url)]
        await context.add_requests(links)

        context.log.info(f'Processing {context.request.url}...')
        status_code = context.http_response.status_code
        if status_code != STATUS_CODE_OK:
            context.log.warning(f'Failed to fetch {context.request.url} with status code {status_code}')
            return

        title = get_h1_from_soup(context.soup) or context.soup.title.string if context.soup.title else None
        if not title:
            context.log.warning(f'No title found for {context.request.url}')
            return
        description = get_description_from_soup(context.soup)
        data: CrawledPage = {
            'url': context.request.url,
            'title': title,
            'description': description if is_description_suitable(description) else None,
        }
        results.append(data)

    crawler_kwargs = {
        'request_handler': request_handler,
        'max_crawl_depth': max_crawl_depth,
        'max_requests_per_crawl': max_crawl_pages,
    }
    if proxy is not None:
        crawler_kwargs['proxy_configuration'] = proxy

    crawler = BeautifulSoupCrawler(**crawler_kwargs)  # type: ignore[arg-type]
    await crawler.run([url])

    return results

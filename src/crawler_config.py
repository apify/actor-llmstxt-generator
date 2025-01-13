CRAWLER_CONFIG = {
    'htmlTransformer': 'none',
    'keepElementsCssSelector': 'meta[name="description"],meta[name="Description"]\ntitle',
    # changed by get_crawler_actor_config with default value 1
    'maxCrawlDepth': 0,  # 0 by default for root page only just in case
    'saveHtmlAsFile': True,
    'startUrls': [
        # is populated by get_crawler_actor_config
    ],
    'useSitemaps': False,
}

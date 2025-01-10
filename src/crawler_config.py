CRAWLER_CONFIG = {
    'aggressivePrune': False,
    'clickElementsCssSelector': '[aria-expanded="False"]',
    'clientSideMinChangePercentage': 15,
    'crawlerType': 'playwright:adaptive',
    'debugLog': False,
    'debugMode': False,
    'expandIframes': True,
    'htmlTransformer': 'none',
    'ignoreCanonicalUrl': False,
    'keepElementsCssSelector': 'meta[name="description"],meta[name="Description"]\ntitle',
    'keepUrlFragments': False,
    # changed by get_crawler_actor_config with defailt value 1
    'maxCrawlDepth': 0,  # 0 by default for root page only just in case
    'proxyConfiguration': {'useApifyProxy': True},
    'readableTextCharThreshold': 100,
    'removeCookieWarnings': True,
    'renderingTypeDetectionPercentage': 10,
    'saveFiles': False,
    'saveHtml': False,
    'saveHtmlAsFile': True,
    'saveMarkdown': False,
    'saveScreenshots': False,
    'startUrls': [
        # is populated by get_crawler_actor_config
    ],
    'useSitemaps': False,
}

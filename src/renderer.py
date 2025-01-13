def render(data: dict) -> str:
    """Generates llms.txt file from the provided data.

    Example data:
    {
        'title': 'Example',
        'description': 'Example description',
        'details': 'Example details',
        'sections': [
            {
                'title': 'Section 1',
                'links': [
                    {'url': 'https://example.com', 'title': 'Example', 'description': 'Example description'},
                ],
            },
        ],
    }
    Example output:
    # Example

    > Example description

    Example details

    ## Section 1

    - [Example](https://example.com): Example description

    """
    result = f"# {data['title']}\n\n"

    if data.get('description'):
        result += f"> {data['description']}\n\n"

    if data.get('details'):
        result += f"{data['details']}\n\n"

    for section in data.get('sections', []):
        result += f"## {section['title']}\n\n"
        for link in section.get('links', []):
            result += f"- [{link['title']}]({link['url']})"
            if link.get('description'):
                result += f": {link['description']}"
            result += '\n'

    return result

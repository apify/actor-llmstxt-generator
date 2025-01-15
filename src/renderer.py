from src.mytypes import LLMSData


def render_llms_txt(data: LLMSData) -> str:
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
    result = [f"# {data['title']}\n\n"]

    if data.get('description'):
        result.append(f"> {data['description']}\n\n")

    if data.get('details'):
        result.append(f"{data['details']}\n\n")

    for section in data.get('sections', []):
        result.append(f"## {section['title']}\n\n")
        for link in section.get('links', []):
            link_str = f"- [{link['title']}]({link['url']})"
            if link.get('description'):
                link_str += f": {link['description']}"
            result.append(f'{link_str}\n')

    return ''.join(result)

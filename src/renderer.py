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
    result = [f"# {data['title'].strip()}\n\n"]

    if description := data.get('description'):
        result.append(f'> {description.strip()}\n\n')

    if details := data.get('details'):
        result.append(f'{details.strip()}\n\n')

    for section_dir in sorted(data.get('sections', {})):
        section = data['sections'][section_dir]
        result.append(f"## {section['title'].strip()}\n\n")
        for link in section.get('links', []):
            link_str = f"- [{link['title'].strip()}]({link['url'].strip()})"
            if link_description := link.get('description'):
                link_str += f': {link_description.strip()}'
            result.append(f'{link_str}\n')
        result.append('\n')

    return ''.join(result)

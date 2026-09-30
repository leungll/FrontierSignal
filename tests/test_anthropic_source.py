from radar.sources.anthropic import parse_featured

# Shape of the page's embedded data: JSON inside a JS string, so quotes escaped.
_HTML = (
    'x{\\"_key\\":\\"a\\",\\"_type\\":\\"featuredGridLink\\",\\"date\\":\\"2026-09-28\\",'
    '\\"subject\\":\\"Announcements\\",\\"summary\\":\\"Faster and cheaper.\\",'
    '\\"title\\":\\"Introducing Claude Sonnet 5.5\\",\\"url\\":\\"/claude-sonnet-5-5\\"}y'
    '{\\"_key\\":\\"b\\",\\"_type\\":\\"footerLink\\",\\"title\\":\\"Careers\\",\\"url\\":\\"/careers\\"}'
    '{\\"_key\\":\\"a\\",\\"_type\\":\\"featuredGridLink\\",\\"date\\":\\"2026-09-28\\",'
    '\\"title\\":\\"Introducing Claude Sonnet 5.5\\",\\"url\\":\\"/claude-sonnet-5-5\\"}'
)


def test_parse_featured_extracts_launches_only_once():
    entries = parse_featured(_HTML)
    assert [e["title"] for e in entries] == ["Introducing Claude Sonnet 5.5"]
    assert entries[0]["url"] == "/claude-sonnet-5-5"
    assert entries[0]["summary"] == "Faster and cheaper."

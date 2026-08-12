from radar.canonical import canonicalize


def test_strips_tracking_params_and_www():
    a = canonicalize("https://www.example.com/post?utm_source=x&ref=y")
    b = canonicalize("https://example.com/post")
    assert a == b


def test_arxiv_abs_pdf_and_version_collapse():
    ids = [
        "https://arxiv.org/abs/2601.12345",
        "https://arxiv.org/abs/2601.12345v3",
        "https://arxiv.org/pdf/2601.12345v1",
    ]
    keys = {canonicalize(u) for u in ids}
    assert keys == {"arxiv:2601.12345"}


def test_trailing_slash_and_scheme_normalized():
    assert canonicalize("http://example.com/a/") == canonicalize("https://example.com/a")


def test_query_order_stable():
    assert canonicalize("https://x.com/p?b=2&a=1") == canonicalize("https://x.com/p?a=1&b=2")

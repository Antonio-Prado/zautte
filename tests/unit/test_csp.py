from api import csp


def test_inline_script_gets_a_hash_and_external_does_not():
    html = '<script src="a.js"></script><script>var a = 1;</script><script type="application/ld+json">{}</script>'
    assert csp.inline_script_hashes(html) == [csp.script_hash("var a = 1;")]


def test_closing_tag_with_attributes_ends_the_script():
    html = "<script>var a = 1;</script foo><p>text</p><script>var b = 2;</script>"
    assert len(csp.inline_script_hashes(html)) == 2


def test_policy_has_no_unsafe_inline_for_scripts():
    p = csp.policy("<script>x()</script>")
    script_src = next(part for part in p.split(";") if part.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src
    assert csp.script_hash("x()") in script_src

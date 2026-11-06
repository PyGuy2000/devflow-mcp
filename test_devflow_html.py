"""Static checks on devflow.html view switching and theme escaping."""
import os
import re

HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "devflow.html"), encoding="utf-8").read()


def _body(name):
    m = re.search(r"function %s\(.*?\n}\n" % name, HTML, re.S)
    assert m, name
    return m.group(0)


def test_filter_project_applies_view_mode_before_render():
    body = _body("filterProject")
    assert "applyViewMode()" in body
    assert body.index("applyViewMode()") < body.index("render()")


def test_theme_row_escapes_id_verdict_kind():
    body = _body("buildThemeRow")
    assert "escapeHtml(r.id)" in body
    assert "escapeHtml(r.kind === 'theme' ? r.verdict : r.kind)" in body
    assert "${r.id}</span>" not in body


def test_theme_detail_escapes_id_verdict_kind():
    body = _body("renderThemeDetail")
    assert "escapeHtml(r.id)" in body and "escapeHtml(r.kind)" in body
    assert "escapeHtml(r.verdict)" in body
    assert "${r.id} ·" not in body

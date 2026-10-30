"""Code block highlighting: a per-line regex scanner, state across lines."""

from moka_code.ui.tui.components.markdown import Markdown
from moka_code.ui.tui.syntax_highlight import highlight_line, scan


def _kinds(line, lang, state=""):
    return {text: kind for text, kind in highlight_line(line, lang, state) if kind != "plain"}


def test_python_covers_numbers_constants_types_and_decorators():
    assert _kinds("@dataclass", "python") == {"@dataclass": "decorator"}
    assert _kinds("class Foo(Base):", "py") == {"class": "keyword", "Foo": "type", "Base": "type"}
    assert _kinds("    x: int = 0x1F  # note", "python") == {
        "int": "type", "=": "operator", "0x1F": "number", "# note": "comment"}
    assert _kinds("return MAX, None, run(3.5e2)", "python") == {
        "return": "keyword", "MAX": "number", "None": "number", "run": "function", "3.5e2": "number"}


def test_strings_and_comments_carry_across_lines():
    segments, state = scan('    """Doc it\'s', "python")
    assert segments[-1][1] == "string" and state == 's"""'
    assert _kinds('    more""" + x', "python", state) == {'    more"""': "string", "+": "operator"}
    _, state = scan("a = 1 /* open", "js")
    assert state == "c*/"
    assert _kinds(" */ foo(1)", "js", state) == {" */": "comment", "foo": "function", "1": "number"}


def test_no_language_is_prose_and_a_lone_quote_is_not_a_string():
    """The ``What's your name`` output block of a fence with no language."""
    assert highlight_line("What's your name? Joe", "") == [("What's your name? Joe", "plain")]
    assert "string" not in [k for _, k in highlight_line("it's done", "python")]


def test_aliases_resolve_and_css_is_not_c():
    assert _kinds("const x = 1", "ts")["const"] == "keyword"
    assert _kinds("echo $HOME", "sh") == {"echo": "keyword", "$HOME": "variable"}
    assert _kinds("color: #fff;", "css") == {"color": "key", "#fff": "number"}


def test_rust_lifetimes_macros_and_attributes():
    kinds = _kinds("#[derive(Debug)] fn f<'a>(s: &'a str) { println!(\"{}\", 'c') }", "rs")
    assert kinds["#[derive(Debug)]"] == "decorator"
    assert kinds["'a"] == "type" and kinds["'c'"] == "string"
    assert kinds["println!"] == "function"


def test_data_keys_and_diffs():
    assert _kinds('{"name": "moka", "ok": true}', "json") == {
        '"name"': "key", '"moka"': "string", '"ok"': "key", "true": "number"}
    assert _kinds("  - port: 8080 # c", "yaml") == {"port": "key", "8080": "number", "# c": "comment"}
    assert highlight_line("[ui]", "toml") == [("[ui]", "keyword")]
    assert [highlight_line(line, "diff")[0][1] for line in ("@@ -1 @@", "+new", "-old", " ctx")] == [
        "hunk", "added", "removed", "plain"]


def test_markdown_passes_the_open_state_down_a_code_block():
    lines = Markdown().parse('```python\n"""doc\nstill doc\n"""\nx = 1\n```')
    code = [line[1:] for line in lines if line and line[0].indent]   # past the prefix
    still = [seg for seg in code[1] if seg.text.strip()][0]
    one = [seg for seg in code[3] if seg.text == "1"][0]
    assert still.text == "still doc"
    assert still.fg == code[0][0].fg   # still the string opened above
    assert one.fg != still.fg


def test_operators_but_not_punctuation():
    """2026-10-30: operators take the keyword color; brackets, commas, dots and
    a lone ``:`` stay plain; a shell flag's ``-`` is not an operator."""
    assert _kinds("x == y -> z && !w", "rs") == {"==": "operator", "->": "operator",
                                                  "&&": "operator", "!": "operator"}
    assert _kinds("def f(a, b): return a.b", "python") == {
        "def": "keyword", "f": "function", "return": "keyword"}
    assert _kinds("std::vec", "cpp") == {"::": "operator"}
    assert _kinds("ls -la | grep x > out", "sh") == {"|": "operator", ">": "operator"}
    assert "operator" not in _kinds('{"a": 1}', "json").values()

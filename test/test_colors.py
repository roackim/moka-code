from moka_code.ui.tui import colors
from moka_code.ui.tui.components.box import theme as imported_box_theme


def test_set_theme_updates_existing_theme_imports():
    original_name = colors.theme.name
    try:
        active_theme = colors.theme
        colors.set_theme("pastel")

        assert colors.theme is active_theme
        assert imported_box_theme is active_theme
        assert imported_box_theme.name == "pastel"
        assert imported_box_theme.ERROR is colors.pastel.ERROR

        colors.set_theme("missing")
        assert colors.theme.name == "terminal"
    finally:
        colors.set_theme(original_name)
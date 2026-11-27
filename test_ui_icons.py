"""Every icon name the templates and app.js use must have its own CSS rule.

An undefined ``icon-*`` class used to render as a solid square (the mask had no image),
which is what the "Compare Prices" button and the Alternatives popup showed.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent


class IconDefinitionTests(unittest.TestCase):
    def test_every_used_icon_is_defined(self):
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        defined = set(re.findall(r"^\.icon-([a-z0-9-]+)\s*\{\s*--icon:", css, re.M))

        js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        sources = [js] + [p.read_text(encoding="utf-8") for p in (ROOT / "templates").glob("*.html")]
        used = set()
        for text in sources:
            used.update(m for m in re.findall(r"\bicon-([a-z0-9]+(?:-[a-z0-9]+)*)\b", text))
            # Names passed in as data: infoCard("Uses", "plus", ...), panel(...), and the index category tuples.
            used.update(re.findall(r'(?:infoCard|panel)\("[^"]+",\s*"([a-z-]+)"', text))
            used.update(re.findall(r'\("[^"]+",\s*"([a-z-]+)",\s*"[\d,]+"\)', text))
        # Classes that are layout helpers, not glyphs.
        used -= {"btn", "mark"}

        missing = sorted(name for name in used if name not in defined)
        self.assertEqual(missing, [], "icon classes used without a CSS definition")

    def test_undefined_icon_falls_back_to_nothing(self):
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        block = re.search(r"^\.icon \{(.*?)\}", css, re.M | re.S).group(1)
        self.assertIn("--icon:", block)


if __name__ == "__main__":
    unittest.main()

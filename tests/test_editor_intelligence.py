import unittest

from modules.editor_intelligence import completion_items, extract_symbols, word_prefix


class EditorIntelligenceTests(unittest.TestCase):
    def test_completion_uses_prefix_and_local_identifiers(self):
        text = "customer_total = 10\nprint(cust)"
        offset = text.index("cust)") + 4
        self.assertEqual(word_prefix(text, offset), "cust")
        self.assertIn("customer_total", completion_items(text, "sample.py", offset))

    def test_python_symbols_include_classes_methods_and_functions(self):
        symbols = extract_symbols("class Service:\n    def run(self, value):\n        return value\n\ndef main():\n    pass\n", "app.py")
        found = {(item.name, item.kind, item.line) for item in symbols}
        self.assertIn(("Service", "class", 1), found)
        self.assertIn(("run", "method", 2), found)
        self.assertIn(("main", "function", 5), found)

    def test_javascript_and_markdown_symbols(self):
        js = extract_symbols("export function build(name) {\n}\nconst save = async (id) => {\n};", "app.js")
        self.assertEqual([item.name for item in js], ["build", "save"])
        md = extract_symbols("# Overview\ntext\n## Setup", "README.md")
        self.assertEqual([(item.name, item.line) for item in md], [("Overview", 1), ("Setup", 3)])


if __name__ == "__main__":
    unittest.main()

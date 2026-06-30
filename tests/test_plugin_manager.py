import unittest

from modules.plugin_manager import (
    PluginManager,
    build_plugin_report_messages,
    initialize_plugins,
    load_installed_plugins,
    plugin_loader_error_status,
)


class _EntryPointCollection:
    def __init__(self, entry_points):
        self.entry_points = tuple(entry_points)

    def select(self, group):
        return tuple(
            entry_point
            for entry_point in self.entry_points
            if entry_point.group == group
        )


class _FakeEntryPoint:
    def __init__(self, name, plugin, group="merotec_ide.plugins", value="fake:plugin"):
        self.name = name
        self.plugin = plugin
        self.group = group
        self.value = value

    def load(self):
        if isinstance(self.plugin, BaseException):
            raise self.plugin
        return self.plugin


class PluginManagerTests(unittest.TestCase):
    def test_discover_accepts_selectable_entry_point_collections(self):
        beta = _FakeEntryPoint("beta", lambda context: None)
        alpha = _FakeEntryPoint("alpha", lambda context: None)
        ignored = _FakeEntryPoint("ignored", lambda context: None, group="other")
        manager = PluginManager(
            entry_points_provider=lambda: _EntryPointCollection([beta, ignored, alpha])
        )

        self.assertEqual(("alpha", "beta"), tuple(ep.name for ep in manager.discover()))

    def test_discover_accepts_legacy_entry_point_dicts(self):
        manager = PluginManager(
            entry_points_provider=lambda: {
                "merotec_ide.plugins": (
                    _FakeEntryPoint("one", lambda context: None),
                )
            }
        )

        self.assertEqual(("one",), tuple(ep.name for ep in manager.discover()))

    def test_loads_class_plugin_and_registers_returned_capabilities(self):
        class SamplePlugin:
            def register(self, context):
                if context.get_service("sample") != "service-value":
                    raise AssertionError("service was not passed to plugin context")
                return {"command": "run"}

        manager = PluginManager(
            services={"sample": "service-value"},
            entry_points_provider=lambda: [
                _FakeEntryPoint("sample_plugin", SamplePlugin)
            ],
        )

        statuses = manager.load_all()

        self.assertTrue(statuses[0].loaded)
        self.assertEqual("", statuses[0].error)
        self.assertEqual(
            {"sample_plugin": {"command": "run"}},
            manager.get_capabilities(),
        )
        self.assertEqual(
            {"command": "run"},
            manager.get_statuses()[0]["capabilities"],
        )

    def test_callable_plugin_can_register_capabilities_via_context(self):
        def plugin(context):
            context.register_capability("callable_plugin", "tool", object())

        manager = PluginManager(
            entry_points_provider=lambda: [
                _FakeEntryPoint("callable_plugin", plugin)
            ],
        )

        status = manager.load_all()[0]

        self.assertTrue(status.loaded)
        self.assertIn("tool", manager.get_capabilities()["callable_plugin"])

    def test_plugin_failure_is_reported_without_aborting_other_plugins(self):
        def working_plugin(_context):
            return {"ok": True}

        manager = PluginManager(
            entry_points_provider=lambda: [
                _FakeEntryPoint("broken", RuntimeError("boom")),
                _FakeEntryPoint("working", working_plugin),
            ],
        )

        statuses = manager.load_all()

        self.assertFalse(statuses[0].loaded)
        self.assertIn("RuntimeError: boom", statuses[0].error)
        self.assertTrue(statuses[1].loaded)
        self.assertEqual({"working": {"ok": True}}, manager.get_capabilities())

    def test_invalid_plugin_return_is_reported_as_status_error(self):
        manager = PluginManager(
            entry_points_provider=lambda: [
                _FakeEntryPoint("invalid", lambda _context: ["not", "a", "dict"])
            ],
        )

        status = manager.load_all()[0]

        self.assertFalse(status.loaded)
        self.assertIn("TypeError", status.error)
        self.assertEqual({}, manager.get_capabilities())

    def test_load_installed_plugins_returns_loaded_manager(self):
        manager = load_installed_plugins(services={"x": 1})

        self.assertIsInstance(manager, PluginManager)
        self.assertIsInstance(manager.get_statuses(), list)

    def test_initialize_plugins_returns_manager_statuses_and_capabilities(self):
        def plugin(_context):
            return {"tool": "ok"}

        def loader(services=None):
            manager = PluginManager(
                services=services,
                entry_points_provider=lambda: [_FakeEntryPoint("sample", plugin)],
            )
            manager.load_all()
            return manager

        manager, statuses, capabilities = initialize_plugins(
            loader=loader
        )

        self.assertTrue(statuses[0]["loaded"])
        self.assertEqual({"sample": {"tool": "ok"}}, capabilities)

    def test_initialize_plugins_reports_loader_failure_without_raising(self):
        def broken_loader(services=None):
            raise RuntimeError("entry points unavailable")

        manager, statuses, capabilities = initialize_plugins(loader=broken_loader)

        self.assertIsNone(manager)
        self.assertEqual({}, capabilities)
        self.assertEqual("plugin-loader", statuses[0]["name"])
        self.assertFalse(statuses[0]["loaded"])
        self.assertIn("RuntimeError: entry points unavailable", statuses[0]["error"])

    def test_plugin_loader_error_status_shape_is_app_compatible(self):
        status = plugin_loader_error_status(ValueError("bad plugin setup"))

        self.assertEqual(
            {
                "name": "plugin-loader",
                "value": "",
                "loaded": False,
                "error": "ValueError: bad plugin setup",
                "capabilities": {},
            },
            status,
        )

    def test_build_plugin_report_messages_compacts_loaded_and_failed_plugins(self):
        statuses = [
            {"name": "alpha", "loaded": True},
            {"name": "beta", "loaded": True},
            {"name": "broken", "loaded": False, "error": "RuntimeError: boom"},
            {"name": "silent", "loaded": False, "error": ""},
        ]

        messages = build_plugin_report_messages(statuses)

        self.assertEqual(
            ("Sistema", "Plugins carregados: alpha, beta."),
            messages[0],
        )
        self.assertEqual(
            (
                "Erro",
                "Falha ao carregar plugin: broken: RuntimeError: boom; silent: erro desconhecido",
            ),
            messages[1],
        )

    def test_build_plugin_report_messages_returns_empty_for_no_plugins(self):
        self.assertEqual([], build_plugin_report_messages([]))


if __name__ == "__main__":
    unittest.main()

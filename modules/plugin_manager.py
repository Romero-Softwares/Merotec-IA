from importlib import metadata


PLUGIN_ENTRY_POINT_GROUP = "merotec_ide.plugins"


def build_plugin_report_messages(statuses):
    statuses = list(statuses or [])
    if not statuses:
        return []

    messages = []
    loaded = [status for status in statuses if status.get("loaded")]
    failed = [status for status in statuses if not status.get("loaded")]
    if loaded:
        names = ", ".join(str(status.get("name") or "plugin") for status in loaded[:6])
        suffix = "" if len(loaded) <= 6 else f" e mais {len(loaded) - 6}"
        messages.append(("Sistema", f"Plugins carregados: {names}{suffix}."))
    if failed:
        details = "; ".join(
            f"{status.get('name') or 'plugin'}: {status.get('error') or 'erro desconhecido'}"
            for status in failed[:4]
        )
        suffix = "" if len(failed) <= 4 else f"; mais {len(failed) - 4} falha(s)"
        messages.append(("Erro", f"Falha ao carregar plugin: {details}{suffix}"))
    return messages


def plugin_loader_error_status(exc):
    return {
        "name": "plugin-loader",
        "value": "",
        "loaded": False,
        "error": f"{type(exc).__name__}: {exc}",
        "capabilities": {},
    }


def initialize_plugins(services=None, loader=None):
    try:
        loader = loader or load_installed_plugins
        manager = loader(services=services)
        return manager, manager.get_statuses(), manager.get_capabilities()
    except Exception as exc:
        return None, [plugin_loader_error_status(exc)], {}


class PluginContext:
    def __init__(self, services=None):
        self.services = dict(services or {})
        self.capabilities = {}

    def get_service(self, name, default=None):
        return self.services.get(name, default)

    def set_service(self, name, service):
        self.services[str(name)] = service

    def register_capability(self, plugin_name, capability_name, value):
        plugin_name = str(plugin_name).strip()
        capability_name = str(capability_name).strip()

        if not plugin_name:
            raise ValueError("Plugin name cannot be empty.")

        if not capability_name:
            raise ValueError("Plugin capability name cannot be empty.")

        self.capabilities.setdefault(plugin_name, {})[capability_name] = value


class PluginStatus:
    def __init__(self, name, value="", loaded=False, error=""):
        self.name = name
        self.value = value
        self.loaded = loaded
        self.error = error
        self.capabilities = {}

    def as_dict(self):
        return {
            "name": self.name,
            "value": self.value,
            "loaded": self.loaded,
            "error": self.error,
            "capabilities": dict(self.capabilities),
        }


class PluginManager:
    def __init__(
        self,
        services=None,
        entry_point_group=PLUGIN_ENTRY_POINT_GROUP,
        entry_points_provider=None,
    ):
        self.context = PluginContext(services)
        self.entry_point_group = entry_point_group
        self.entry_points_provider = entry_points_provider or metadata.entry_points
        self.statuses = []

    def discover(self):
        entry_points = self.entry_points_provider()

        if hasattr(entry_points, "select"):
            selected = entry_points.select(group=self.entry_point_group)
        elif isinstance(entry_points, dict):
            selected = entry_points.get(self.entry_point_group, ())
        else:
            selected = [
                entry_point
                for entry_point in entry_points
                if getattr(entry_point, "group", "") == self.entry_point_group
            ]

        return tuple(
            sorted(
                selected,
                key=lambda entry_point: str(getattr(entry_point, "name", "")),
            )
        )

    def load_all(self):
        self.statuses = [
            self._load_entry_point(entry_point)
            for entry_point in self.discover()
        ]
        return list(self.statuses)

    def get_capabilities(self):
        return {
            plugin_name: dict(capabilities)
            for plugin_name, capabilities in self.context.capabilities.items()
        }

    def get_statuses(self):
        return [status.as_dict() for status in self.statuses]

    def _load_entry_point(self, entry_point):
        plugin_name = str(getattr(entry_point, "name", "plugin")).strip() or "plugin"
        plugin_value = str(getattr(entry_point, "value", "")).strip()
        status = PluginStatus(plugin_name, plugin_value)

        try:
            plugin = entry_point.load()
            result = self._invoke_plugin(plugin)
            self._register_returned_capabilities(plugin_name, result)
            status.loaded = True
            status.capabilities = dict(
                self.context.capabilities.get(plugin_name, {})
            )
        except Exception as exc:
            status.error = "{}: {}".format(type(exc).__name__, exc)

        return status

    def _invoke_plugin(self, plugin):
        if isinstance(plugin, type):
            plugin = plugin()

        register = getattr(plugin, "register", None)
        if callable(register):
            return register(self.context)

        if callable(plugin):
            return plugin(self.context)

        raise TypeError(
            "Plugin must define register(context), be callable, or be a class "
            "that provides one of those interfaces."
        )

    def _register_returned_capabilities(self, plugin_name, result):
        if result is None:
            return

        if not isinstance(result, dict):
            raise TypeError(
                "A plugin registration must return a dictionary or None."
            )

        for capability_name, capability in result.items():
            self.context.register_capability(
                plugin_name,
                capability_name,
                capability,
            )


def load_installed_plugins(services=None):
    manager = PluginManager(services=services)
    manager.load_all()
    return manager

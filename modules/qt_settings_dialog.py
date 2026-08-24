"""Dialogo PySide6 para os perfis de IA da Merotec."""

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout

from modules.ai_profiles import PROFILE_DEFAULTS, PROVIDER_LABELS, PROVIDER_ORDER, activate_profile, profile_for, update_profile
from modules.video_generation import VIDEO_SETTING_DEFAULTS


class QtSettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.fields = {}
        self.codex_login_callback = getattr(parent, "launch_codex_login", None)
        self.setWindowTitle("Configuracoes da IA")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        self.provider = QComboBox()
        for value in PROVIDER_ORDER:
            self.provider.addItem(PROVIDER_LABELS[value], value)
        active = settings.get("active_ai_profile", settings.get("ai_provider", "web_chat"))
        self.provider.setCurrentIndex(max(0, self.provider.findData(active)))
        self.provider.currentIndexChanged.connect(self._rebuild_fields)
        layout.addWidget(self.provider)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.video_form = QFormLayout()
        layout.addWidget(QLabel("Geração de vídeo local (ComfyUI)"))
        layout.addLayout(self.video_form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.codex_login_button = buttons.addButton(
            "Entrar Codex",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.codex_login_button.setToolTip("Conectar a sessao desta conta no Codex CLI")
        self.codex_login_button.clicked.connect(self._launch_codex_login)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._build_video_fields()
        self._rebuild_fields()

    def _build_video_fields(self):
        self.video_fields = {}
        for key, default in VIDEO_SETTING_DEFAULTS.items():
            label = key.replace("_", " ").capitalize()
            value = self.settings.get(key, default)
            if isinstance(default, int):
                control = QSpinBox()
                control.setRange(60, 1000000)
                control.setValue(int(value or default))
            else:
                control = QLineEdit(str(value or ""))
            self.video_fields[key] = control
            self.video_form.addRow(label, control)

    def _rebuild_fields(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields = {}
        provider = self.provider.currentData()
        self.codex_login_button.setVisible(provider == "codex")
        values = profile_for(self.settings, provider)
        for key, default in PROFILE_DEFAULTS[provider].items():
            label = key.replace("_", " ").capitalize()
            value = values.get(key, default)
            if isinstance(default, bool):
                control = QCheckBox()
                control.setChecked(bool(value))
            elif isinstance(default, int):
                control = QSpinBox()
                control.setRange(0, 1000000)
                control.setValue(int(value or 0))
            else:
                control = QLineEdit(str(value or ""))
                if "key" in key:
                    control.setEchoMode(QLineEdit.EchoMode.Password)
            self.fields[key] = control
            self.form.addRow(label, control)

    def _launch_codex_login(self):
        if callable(self.codex_login_callback):
            self.codex_login_callback()
            return
        self.codex_login_button.setEnabled(False)
        self.codex_login_button.setText("Login indisponivel")

    def accept(self):
        values = {}
        for key, control in self.fields.items():
            values[key] = control.isChecked() if isinstance(control, QCheckBox) else control.value() if isinstance(control, QSpinBox) else control.text().strip()
        provider = self.provider.currentData()
        update_profile(self.settings, provider, values)
        activate_profile(self.settings, provider)
        for key, control in self.video_fields.items():
            self.settings[key] = control.value() if isinstance(control, QSpinBox) else control.text().strip()
        super().accept()

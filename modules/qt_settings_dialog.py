"""Dialogo PySide6 para os perfis de IA da Merotec."""

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox, QVBoxLayout

from modules.ai_profiles import PROFILE_DEFAULTS, PROVIDER_LABELS, PROVIDER_ORDER, activate_profile, profile_for, update_profile


class QtSettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.fields = {}
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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._rebuild_fields()

    def _rebuild_fields(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields = {}
        provider = self.provider.currentData()
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

    def accept(self):
        values = {}
        for key, control in self.fields.items():
            values[key] = control.isChecked() if isinstance(control, QCheckBox) else control.value() if isinstance(control, QSpinBox) else control.text().strip()
        provider = self.provider.currentData()
        update_profile(self.settings, provider, values)
        activate_profile(self.settings, provider)
        super().accept()

"""Dialogo PySide6 para os perfis de IA da Merotec."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout

from modules.ai_profiles import PROFILE_DEFAULTS, PROVIDER_LABELS, PROVIDER_ORDER, activate_profile, profile_for, update_profile
from modules.agent_skills import AGENT_SKILL_CHOICES
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
        self.agent_form = QFormLayout()
        layout.addWidget(QLabel("Autonomia e especialista do agente"))
        layout.addLayout(self.agent_form)
        self._build_agent_fields()
        self.video_form = QFormLayout()
        layout.addWidget(QLabel("Geração de vídeo local"))
        layout.addLayout(self.video_form)
        self._build_video_fields()
        self.codex_usage_label = QLabel()
        self.codex_usage_label.setWordWrap(True)
        engine = getattr(parent, "engine", None)
        quota_text = getattr(engine, "quota_status_text", lambda: "")() if engine else ""
        self.codex_usage_label.setText(
            f"Cota informada pelo Codex: {quota_text}" if quota_text else
            "A cota aparece aqui depois que o Codex informar os limites da conta."
        )
        self.codex_usage_label.setObjectName("codexUsageStatus")
        layout.addWidget(QLabel("Uso e redefinições do Codex"))
        layout.addWidget(self.codex_usage_label)
        self.manage_codex_usage = QPushButton("Abrir uso e redefinições no ChatGPT")
        self.manage_codex_usage.setToolTip("Abre a página da conta onde redefinições disponíveis podem ser aplicadas.")
        self.manage_codex_usage.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://chatgpt.com/#settings/usage"))
        )
        layout.addWidget(self.manage_codex_usage)
        layout.addWidget(QLabel(
            "Quando houver uma redefinição disponível, use-a nessa página. "
            "A IDE não altera os limites da conta; o Codex atualiza a cota ao consultar o serviço."
        ))
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
        self._rebuild_fields()

    def _build_agent_fields(self):
        self.agent_skill = QComboBox()
        self.agent_skill.addItem("Automático (recomendado)", "auto")
        for identifier, title in AGENT_SKILL_CHOICES:
            if identifier == "general-engineer":
                continue
            self.agent_skill.addItem(title, identifier)
        configured_skill = str(self.settings.get("agent_skill_id", "auto") or "auto")
        self.agent_skill.setCurrentIndex(max(0, self.agent_skill.findData(configured_skill)))
        self.agent_form.addRow("Especialista", self.agent_skill)

        self.continuous_loop = QCheckBox()
        self.continuous_loop.setChecked(bool(self.settings.get("continuous_development_loop_enabled", True)))
        self.agent_form.addRow("Ciclo contínuo", self.continuous_loop)

        self.max_agent_cycles = QSpinBox()
        self.max_agent_cycles.setRange(0, 1000)
        self.max_agent_cycles.setSpecialValueText("Sem teto de rodadas")
        self.max_agent_cycles.setValue(max(0, int(self.settings.get("continuous_development_max_cycles", 0) or 0)))
        self.agent_form.addRow("Máximo de rodadas", self.max_agent_cycles)

        self.codex_auto_approve = QCheckBox()
        self.codex_auto_approve.setChecked(bool(self.settings.get("codex_auto_approve_app_server_requests", True)))
        self.codex_auto_approve.setToolTip("Quando desativado, a IDE pede confirmação para cada ação do Codex app-server.")
        self.agent_form.addRow("Autoaprovar Codex", self.codex_auto_approve)

    def _build_video_fields(self):
        self.video_fields = {}
        for key, default in VIDEO_SETTING_DEFAULTS.items():
            label = key.replace("_", " ").capitalize()
            value = self.settings.get(key, default)
            if key == "video_provider":
                control = QComboBox()
                control.addItem("Gerador local (MoviePy)", "moviepy")
                control.addItem("ComfyUI (workflow externo)", "comfyui")
                control.setCurrentIndex(max(0, control.findData(str(value or default).lower())))
            elif isinstance(default, int):
                control = QSpinBox()
                control.setRange(60, 1_000_000)
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
        self.settings["agent_skill_id"] = self.agent_skill.currentData() or "auto"
        self.settings["continuous_development_loop_enabled"] = self.continuous_loop.isChecked()
        self.settings["continuous_development_max_cycles"] = self.max_agent_cycles.value()
        self.settings["codex_auto_approve_app_server_requests"] = self.codex_auto_approve.isChecked()
        for key, control in self.video_fields.items():
            self.settings[key] = (
                control.currentData() if isinstance(control, QComboBox) else
                control.value() if isinstance(control, QSpinBox) else
                control.text().strip()
            )
        super().accept()

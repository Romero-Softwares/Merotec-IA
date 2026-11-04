"""Dialogo PySide6 para os perfis de IA da Merotec."""

import hashlib
import copy

from PySide6.QtCore import QUrl, QObject, QRunnable, QThreadPool, Signal, QTimer, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget, QMessageBox

from modules.ai_profiles import PROFILE_DEFAULTS, PROVIDER_LABELS, PROVIDER_ORDER, activate_profile, profile_for, update_profile
from modules.agent_skills import AGENT_SKILL_CHOICES
from modules.video_generation import VIDEO_SETTING_DEFAULTS
from modules.voice import DEFAULT_EDGE_VOICE_ID, TTS_ENGINE_EDGE, list_tts_voices
from modules.model_discovery import discover_openai_models, normalize_openai_base_url
from modules.codex_model_discovery import discover_codex_models


class DiscoverySignals(QObject):
    done = Signal(int, object, str)


class ModelDiscoveryJob(QRunnable):
    def __init__(self, token, query):
        super().__init__()
        self.token, self.query = token, query
        self.signals = DiscoverySignals()

    def run(self):
        try:
            self.signals.done.emit(self.token, self.query(), "")
        except ValueError as exc:
            self.signals.done.emit(self.token, [], str(exc))
        except Exception:
            self.signals.done.emit(self.token, [], "Não foi possível consultar modelos. Confira a conexão e tente atualizar.")


class QtSettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.fields = {}
        self._current_provider = None
        self._drafts = {provider: profile_for(settings, provider) for provider in PROVIDER_ORDER}
        self._initial_drafts = copy.deepcopy(self._drafts)
        self._model_token = 0
        self._closed = False
        self._catalogs = {}
        self._model_records = {}
        self._catalog_loaded = False
        self._model_requests = {}
        self._model_timer = QTimer(self)
        self._model_timer.setSingleShot(True)
        self._model_timer.timeout.connect(self.refresh_models)
        self._codex_executable = getattr(getattr(parent, "engine", None), "codex_executable", None)
        self._known_codex_default = getattr(getattr(parent, "engine", None), "codex_default_model_name", "")
        self.codex_login_callback = getattr(parent, "launch_codex_login", None)
        self.setWindowTitle("Configuracoes da IA")
        self.setMinimumWidth(520)
        self.resize(650, 720)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.provider = QComboBox()
        for value in PROVIDER_ORDER:
            self.provider.addItem(PROVIDER_LABELS[value], value)
        active = settings.get("active_ai_profile", settings.get("ai_provider", "web_chat"))
        self.provider.setCurrentIndex(max(0, self.provider.findData(active)))
        self.provider.currentIndexChanged.connect(self._rebuild_fields)
        body.addWidget(QLabel("Perfil de IA ativo"))
        body.addWidget(self.provider)
        self.form = QFormLayout()
        body.addLayout(self.form)
        self.voice_form = QFormLayout()
        body.addWidget(QLabel("Voz e comandos"))
        body.addLayout(self.voice_form)
        self._build_voice_fields()
        self.agent_form = QFormLayout()
        body.addWidget(QLabel("Autonomia e especialista do agente"))
        body.addLayout(self.agent_form)
        self._build_agent_fields()
        self.video_form = QFormLayout()
        body.addWidget(QLabel("Geração de vídeo local"))
        body.addLayout(self.video_form)
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
        body.addWidget(QLabel("Uso e redefinições do Codex"))
        body.addWidget(self.codex_usage_label)
        self.manage_codex_usage = QPushButton("Abrir uso e redefinições no ChatGPT")
        self.manage_codex_usage.setToolTip("Abre a página da conta onde redefinições disponíveis podem ser aplicadas.")
        self.manage_codex_usage.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://chatgpt.com/#settings/usage"))
        )
        body.addWidget(self.manage_codex_usage)
        quota_help = QLabel(
            "Quando houver uma redefinição disponível, use-a nessa página. "
            "A IDE não altera os limites da conta; o Codex atualiza a cota ao consultar o serviço."
        )
        quota_help.setWordWrap(True)
        body.addWidget(quota_help)
        body.addStretch(1)
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

    def _build_voice_fields(self):
        self.voice_keyword_listener = QCheckBox("Ativar escuta automática do microfone")
        self.voice_keyword_listener.setChecked(bool(self.settings.get("voice_keyword_listener_enabled", False)))
        self.voice_keyword_listener.setToolTip(
            'Escuta "Merotec" seguido do comando e envia quando ouvir "ok".'
        )
        self.voice_form.addRow("Comando de voz", self.voice_keyword_listener)

        self.voice_options = {
            "Microsoft Francisca Neural - Português Brasil (EDGE)": {
                "id": DEFAULT_EDGE_VOICE_ID,
                "engine": TTS_ENGINE_EDGE,
            }
        }
        for voice in list_tts_voices():
            label = str(voice.get("label") or voice.get("name") or voice.get("id") or "").strip()
            if label:
                self.voice_options[label] = {
                    "id": str(voice.get("id") or DEFAULT_EDGE_VOICE_ID),
                    "engine": str(voice.get("engine") or TTS_ENGINE_EDGE),
                }
        self.tts_voice = QComboBox()
        self.tts_voice.addItems(self.voice_options.keys())
        current_voice_id = str(self.settings.get("tts_voice_id") or DEFAULT_EDGE_VOICE_ID)
        selected = next(
            (label for label, config in self.voice_options.items() if config["id"] == current_voice_id),
            next(iter(self.voice_options)),
        )
        self.tts_voice.setCurrentText(selected)
        self.tts_voice.setToolTip("Voz usada na leitura de respostas e nas narrações geradas.")
        self.voice_form.addRow("Voz da leitura", self.tts_voice)

        help_text = QLabel(
            'Comando contínuo: diga “Merotec”, fale o pedido e finalize com “ok”. '
            "O botão de microfone continua disponível para gravação manual."
        )
        help_text.setWordWrap(True)
        self.voice_form.addRow("Como usar", help_text)

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
        if self._current_provider is not None:
            self._drafts[self._current_provider].update(self._field_values())
        self._model_timer.stop()
        self._model_token += 1
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields = {}
        provider = self.provider.currentData()
        self._current_provider = provider
        self._model_records = {}
        self._catalog_loaded = False
        self.codex_login_button.setVisible(provider == "codex")
        values = self._drafts[provider]
        for key, default in PROFILE_DEFAULTS[provider].items():
            label = {"openai_model_name": "Modelo de texto", "openai_image_model_name": "Modelo de imagem",
                     "codex_model_name": "Modelo Codex", "codex_reasoning_effort": "Raciocínio",
                     "openai_api_key": "Chave da API", "openai_base_url": "URL da API"}.get(key, key.replace("_", " ").capitalize())
            value = values.get(key, default)
            if key in {"openai_model_name", "openai_image_model_name", "codex_model_name", "codex_reasoning_effort"}:
                control = QComboBox()
                control.setEditable(True)
                control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                control.setMinimumContentsLength(18)
                control.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                seed = [{"id": self._known_codex_default, "default": True}] if key == "codex_model_name" and self._known_codex_default else []
                self._fill_model_combo(control, seed, str(value or ""), account_default=key == "codex_model_name")
                control.completer().setFilterMode(Qt.MatchFlag.MatchContains)
                control.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                control.lineEdit().setPlaceholderText("Selecione ou informe o ID exato")
            elif isinstance(default, bool):
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
        if provider in {"openai", "codex"}:
            self.refresh_models_button = QPushButton("Atualizar modelos disponíveis")
            self.refresh_models_button.clicked.connect(lambda: self.refresh_models(force=True))
            self.form.addRow("Catálogo", self.refresh_models_button)
            self.model_status = QLabel("Consultando modelos disponíveis…")
            self.model_status.setWordWrap(True)
            self.form.addRow(self.model_status)
            note = QLabel("A seleção salva o ID exato. A disponibilidade depende da conta; modelos de imagem/áudio não são necessariamente compatíveis com o chat.")
            note.setWordWrap(True)
            self.form.addRow(note)
            for name in ("codex_model_name", "openai_model_name", "openai_image_model_name"):
                if name in self.fields:
                    self.fields[name].currentTextChanged.connect(self._refresh_catalog_status)
            if provider == "openai":
                for key in ("openai_api_key", "openai_base_url"):
                    self.fields[key].textEdited.connect(self._invalidate_models)
                    self.fields[key].editingFinished.connect(self._schedule_discovery)
            else:
                self.fields["codex_model_name"].currentTextChanged.connect(self._update_reasoning_choices)
            self._schedule_discovery()

    @staticmethod
    def _control_value(control):
        if isinstance(control, QCheckBox):
            return control.isChecked()
        if isinstance(control, QSpinBox):
            return control.value()
        if isinstance(control, QComboBox):
            index = control.currentIndex()
            if index >= 0 and control.currentText() == control.itemText(index) and control.currentData() is not None:
                return control.currentData()
            return control.currentText().strip()
        return control.text().strip()

    def _field_values(self):
        return {key: self._control_value(control) for key, control in self.fields.items()}

    @staticmethod
    def _fill_model_combo(control, records, current, account_default=False):
        control.blockSignals(True)
        try:
            control.clear()
            if account_default:
                default = next((record["id"] for record in records if isinstance(record, dict) and record.get("default")), "")
                control.addItem(f"{default} (automático)" if default else "Consultando ID do modelo automático…", "")
            for record in records:
                identifier = record["id"] if isinstance(record, dict) else record
                if control.findData(identifier) < 0:
                    control.addItem(identifier, identifier)
                    if isinstance(record, dict):
                        control.setItemData(control.count() - 1, record.get("name", identifier), Qt.ItemDataRole.ToolTipRole)
            found = control.findData(current)
            if found >= 0:
                control.setCurrentIndex(found)
            elif current:
                control.insertItem(0, current, current)
                control.setCurrentIndex(0)
            else:
                control.setCurrentIndex(-1)
                control.setEditText("")
        finally:
            control.blockSignals(False)

    def _schedule_discovery(self):
        if not self._closed and self._current_provider in {"openai", "codex"}:
            self._model_timer.start(200)

    def _invalidate_models(self, *_):
        self._model_token += 1
        self._model_timer.stop()
        self._model_records = {}
        self._catalog_loaded = False
        self.refresh_models_button.setEnabled(True)
        for key in ("openai_model_name", "openai_image_model_name"):
            if key in self.fields:
                control = self.fields[key]
                self._fill_model_combo(control, [], self._control_value(control))
        self.model_status.setText("Credenciais/endpoint alterados. A lista será consultada ao concluir a edição, ou clique em Atualizar.")

    def refresh_models(self, force=False):
        if self._closed or self._current_provider not in {"openai", "codex"}:
            return
        self._model_timer.stop()
        provider = self._current_provider
        values = self._field_values()
        if provider == "openai":
            key = values["openai_api_key"]
            try:
                base = normalize_openai_base_url(values["openai_base_url"], key)
            except ValueError as exc:
                self.model_status.setText(str(exc))
                return
            if QUrl(base).host() == "api.openai.com" and not key:
                self.model_status.setText("Informe a chave de API deste perfil para descobrir os modelos da conta. A digitação manual continua disponível.")
                return
            signature = (provider, base, hashlib.sha256(key.encode()).hexdigest())
            query = lambda: [{"id": name} for name in discover_openai_models(base, key, timeout=8)]
        else:
            signature = (provider, self._codex_executable or "local")
            executable = self._codex_executable
            def query():
                if not executable:
                    from modules.engine import UniversalEngine
                    finder = UniversalEngine.__new__(UniversalEngine)
                    detected = finder._find_codex_executable()
                else:
                    detected = executable
                return discover_codex_models(detected, timeout=12)
        self._model_token += 1
        token = self._model_token
        self._model_requests[token] = signature
        if not force and signature in self._catalogs:
            self._models_discovered(token, self._catalogs[signature], "")
            return
        self.model_status.setText("Consultando modelos da conta… Você pode continuar editando.")
        self._catalog_loaded = False
        self.refresh_models_button.setEnabled(False)
        job = ModelDiscoveryJob(token, query)
        job.signals.done.connect(self._models_discovered)
        QThreadPool.globalInstance().start(job)

    def _models_discovered(self, token, records, error):
        signature = self._model_requests.pop(token, None)
        if self._closed or token != self._model_token or signature is None:
            return
        self.refresh_models_button.setEnabled(True)
        if error:
            self.model_status.setText(error + " O modelo informado foi preservado; você pode digitá-lo manualmente.")
            return
        self._catalogs[signature] = records
        self._model_records = {record["id"]: record for record in records}
        self._catalog_loaded = True
        provider = self._current_provider
        model_keys = ("codex_model_name",) if provider == "codex" else ("openai_model_name", "openai_image_model_name")
        missing = []
        resolved_label = False
        for key in model_keys:
            control = self.fields[key]
            selected = self._control_value(control)
            if provider == "codex" and selected and selected not in self._model_records:
                matching = [record for record in records if record.get("name", "").casefold() == selected.casefold()]
                if len(matching) == 1:
                    selected = matching[0]["id"]
                    resolved_label = True
            self._fill_model_combo(control, records, selected, account_default=key == "codex_model_name")
            if selected and selected not in self._model_records:
                missing.append(selected)
        message = f"{len(records)} modelo(s) retornado(s) pela conta. Escolha o ID na lista ou digite um ID manualmente."
        if missing:
            message += " A seleção atual não foi encontrada no catálogo; confira o ID antes de salvar."
        if resolved_label:
            message += " O nome exibido foi associado ao ID exato retornado pelo Codex; clique em Salvar para aplicar."
        self.model_status.setText(message)
        if provider == "codex":
            default = next((record["id"] for record in records if record.get("default")), "")
            self._known_codex_default = default
            engine = getattr(self.parent(), "engine", None)
            if default and engine is not None and hasattr(engine, "remember_codex_default_model"):
                engine.remember_codex_default_model(default)
            self._update_reasoning_choices()

    def _refresh_catalog_status(self, *_):
        if not self._catalog_loaded:
            return
        keys = ("codex_model_name",) if self._current_provider == "codex" else ("openai_model_name", "openai_image_model_name")
        missing = [self._control_value(self.fields[key]) for key in keys if self._control_value(self.fields[key]) and self._control_value(self.fields[key]) not in self._model_records]
        text = f"{len(self._model_records)} modelo(s) retornado(s) pela conta. Clique em Salvar para aplicar o ID selecionado."
        if missing:
            text += " Um modelo informado não foi encontrado no catálogo; confira o ID."
        self.model_status.setText(text)

    def _update_reasoning_choices(self, *_):
        if self._current_provider != "codex":
            return
        selected = self._control_value(self.fields["codex_model_name"])
        record = self._model_records.get(selected, {}) if selected else next((entry for entry in self._model_records.values() if entry.get("default")), {})
        choices = record.get("reasoning", [])
        if choices:
            control = self.fields["codex_reasoning_effort"]
            current = self._control_value(control)
            if current not in choices:
                current = record.get("default_reasoning") or choices[0]
            self._fill_model_combo(control, choices, current)

    def _launch_codex_login(self):
        if callable(self.codex_login_callback):
            self.codex_login_callback()
            return
        self.codex_login_button.setEnabled(False)
        self.codex_login_button.setText("Login indisponivel")

    def accept(self):
        values = self._field_values()
        provider = self.provider.currentData()
        if provider == "codex":
            selected = values["codex_model_name"]
            if selected and selected not in self._model_records:
                labels = [record for record in self._model_records.values() if record.get("name", "").casefold() == selected.casefold()]
                if len(labels) == 1:
                    values["codex_model_name"] = labels[0]["id"]
                elif any(char.isspace() for char in selected):
                    QMessageBox.warning(self, "Modelo Codex", "Informe o ID exato, sem espaços, ou selecione um modelo no catálogo. O nome exibido pelo produto pode ser diferente do ID.")
                    return
            selected = values["codex_model_name"]
            record = self._model_records.get(selected, {}) if selected else next((entry for entry in self._model_records.values() if entry.get("default")), {})
            choices = record.get("reasoning", [])
            if choices and values["codex_reasoning_effort"] not in choices:
                values["codex_reasoning_effort"] = record.get("default_reasoning") or choices[0]
        if provider == "openai":
            if not values["openai_model_name"]:
                QMessageBox.warning(self, "Modelo OpenAI", "Selecione ou informe o ID do modelo de texto.")
                return
            try:
                values["openai_base_url"] = normalize_openai_base_url(values["openai_base_url"], values["openai_api_key"])
            except ValueError as exc:
                QMessageBox.warning(self, "Endpoint OpenAI", str(exc))
                return
        self._drafts[provider].update(values)
        # Switching profiles preserves drafts; validate edited inactive drafts too
        # before mutating the shared settings object.
        for name, draft in self._drafts.items():
            if name == provider or draft == self._initial_drafts[name]:
                continue
            try:
                if name == "openai":
                    if not str(draft.get("openai_model_name") or "").strip():
                        raise ValueError("Selecione ou informe o ID do modelo de texto.")
                    draft["openai_base_url"] = normalize_openai_base_url(draft.get("openai_base_url"), draft.get("openai_api_key"))
                elif name == "codex":
                    selected = str(draft.get("codex_model_name") or "").strip()
                    if any(char.isspace() for char in selected):
                        signature = ("codex", self._codex_executable or "local")
                        matches = [record for record in self._catalogs.get(signature, []) if record.get("name", "").casefold() == selected.casefold()]
                        if len(matches) != 1:
                            raise ValueError("Selecione o ID Codex exato, sem espaços, na lista de modelos.")
                        draft["codex_model_name"] = matches[0]["id"]
            except ValueError as exc:
                QMessageBox.warning(self, f"Perfil {PROVIDER_LABELS[name]}", str(exc))
                self.provider.setCurrentIndex(self.provider.findData(name))
                return
        for name, draft in self._drafts.items():
            update_profile(self.settings, name, draft)
        activate_profile(self.settings, provider)
        self.settings["agent_skill_id"] = self.agent_skill.currentData() or "auto"
        self.settings["continuous_development_loop_enabled"] = self.continuous_loop.isChecked()
        self.settings["continuous_development_max_cycles"] = self.max_agent_cycles.value()
        self.settings["codex_auto_approve_app_server_requests"] = self.codex_auto_approve.isChecked()
        voice_config = self.voice_options.get(self.tts_voice.currentText(), {})
        self.settings["voice_keyword_listener_enabled"] = self.voice_keyword_listener.isChecked()
        self.settings["tts_engine"] = voice_config.get("engine", TTS_ENGINE_EDGE)
        self.settings["tts_voice_id"] = voice_config.get("id", DEFAULT_EDGE_VOICE_ID)
        for key, control in self.video_fields.items():
            self.settings[key] = (
                control.currentData() if isinstance(control, QComboBox) else
                control.value() if isinstance(control, QSpinBox) else
                control.text().strip()
            )
        self._closed = True
        self._model_timer.stop()
        self._model_token += 1
        self._model_requests.clear()
        super().accept()

    def reject(self):
        self._closed = True
        self._model_timer.stop()
        self._model_token += 1
        self._model_requests.clear()
        super().reject()

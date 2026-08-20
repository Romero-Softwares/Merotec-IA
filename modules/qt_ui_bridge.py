"""Primitivas de integracao entre workers da IDE e a thread do Qt.

O nucleo da Merotec executa IA, terminal, voz e navegador em threads. Esta
ponte oferece um contrato unico para a migracao: nenhum worker pode tocar em
widgets diretamente, e os timers usados pelos mixins deixam de depender do
agendador do Tk.
"""

from __future__ import annotations

from itertools import count

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt


class QtUiBridge(QObject):
    """Agenda callbacks para a thread Qt e permite cancela-los com seguranca."""

    _invoke_requested = Signal(object)
    _schedule_requested = Signal(int, int, object)
    _cancel_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sequence = count(1)
        self._timers = {}
        # ``emit`` pode ser chamado por workers Python que não possuem
        # afinidade Qt. A conexão automática considera os QObjects (ambos
        # vivem na thread principal), podendo executar o slot no worker. Forçar
        # fila garante que widgets e QWebEngine só sejam tocados pelo loop Qt.
        self._invoke_requested.connect(self._invoke, Qt.ConnectionType.QueuedConnection)
        self._schedule_requested.connect(self._schedule, Qt.ConnectionType.QueuedConnection)
        self._cancel_requested.connect(self._cancel, Qt.ConnectionType.QueuedConnection)

    def call_soon(self, callback):
        self._invoke_requested.emit(callback)

    def after(self, milliseconds, callback):
        token = next(self._sequence)
        self._schedule_requested.emit(token, max(0, int(milliseconds)), callback)
        return token

    def after_cancel(self, token):
        self._cancel_requested.emit(token)

    @Slot(object)
    def _invoke(self, callback):
        callback()

    @Slot(int, int, object)
    def _schedule(self, token, milliseconds, callback):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._run_timer(token, callback))
        self._timers[token] = timer
        timer.start(milliseconds)

    @Slot(int)
    def _cancel(self, token):
        timer = self._timers.pop(token, None)
        if timer:
            timer.stop()
            timer.deleteLater()

    def _run_timer(self, token, callback):
        timer = self._timers.pop(token, None)
        if timer:
            timer.deleteLater()
        callback()

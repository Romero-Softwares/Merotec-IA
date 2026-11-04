"""Native Qt schematic and PCB editor for the Merotec CAD model."""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF, QObject, Signal, QRunnable, QThreadPool
from PySide6.QtGui import QColor, QPen, QPainterPath, QAction, QKeySequence, QPainter
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGraphicsScene,
    QGraphicsView, QGraphicsItem, QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsPathItem,
    QComboBox, QLabel, QSplitter, QPlainTextEdit, QFileDialog, QMessageBox, QInputDialog,
    QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QFormLayout, QDoubleSpinBox)

from modules import native_pcb as cad


class CheckSignals(QObject):
    done = Signal(object)


class CheckJob(QRunnable):
    def __init__(self, design):
        super().__init__()
        self.design = copy.deepcopy(design)
        self.signals = CheckSignals()

    def run(self):
        try:
            result = cad.checks(self.design)
            self.signals.done.emit((self.design, result, ""))
        except Exception as exc:
            self.signals.done.emit((self.design, [], str(exc)))


def symbol_pin(part, pin):
    pins = list(part["nets"])
    index = pins.index(pin)
    half = (len(pins) + 1) // 2
    return (-6 if index < half else 6, (index % half - (half - 1) / 2) * 2.5)


class PartItem(QGraphicsRectItem):
    def __init__(self, editor, part):
        super().__init__(-3.3, -3.2, 6.6, 6.4)
        self.editor, self.part = editor, part
        self.setData(0, ("component", part["ref"]))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setPen(QPen(QColor("#90b6d9"), .12))
        self.setBrush(QColor("#21394c"))
        self.setZValue(3)
        self.setPos(part["sx"] if editor.schematic else part["x"], part["sy"] if editor.schematic else part["y"])
        for pin in part["nets"]:
            if editor.schematic:
                x, y = symbol_pin(part, pin)
                size = (0.8, 0.8)
            else:
                global_point = cad.pad(editor.design, f"{part['ref']}.{pin}")
                x, y = global_point[0] - part["x"], global_point[1] - part["y"]
                size = cad.LIBRARY[part["kind"]]["size"]
            pad = QGraphicsRectItem(-size[0]/2, -size[1]/2, *size, self)
            pad.setPos(x, y)
            if not editor.schematic:
                pad.setRotation(part["rotation"])
            pad.setBrush(QColor("#f2d084"))
            pad.setPen(QPen(QColor("#ffeabb"), .08))
            pad.setData(0, ("pad", f"{part['ref']}.{pin}"))
            pad.setToolTip(f"{part['ref']}.{pin}: {part['nets'][pin] or 'sem rede'}")
        text = editor.scene.addSimpleText(f"{part['ref']} {part['value']}")
        text.setParentItem(self)
        text.setScale(.1)
        text.setBrush(QColor("#e2eef8"))
        text.setPos(-3.3, -5.3)

    def mousePressEvent(self, event):
        self.old_position = self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        old = getattr(self, "old_position", self.pos())
        if self.pos() != old:
            self.editor.history.record(self.editor.design)
            keys = ("sx", "sy") if self.editor.schematic else ("x", "y")
            self.part[keys[0]] = round(self.x() * 2) / 2
            self.part[keys[1]] = round(self.y() * 2) / 2
            # Rebuild after the mouse handler returns, so Qt does not delete its active item.
            from PySide6.QtCore import QTimer
            self.editor.changed = True
            QTimer.singleShot(0, self.editor.render)


class CADScene(QGraphicsScene):
    def mousePressEvent(self, event):
        editor = self.parent()
        if editor.routing and not editor.schematic and event.button() == Qt.MouseButton.LeftButton:
            endpoint = None
            for item in self.items(event.scenePos()):
                data = item.data(0)
                if isinstance(data, tuple) and data[0] == "pad":
                    endpoint = data[1]
                    break
            if endpoint:
                editor.route_endpoint(endpoint)
            elif editor.route_start:
                p = event.scenePos()
                editor.route_points.append([round(p.x()*2)/2, round(p.y()*2)/2])
                editor.render()
            return
        super().mousePressEvent(event)


class CADView(QGraphicsView):
    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        if .5 < self.transform().m11() * factor < 80:
            self.scale(factor, factor)


class NativePCBWindow(QMainWindow):
    def __init__(self, ide):
        super().__init__(ide)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.workspace = Path(ide.workspace).resolve()
        self.design = cad.new_design()
        self.path = None
        self.changed = False
        self.check_job = None
        self.history = cad.History()
        self.route_start, self.route_points = None, []
        self.routing = False
        self.schematic = True
        self.resize(1180, 760)
        self.setWindowTitle("Merotec PCB | Editor nativo")
        self.setStyleSheet("""
            QMainWindow { background: #0b1722; color: #dce8f4; }
            QToolBar { background: #102233; border: 0; spacing: 4px; padding: 5px; }
            QToolButton { color: #deebf7; background: #1d3a50; border: 1px solid #34576c; border-radius: 4px; padding: 6px; }
            QToolButton:hover, QToolButton:checked { background: #316479; }
            QComboBox { background: #172e40; color: #deebf7; padding: 5px; border: 1px solid #34576c; }
            QComboBox QAbstractItemView { background: #172e40; color: #deebf7; }
            QPlainTextEdit { background: #101f2c; color: #cfdfed; border: 0; padding: 12px; }
            QStatusBar { background: #102233; color: #9ab7c9; }
            QSplitter::handle { background: #294355; width: 2px; }
        """)
        toolbar = self.addToolBar("Projeto e edição")
        for label, handler, shortcut in [
            ("Novo", self.new, "Ctrl+N"), ("Abrir", self.open, "Ctrl+O"), ("Salvar", self.save, "Ctrl+S"),
            ("Salvar como", self.save_as, "Ctrl+Shift+S"), ("Desfazer", self.undo, "Ctrl+Z"),
            ("Refazer", self.redo, "Ctrl+Shift+Z"), ("Excluir", self.delete_selected, "Delete"),
            ("Girar 90°", self.rotate_selected, "R"), ("ERC/DRC", self.run_checks, "F6")]:
            action = QAction(label, self)
            action.triggered.connect(handler)
            action.setShortcut(QKeySequence(shortcut))
            toolbar.addAction(action)
        self.addToolBarBreak()
        modebar = self.addToolBar("Circuito")
        self.mode = QComboBox()
        self.mode.addItems(["Esquemático", "PCB — cobre superior"])
        self.mode.currentIndexChanged.connect(self.change_mode)
        modebar.addWidget(self.mode)
        self.library = QComboBox()
        self.library.addItems(cad.LIBRARY)
        modebar.addWidget(self.library)
        for label, handler in [("Adicionar", self.add_part), ("Redes e pinos", self.edit_nets),
                               ("Placa e regras", self.board_settings), ("Exemplo", self.open_demo), ("Enquadrar", self.fit_view), ("SVG/BOM", self.export)]:
            action = modebar.addAction(label)
            action.triggered.connect(handler)
        self.route_action = modebar.addAction("Rotear")
        self.route_action.setCheckable(True)
        self.route_action.toggled.connect(self.set_routing)
        cancel = QAction("Cancelar rota", self)
        cancel.setShortcut("Escape")
        cancel.triggered.connect(self.cancel_route)
        self.addAction(cancel)
        self.scene = CADScene(self)
        self.view = CADView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.view.setBackgroundBrush(QColor("#0b1722"))
        self.view.scale(7, 7)
        splitter = QSplitter()
        splitter.addWidget(self.view)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMinimumWidth(260)
        self.report.setPlainText("Editor nativo sem dependência de KiCad.\n\n1. Adicione componentes.\n2. Em Redes e pinos, atribua nomes e tipos elétricos. Nomes iguais formam uma rede.\n3. Alterne para PCB e posicione footprints.\n4. Ative Rotear: pad inicial, pontos intermediários e pad final. Esc cancela.\n5. Execute ERC/DRC (F6).\n\nArraste: mover; R: girar footprint; Delete: excluir seleção; roda: zoom.\n\nCamada única, pads SMD, símbolos genéricos. SVG é prévia, não arquivo de fabricação.")
        splitter.addWidget(self.report)
        splitter.setStretchFactor(0, 4)
        splitter.setSizes([850, 330])
        self.setCentralWidget(splitter)
        self.render()

    def title(self):
        self.setWindowTitle(f"Merotec PCB | {self.path.name if self.path else 'Sem título'}{' *' if self.changed else ''}")

    def change_mode(self, index):
        self.schematic = index == 0
        self.cancel_route()
        self.render()

    def set_routing(self, enabled):
        self.routing = enabled
        self.route_start, self.route_points = None, []
        self.statusBar().showMessage("Clique no pad inicial, pontos opcionais e pad final da mesma rede. Esc cancela." if enabled else "Seleção e posicionamento")
        self.render()

    def cancel_route(self):
        self.route_action.setChecked(False)
        self.route_start, self.route_points = None, []
        self.render()

    def mutate(self):
        self.history.record(self.design)
        self.changed = True

    def add_part(self):
        value, ok = QInputDialog.getText(self, "Componente", "Valor / modelo:")
        if ok:
            if len(self.design["components"]) >= 500:
                return self.warning("Limite de 500 componentes atingido.")
            self.mutate()
            count = len(self.design["components"])
            cad.add_component(self.design, self.library.currentText(), value, 10 + count % 6 * 10, 10 + count // 6 * 10)
            self.render()

    def render(self):
        if not hasattr(self, "scene"):
            return
        self.scene.clear()
        if not self.schematic:
            board = self.design["board"]
            self.scene.addRect(0, 0, board["width"], board["height"], QPen(QColor("#75c49b"), .2), QColor("#102a24"))
            for index, track in enumerate(self.design["tracks"]):
                points = cad.track_points(self.design, track)
                path = QPainterPath(QPointF(*points[0]))
                for point in points[1:]:
                    path.lineTo(*point)
                pen = QPen(QColor("#eda34f"), track["width"])
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                item = self.scene.addPath(path, pen)
                item.setData(0, ("track", index))
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        groups = {}
        for part in self.design["components"]:
            self.scene.addItem(PartItem(self, part))
            for pin, name in part["nets"].items():
                if not name:
                    continue
                if self.schematic:
                    x, y = symbol_pin(part, pin)
                    position = (part["sx"] + x, part["sy"] + y)
                else:
                    position = cad.pad(self.design, f"{part['ref']}.{pin}")
                groups.setdefault(name, []).append(position)
        for name, points in groups.items():
            for a, b in zip(points, points[1:]):
                pen = QPen(QColor("#54c9dd" if self.schematic else "#546d6e"), .12)
                if not self.schematic:
                    pen.setStyle(Qt.PenStyle.DashLine)
                line = self.scene.addLine(*a, *b, pen)
                line.setZValue(1 if self.schematic else 0)
                line.setToolTip(name)
        if self.route_start:
            points = [cad.pad(self.design, self.route_start), *self.route_points]
            for a, b in zip(points, points[1:]):
                self.scene.addLine(*a, *b, QPen(QColor("#ffffff"), .25))
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-12, -12, 12, 12))
        self.title()

    def route_endpoint(self, endpoint):
        if self.route_start is None:
            if not cad.net(self.design, endpoint):
                return self.warning("Atribua uma rede ao pad antes de rotear.")
            self.route_start = endpoint
            self.statusBar().showMessage(f"Origem: {endpoint}; clique no destino da mesma rede.")
        elif endpoint != self.route_start:
            if cad.net(self.design, endpoint) != cad.net(self.design, self.route_start):
                return self.warning("Destino deve pertencer à mesma rede da origem.")
            if len(self.design["tracks"]) >= 2000 or len(self.route_points) > 100:
                return self.warning("Limite de trilhas/pontos atingido.")
            self.mutate()
            self.design["tracks"].append({"from": self.route_start, "to": endpoint,
                "width": self.design["board"]["min_track"], "points": copy.deepcopy(self.route_points)})
            self.route_start, self.route_points = None, []
            self.render()

    def edit_nets(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Netlist compartilhada: esquemático e PCB")
        dialog.resize(600, 500)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(sum(len(c["nets"]) for c in self.design["components"]), 3)
        table.setHorizontalHeaderLabels(["Terminal", "Rede", "Tipo elétrico"])
        rows = []
        for part in self.design["components"]:
            for pin in part["nets"]:
                row = len(rows)
                item = QTableWidgetItem(f"{part['ref']}.{pin}")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, 0, item)
                table.setItem(row, 1, QTableWidgetItem(part["nets"][pin]))
                role = QComboBox()
                role.addItems(["passive", "input", "output", "power_in", "power_out", "nc"])
                role.setCurrentText(part["roles"][pin])
                table.setCellWidget(row, 2, role)
                rows.append((part, pin, role))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if any(len(table.item(i, 1).text().strip()) > 100 for i in range(len(rows))):
                return self.warning("Nome de rede limitado a 100 caracteres.")
            self.mutate()
            for i, (part, pin, role) in enumerate(rows):
                part["nets"][pin] = table.item(i, 1).text().strip()
                part["roles"][pin] = role.currentText()
            self.cancel_route()

    def board_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Dimensões e regras (mm)")
        form = QFormLayout(dialog)
        fields = {}
        for key, label in [("width", "Largura"), ("height", "Altura"), ("clearance", "Folga mínima"), ("min_track", "Largura mínima de trilha")]:
            widget = QDoubleSpinBox()
            widget.setDecimals(3)
            widget.setRange(.01, 1000)
            widget.setValue(self.design["board"][key])
            fields[key] = widget
            form.addRow(label, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.mutate()
            self.design["board"].update({key: widget.value() for key, widget in fields.items()})
            self.render()

    def delete_selected(self):
        selected = [i.data(0) for i in self.scene.selectedItems() if i.data(0)]
        if not selected:
            return
        self.mutate()
        removed = {data[1] for data in selected if data[0] == "track"}
        self.design["tracks"] = [t for i, t in enumerate(self.design["tracks"]) if i not in removed]
        for kind, ident in selected:
            if kind == "component":
                cad.remove_component(self.design, ident)
        self.cancel_route()

    def rotate_selected(self):
        if self.schematic:
            return
        refs = [i.data(0)[1] for i in self.scene.selectedItems() if i.data(0) and i.data(0)[0] == "component"]
        if refs:
            self.mutate()
            for ref in refs:
                c = cad.component(self.design, ref)
                c["rotation"] = (c["rotation"] + 90) % 360
            self.render()

    def undo(self):
        if self.history.undo_stack:
            self.design = self.history.undo(self.design)
            self.changed = True
            self.cancel_route()

    def redo(self):
        if self.history.redo_stack:
            self.design = self.history.redo(self.design)
            self.changed = True
            self.cancel_route()

    def run_checks(self):
        if self.check_job is not None:
            return
        self.check_job = CheckJob(self.design)
        self.check_job.signals.done.connect(self.receive_checks)
        self.report.setPlainText("Executando ERC/DRC nativo em segundo plano…")
        QThreadPool.globalInstance().start(self.check_job)

    def receive_checks(self, result):
        snapshot, findings, error = result
        self.check_job = None
        stale = "\nO projeto mudou durante a análise; execute novamente.\n" if snapshot != self.design else ""
        self.report.setPlainText("ERC/DRC nativo — snapshot em memória\n" + stale +
            (f"Falha na análise: {error}" if error else
             ("\n".join(f"[{f['code']}] {f['detail']}" for f in findings) if findings else "Nenhuma violação nas regras implementadas.")) +
            "\n\nCamada única SMD; pads retangulares e trilhas com extremidades circulares. Não inclui regras de fabricação, térmica, EMC ou certificação. Interseções na mesma rede não criam junções lógicas; conecte pads explicitamente.")

    def warning(self, text):
        QMessageBox.warning(self, "Merotec PCB", text)

    def discard_ok(self):
        if not self.changed:
            return True
        choice = QMessageBox.question(self, "Projeto alterado", "Salvar alterações antes de continuar?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return self.save() if choice == QMessageBox.StandardButton.Save else choice == QMessageBox.StandardButton.Discard

    def new(self):
        if self.discard_ok():
            self.design, self.path, self.history = cad.new_design(), None, cad.History()
            self.changed = False
            self.cancel_route()

    def open_demo(self):
        if self.discard_ok():
            self.design, self.path, self.history = cad.demo_design(), None, cad.History()
            self.changed = True
            self.mode.setCurrentIndex(1)
            self.cancel_route()
            self.report.setPlainText("Exemplo de geometria: dois resistores em paralelo, com duas redes roteadas. Não representa um dispositivo alimentado completo.\n\nUse Redes e pinos para examinar a netlist e F6 para verificar as regras nativas.")
            self.fit_view()

    def fit_view(self):
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def open(self):
        if not self.discard_ok():
            return
        name, _ = QFileDialog.getOpenFileName(self, "Abrir projeto nativo", str(self.workspace), "Merotec PCB (*.mpcb.json)")
        if name:
            try:
                design = cad.load(name)
                self.design, self.path, self.history = design, Path(name), cad.History()
                self.changed = False
                self.cancel_route()
            except (ValueError, OSError) as exc:
                self.warning(str(exc))

    def save(self):
        if self.path is None:
            return self.save_as()
        try:
            cad.save(self.path, self.design)
            self.changed = False
            self.title()
            return True
        except (ValueError, OSError) as exc:
            self.warning(str(exc))
            return False

    def save_as(self):
        name, _ = QFileDialog.getSaveFileName(self, "Salvar projeto nativo", str(self.path or self.workspace / "hardware/design.mpcb.json"), "Merotec PCB (*.mpcb.json)")
        if not name:
            return False
        target = Path(name if name.endswith(".mpcb.json") else name + ".mpcb.json")
        previous = self.path
        self.path = target
        if not self.save():
            self.path = previous
            return False
        return True

    def export(self):
        name, selected = QFileDialog.getSaveFileName(self, "Exportar prévia ou BOM", str(self.workspace / "board.svg"), "Prévia SVG (*.svg);;BOM CSV (*.csv)")
        if name:
            try:
                csv_output = "CSV" in selected
                suffix = ".csv" if csv_output else ".svg"
                path = Path(name if name.endswith(suffix) else name + suffix)
                path.write_text(cad.export_bom(self.design) if csv_output else cad.export_svg(self.design), encoding="utf-8")
            except (ValueError, OSError) as exc:
                self.warning(str(exc))

    def closeEvent(self, event):
        if self.discard_ok():
            event.accept()
        else:
            event.ignore()

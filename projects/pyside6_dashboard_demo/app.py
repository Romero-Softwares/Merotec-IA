"""Dashboard demonstrativo de compostagem construído com PySide6."""

import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)


LIGHT_COLORS = {
    "background": "#F5F7F3",
    "surface": "#FFFFFF",
    "ink": "#1B2A25",
    "muted": "#71817A",
    "green": "#2D7A5D",
    "green_light": "#E4F3EA",
    "yellow": "#F2B84B",
    "line": "#DCE5DE",
    "red": "#DC6B68",
}

DARK_COLORS = {
    "background": "#121A17",
    "surface": "#1B2823",
    "ink": "#EFF7F1",
    "muted": "#A8BBB1",
    "green": "#49A77C",
    "green_light": "#203C31",
    "yellow": "#F4BA4C",
    "line": "#395248",
    "red": "#EE807B",
}

COLORS = LIGHT_COLORS.copy()


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.NoFrame)


class MetricCard(Card):
    def __init__(self, icon, label, value, detail, accent):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        top = QHBoxLayout()
        badge = QLabel()
        badge.setPixmap(icon.pixmap(20, 20))
        badge.setFixedSize(38, 38)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"background:{accent}; border-radius:19px;")
        top.addWidget(badge)
        top.addStretch()
        status = QLabel("ATIVO")
        status.setObjectName("status")
        top.addWidget(status)
        layout.addLayout(top)

        label_widget = QLabel(label)
        label_widget.setObjectName("metricLabel")
        layout.addWidget(label_widget)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        layout.addWidget(self.value_label)
        detail_widget = QLabel(detail)
        detail_widget.setObjectName("metricDetail")
        layout.addWidget(detail_widget)


class TrendChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(230)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.values = [28, 42, 37, 58, 49, 72, 66, 88]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(12, 15, -15, -30)
        max_value = 100
        min_value = 0

        painter.setFont(QFont("Segoe UI", 8))
        for tick in range(0, 101, 25):
            y = rect.bottom() - (tick - min_value) / (max_value - min_value) * rect.height()
            painter.setPen(QPen(QColor(COLORS["line"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(0, int(y - 7), 34, 16, Qt.AlignmentFlag.AlignRight, str(tick))

        step = rect.width() / (len(self.values) - 1)
        points = [QPointF(rect.left() + index * step, rect.bottom() - value / max_value * rect.height()) for index, value in enumerate(self.values)]
        area = QPainterPath()
        area.moveTo(points[0])
        for point in points[1:]:
            area.lineTo(point)
        area.lineTo(points[-1].x(), rect.bottom())
        area.lineTo(points[0].x(), rect.bottom())
        area.closeSubpath()
        gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
        gradient.setColorAt(0, QColor(45, 122, 93, 75))
        gradient.setColorAt(1, QColor(45, 122, 93, 4))
        painter.fillPath(area, gradient)

        line = QPainterPath()
        line.moveTo(points[0])
        for point in points[1:]:
            line.lineTo(point)
        painter.setPen(QPen(QColor(COLORS["green"]), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(line)
        painter.setBrush(QColor(COLORS["surface"]))
        for point in points:
            painter.setPen(QPen(QColor(COLORS["green"]), 2))
            painter.drawEllipse(point, 4, 4)

        labels = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom", "Hoje"]
        painter.setPen(QColor(COLORS["muted"]))
        for index, label in enumerate(labels):
            x = rect.left() + index * step
            painter.drawText(int(x - 18), rect.bottom() + 9, 36, 18, Qt.AlignmentFlag.AlignCenter, label)


class DonutChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(170, 170)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(18, 18, 134, 134)
        pen = QPen(QColor("#E7ECE7"), 16)
        painter.setPen(pen)
        painter.drawArc(area, 0, 360 * 16)
        pen.setColor(QColor(COLORS["yellow"]))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(area, 90 * 16, -252 * 16)
        painter.setPen(QColor(COLORS["ink"]))
        painter.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "70%")
        painter.setPen(QColor(COLORS["muted"]))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(QRectF(0, 103, 170, 40), Qt.AlignmentFlag.AlignHCenter, "capacidade usada")


class TaskRow(QFrame):
    def __init__(self, title, subtitle, color, icon):
        super().__init__()
        self.setObjectName("taskRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 11, 12, 11)
        layout.setSpacing(11)
        marker = QLabel()
        marker.setPixmap(icon.pixmap(17, 17))
        marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        marker.setFixedSize(33, 33)
        marker.setStyleSheet(f"background:{color}; border-radius:10px;")
        layout.addWidget(marker)
        text = QVBoxLayout()
        text.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("taskTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("taskSubtitle")
        text.addWidget(title_label)
        text.addWidget(subtitle_label)
        layout.addLayout(text, 1)
        more = QPushButton()
        more.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMenuButton))
        more.setObjectName("iconButton")
        layout.addWidget(more)


class EcoCiclo(QMainWindow):
    def __init__(self):
        super().__init__()
        self.production = 48
        self.is_dark = False
        self.setWindowTitle("EcoCiclo | Gestão de Compostagem")
        self.resize(1180, 760)
        self.setMinimumSize(980, 650)
        self.build_ui()

    def build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(238)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 20)
        side.setSpacing(7)
        brand = QLabel("◒  EcoCiclo")
        brand.setObjectName("brand")
        side.addWidget(brand)
        subtitle = QLabel("GESTÃO SUSTENTÁVEL")
        subtitle.setObjectName("brandSubtitle")
        side.addWidget(subtitle)
        side.addSpacing(36)
        items = [
            ("Visão geral", QStyle.StandardPixmap.SP_ComputerIcon, True),
            ("Meus compostos", QStyle.StandardPixmap.SP_DirIcon, False),
            ("Coletas", QStyle.StandardPixmap.SP_DialogOpenButton, False),
            ("Relatórios", QStyle.StandardPixmap.SP_FileDialogDetailedView, False),
            ("Equipe", QStyle.StandardPixmap.SP_DirHomeIcon, False),
        ]
        for text, pixmap, active in items:
            button = QPushButton(text)
            button.setIcon(self.style().standardIcon(pixmap))
            button.setObjectName("navActive" if active else "nav")
            button.setCheckable(True)
            button.setChecked(active)
            side.addWidget(button)
        side.addStretch()
        help_button = QPushButton("Central de ajuda")
        help_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxQuestion))
        help_button.setObjectName("nav")
        side.addWidget(help_button)
        profile = QLabel("  Marina Costa\n  Administradora")
        profile.setObjectName("profile")
        side.addWidget(profile)
        layout.addWidget(sidebar)

        content = QScrollArea()
        content.setWidgetResizable(True)
        content.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("page")
        content.setWidget(page)
        main = QVBoxLayout(page)
        main.setContentsMargins(34, 28, 34, 32)
        main.setSpacing(20)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Bom dia, Marina 👋")
        title.setObjectName("title")
        title_box.addWidget(title)
        title_box.addWidget(QLabel("Acompanhe a operação da sua compostagem hoje."))
        title_box.itemAt(1).widget().setObjectName("subtitle")
        header.addLayout(title_box)
        header.addStretch()
        notify = QPushButton()
        notify.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        notify.setObjectName("circleButton")
        header.addWidget(notify)
        self.theme_button = QPushButton("🌙  Modo escuro")
        self.theme_button.setObjectName("themeButton")
        self.theme_button.clicked.connect(self.toggle_theme)
        header.addWidget(self.theme_button)
        add_button = QPushButton("  Registrar lote")
        add_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder))
        add_button.setObjectName("primaryButton")
        add_button.clicked.connect(self.register_batch)
        header.addWidget(add_button)
        main.addLayout(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(15)
        icon = self.style().standardIcon
        self.production_card = MetricCard(icon(QStyle.StandardPixmap.SP_DriveHDIcon), "Produção do mês", "48,2 kg", "+12% comparado a junho", "#E1F3E9")
        metrics.addWidget(self.production_card)
        metrics.addWidget(MetricCard(icon(QStyle.StandardPixmap.SP_DialogApplyButton), "Lotes em maturação", "06", "2 prontos para peneirar", "#E8EFFE"))
        metrics.addWidget(MetricCard(icon(QStyle.StandardPixmap.SP_BrowserReload), "Desvios do aterro", "184 kg", "Impacto estimado no mês", "#FFF3D9"))
        main.addLayout(metrics)

        charts = QHBoxLayout()
        trend_card = Card()
        trend_layout = QVBoxLayout(trend_card)
        trend_layout.setContentsMargins(20, 17, 20, 12)
        chart_header = QHBoxLayout()
        chart_header.addWidget(self.section_title("Evolução semanal"))
        chart_header.addStretch()
        period = QPushButton("Últimos 7 dias  ▾")
        period.setObjectName("filterButton")
        chart_header.addWidget(period)
        trend_layout.addLayout(chart_header)
        trend_layout.addWidget(TrendChart())
        charts.addWidget(trend_card, 2)
        capacity = Card()
        capacity_layout = QVBoxLayout(capacity)
        capacity_layout.setContentsMargins(18, 17, 18, 15)
        capacity_layout.addWidget(self.section_title("Capacidade das composteiras"))
        capacity_layout.addWidget(DonutChart(), alignment=Qt.AlignmentFlag.AlignCenter)
        legend = QLabel("●  3 composteiras ativas\n●  1 em manutenção")
        legend.setObjectName("legend")
        capacity_layout.addWidget(legend, alignment=Qt.AlignmentFlag.AlignCenter)
        charts.addWidget(capacity, 1)
        main.addLayout(charts)

        bottom = QHBoxLayout()
        bottom.setSpacing(15)
        tasks = Card()
        task_layout = QVBoxLayout(tasks)
        task_layout.setContentsMargins(20, 17, 20, 17)
        task_head = QHBoxLayout()
        task_head.addWidget(self.section_title("Próximas atividades"))
        task_head.addStretch()
        all_tasks = QPushButton("Ver agenda →")
        all_tasks.setObjectName("textButton")
        task_head.addWidget(all_tasks)
        task_layout.addLayout(task_head)
        task_layout.addSpacing(3)
        task_layout.addWidget(TaskRow("Revirar lote #041", "Hoje, às 15:30", "#FFF0D1", icon(QStyle.StandardPixmap.SP_BrowserReload)))
        task_layout.addWidget(TaskRow("Medir umidade", "Amanhã, às 09:00", "#E4F3EA", icon(QStyle.StandardPixmap.SP_DialogApplyButton)))
        bottom.addWidget(tasks, 2)
        tip = Card()
        tip.setObjectName("tipCard")
        tip_layout = QVBoxLayout(tip)
        tip_layout.setContentsMargins(20, 17, 20, 17)
        tip_layout.addWidget(QLabel("♻  Dica do dia"), alignment=Qt.AlignmentFlag.AlignLeft)
        tip_layout.itemAt(0).widget().setObjectName("tipTitle")
        tip_text = QLabel("Misture materiais secos às sobras úmidas para manter o equilíbrio ideal do seu composto.")
        tip_text.setWordWrap(True)
        tip_text.setObjectName("tipText")
        tip_layout.addWidget(tip_text)
        tip_layout.addStretch()
        tip_button = QPushButton("Ver boas práticas  →")
        tip_button.setObjectName("tipButton")
        tip_layout.addWidget(tip_button)
        bottom.addWidget(tip, 1)
        main.addLayout(bottom)
        layout.addWidget(content, 1)

    def section_title(self, text):
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def register_batch(self):
        self.production += 2
        self.production_card.value_label.setText(f"{self.production},2 kg")
        self.statusBar().showMessage("Novo lote registrado com sucesso.", 3500)

    def toggle_theme(self):
        self.is_dark = not self.is_dark
        COLORS.clear()
        COLORS.update(DARK_COLORS if self.is_dark else LIGHT_COLORS)
        apply_theme(QApplication.instance(), self.is_dark)
        self.theme_button.setText("☀  Modo claro" if self.is_dark else "🌙  Modo escuro")
        for chart in self.findChildren((TrendChart, DonutChart)):
            chart.update()


def apply_theme(app, dark=False):
    palette = {
        "sidebar": "#102D25" if dark else "#183C32",
        "sidebar_hover": "#24483B" if dark else "#285345",
        "card_border": "#30463C" if dark else "#E6ECE7",
        "control": "#24362E" if dark else "#F4F7F4",
        "control_border": "#3A5046" if dark else "#E2E9E3",
        "task": "#203129" if dark else "#F8FAF8",
        "tip": "#1F382D" if dark else "#E2F1E7",
        "tip_border": "#315947" if dark else "#CEE7D7",
        "circle": "#203129" if dark else "white",
    }
    app.setStyleSheet(f"""
        * {{ font-family: 'Segoe UI'; color: {COLORS['ink']}; }}
        QMainWindow, #root, #page {{ background: {COLORS['background']}; }}
        #sidebar {{ background: {palette['sidebar']}; }}
        #brand {{ color: white; font-size: 23px; font-weight: 700; padding-left: 5px; }}
        #brandSubtitle {{ color: #A9C6BA; font-size: 9px; letter-spacing: 1.4px; padding-left: 8px; }}
        #nav, #navActive {{ text-align: left; padding: 11px 13px; border: 0; border-radius: 9px; color: #D1E0D8; font-size: 13px; }}
        #nav:hover {{ background: {palette['sidebar_hover']}; color: white; }}
        #navActive {{ background: #2D7A5D; color: white; font-weight: 600; }}
        #profile {{ background: {palette['sidebar_hover']}; border-radius: 11px; padding: 12px; color: #EAF5EF; font-size: 12px; }}
        #title {{ font-size: 27px; font-weight: 700; }} #subtitle {{ color: {COLORS['muted']}; font-size: 13px; }}
        #card {{ background: {COLORS['surface']}; border: 1px solid {palette['card_border']}; border-radius: 14px; }}
        #metricLabel, #metricDetail {{ color: {COLORS['muted']}; font-size: 12px; }} #metricValue {{ font-size: 25px; font-weight: 700; }}
        #status {{ color: {COLORS['green']}; background: {COLORS['green_light']}; border-radius: 7px; padding: 4px 6px; font-size: 8px; font-weight: 700; }}
        #sectionTitle {{ font-size: 15px; font-weight: 700; }}
        #primaryButton {{ background: {COLORS['green']}; color: white; border: 0; border-radius: 9px; padding: 10px 16px; font-weight: 600; }}
        #primaryButton:hover {{ background: #21664B; }} #circleButton {{ background: {palette['circle']}; border: 1px solid {palette['control_border']}; border-radius: 18px; padding: 8px; }}
        #themeButton {{ background: {palette['control']}; border: 1px solid {palette['control_border']}; border-radius: 9px; padding: 9px 12px; color: {COLORS['ink']}; font-weight: 600; }} #themeButton:hover {{ border-color: {COLORS['green']}; }}
        #filterButton {{ background: {palette['control']}; border: 1px solid {palette['control_border']}; border-radius: 7px; padding: 6px 9px; color: {COLORS['muted']}; }}
        #taskRow {{ background: {palette['task']}; border-radius: 10px; }} #taskTitle {{ font-weight: 600; font-size: 12px; }} #taskSubtitle {{ color: {COLORS['muted']}; font-size: 11px; }}
        #iconButton {{ background: transparent; border: 0; padding: 5px; }} #textButton {{ border: 0; color: {COLORS['green']}; font-weight: 600; }}
        #tipCard {{ background: {palette['tip']}; border: 1px solid {palette['tip_border']}; }} #tipTitle {{ color: {COLORS['green']}; font-weight: 700; font-size: 15px; }} #tipText {{ color: {COLORS['muted']}; font-size: 12px; line-height: 1.4; }}
        #tipButton {{ text-align: left; border: 0; color: {COLORS['green']}; font-weight: 700; padding: 0; }} #legend {{ color: {COLORS['muted']}; font-size: 10px; line-height: 1.7; }}
        QScrollBar:vertical {{ width: 8px; background: transparent; }} QScrollBar::handle:vertical {{ background: #CBD6CE; border-radius: 4px; }}
    """)


if __name__ == "__main__":
    application = QApplication(sys.argv)
    application.setStyle("Fusion")
    apply_theme(application)
    window = EcoCiclo()
    window.show()
    sys.exit(application.exec())

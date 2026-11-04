"""Status fields that remain inside their geometry when the window narrows."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QStyle, QStyleOption


class ElidedLabel(QLabel):
    """Keep the full text/API/tooltip, but clip and elide its painted form."""
    def __init__(self, text="", parent=None, *, elide=Qt.TextElideMode.ElideRight):
        super().__init__(text, parent)
        self.elide_mode = elide
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setToolTip(text)

    def setText(self, text):
        super().setText(str(text))
        self.setToolTip(str(text))
        self.update()

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event):
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option, painter, self)
        rect = self.contentsRect().adjusted(self.margin(), self.margin(), -self.margin(), -self.margin())
        if rect.width() <= 0:
            return
        painter.setClipRect(rect)
        painter.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text(), self.elide_mode, rect.width())
        self.style().drawItemText(painter, rect, int(self.alignment()), self.palette(), self.isEnabled(), text, self.foregroundRole())


class AdaptiveStatusBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusbar")
        self.setFixedHeight(42)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)
        self.status = ElidedLabel("●  Pronto")
        self.status.setObjectName("readyStatus")
        self.status.setMaximumWidth(400)
        self.model_identity = ElidedLabel(elide=Qt.TextElideMode.ElideMiddle)
        self.model_identity.setObjectName("statusText")
        self.model_identity.setMaximumWidth(450)
        self.quota_status = ElidedLabel()
        self.quota_status.setObjectName("quotaStatus")
        self.quota_status.setMaximumWidth(420)
        self.agent_status = ElidedLabel("🤖  Agente IA: ativo")
        self.agent_status.setObjectName("agentStatus")
        self.agent_status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.language = ElidedLabel("Python")
        self.cursor = ElidedLabel("Ln 1, Col 1")
        self.encoding = ElidedLabel("UTF-8    CRLF    4 espacos")
        for label in (self.language, self.cursor, self.encoding):
            label.setObjectName("statusText")
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        for label, stretch in ((self.status, 2), (self.model_identity, 3), (self.quota_status, 2),
                               (self.agent_status, 0), (self.language, 0), (self.cursor, 0), (self.encoding, 0)):
            layout.addWidget(label, stretch)
        self._adapt()

    def _adapt(self):
        width = self.width()
        self.encoding.setVisible(width >= 1200)
        self.language.setVisible(width >= 1000)
        self.cursor.setVisible(width >= 900)
        self.agent_status.setVisible(width >= 720)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adapt()

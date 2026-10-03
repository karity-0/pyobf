import keyword
import re

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextFormat
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from .themes import THEMES


class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, document, theme):
        super().__init__(document)
        self.theme = theme
        self.keyword_pattern = re.compile(r"\b(?:" + "|".join(keyword.kwlist + ["match", "case"]) + r")\b")

    def paint(self, start, length, color, bold=False):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Weight.DemiBold)
        self.setFormat(self._offsets[start], self._offsets[start + length] - self._offsets[start], fmt)

    @staticmethod
    def string_end(text, start, quote):
        while True:
            end = text.find(quote, start)
            if end < 0:
                return -1
            slashes, index = 0, end - 1
            while index >= 0 and text[index] == "\\":
                slashes += 1
                index -= 1
            if slashes % 2 == 0:
                return end + len(quote)
            start = end + len(quote)

    def highlightBlock(self, text):
        t = self.theme
        self._offsets = [0]
        for character in text:
            self._offsets.append(self._offsets[-1] + (2 if ord(character) > 65535 else 1))
        self.setCurrentBlockState(0)
        for match in self.keyword_pattern.finditer(text):
            self.paint(match.start(), len(match.group()), t.keyword, True)
        for match in re.finditer(r"\b(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)\b", text):
            self.paint(match.start(), len(match.group()), t.number)
        for match in re.finditer(r"\b(?:def|class)\s+(\w+)", text):
            self.paint(match.start(1), len(match.group(1)), t.accent, True)
        for match in re.finditer(r"@(?:protect_start|protect_end)\b|@\{|\b(?:print|range|len|str|int|bytes|super)\b", text):
            self.paint(match.start(), len(match.group()), t.accent)
        index = 0
        if self.previousBlockState() in (1, 2):
            quote = "'''" if self.previousBlockState() == 1 else '"""'
            end = self.string_end(text, 0, quote)
            self.paint(0, len(text) if end < 0 else end, t.string)
            if end < 0:
                self.setCurrentBlockState(self.previousBlockState())
                return
            index = end
        while index < len(text):
            char = text[index]
            if char == "#":
                self.paint(index, len(text) - index, t.comment)
                break
            if char in "\"'":
                quote = char * 3 if text.startswith(char * 3, index) else char
                end = self.string_end(text, index + len(quote), quote)
                start = index
                while start > 0 and text[start - 1] in "rRuUbBfF":
                    start -= 1
                if start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
                    start = index
                self.paint(start, (len(text) if end < 0 else end) - start, t.string)
                if end < 0:
                    if len(quote) == 3:
                        self.setCurrentBlockState(1 if char == "'" else 2)
                    break
                index = end
            else:
                index += 1


class Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, readonly=False, parent=None):
        super().__init__(parent)
        self.theme = THEMES["white"]
        self.setReadOnly(readonly)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Cascadia Code", 11)
        font.setFamilies(["Cascadia Code", "Consolas", "DejaVu Sans Mono", "monospace"])
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.gutter = Gutter(self)
        self.highlighter = PythonHighlighter(self.document(), self.theme)
        self.blockCountChanged.connect(self.update_gutter_width)
        self.updateRequest.connect(self.update_gutter)
        self.cursorPositionChanged.connect(self.highlight_line)
        self.update_gutter_width()
        self.highlight_line()

    def set_theme(self, theme):
        self.theme = theme
        self.highlighter.theme = theme
        self.highlighter.rehighlight()
        self.highlight_line()
        self.gutter.update()

    def gutter_width(self):
        return 24 + self.fontMetrics().horizontalAdvance("9") * max(2, len(str(self.blockCount())))

    def update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.gutter.setGeometry(QRect(rect.left(), rect.top(), self.gutter_width(), rect.height()))

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor(self.theme.surface))
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            height = round(self.blockBoundingRect(block).height())
            if block.isVisible() and top + height >= event.rect().top():
                painter.setPen(QColor(self.theme.accent if number == self.textCursor().blockNumber() else self.theme.muted))
                painter.drawText(0, top, self.gutter.width() - 12, height, Qt.AlignmentFlag.AlignRight, str(number + 1))
            top += height
            number += 1
            block = block.next()

    def highlight_line(self):
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor(self.theme.line))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
        self.gutter.update()

    def keyPressEvent(self, event):
        if not self.isReadOnly() and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and self.textCursor().hasSelection():
            cursor = self.textCursor()
            start, end = cursor.selectionStart(), cursor.selectionEnd()
            first = self.document().findBlock(start)
            last = self.document().findBlock(end)
            if end == last.position() and last != first:
                last = last.previous()
            cursor.beginEditBlock()
            block = first
            while block.isValid():
                cursor.setPosition(block.position())
                if event.key() == Qt.Key.Key_Tab:
                    cursor.insertText("    ")
                else:
                    count = min(4, len(block.text()) - len(block.text().lstrip(" ")))
                    for _ in range(count):
                        cursor.deleteChar()
                if block == last:
                    break
                block = block.next()
            cursor.endEditBlock()
            return
        if not self.isReadOnly() and event.key() == Qt.Key.Key_Tab:
            self.insertPlainText("    ")
            return
        if not self.isReadOnly() and event.key() == Qt.Key.Key_Backtab:
            cursor = self.textCursor()
            cursor.beginEditBlock()
            cursor.movePosition(cursor.MoveOperation.StartOfBlock)
            line = cursor.block().text()
            count = min(4, len(line) - len(line.lstrip(" ")))
            for _ in range(count):
                cursor.deleteChar()
            cursor.endEditBlock()
            return
        if not self.isReadOnly() and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers():
            cursor = self.textCursor()
            line = cursor.block().text()[:cursor.positionInBlock()]
            indent = re.match(r"\s*", line).group()
            if line.rstrip().endswith(":"):
                indent += "    "
            self.insertPlainText("\n" + indent)
            return
        super().keyPressEvent(event)

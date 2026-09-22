"""按对话保存本次运行的草稿，异步结果只更新原始归属。"""

from copy import deepcopy
from dataclasses import dataclass, field
from PyQt5.QtCore import QObject, pyqtSignal


@dataclass
class Draft:
    text: str = ""
    images: list = field(default_factory=list)
    revision: int = 0
    text_revision: int = 0


class DraftStore(QObject):
    changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drafts = {}
        self._submitted = {}
        self._deleted = set()

    def get(self, chat_id):
        return deepcopy(self._drafts.get(chat_id, Draft()))

    def set(self, chat_id, text, images):
        if chat_id in self._deleted:
            return
        old = self.get(chat_id)
        if old.text == text and old.images == images:
            return
        self._drafts[chat_id] = Draft(
            text,
            deepcopy(images),
            old.revision + 1,
            old.text_revision + (old.text != text),
        )
        self.changed.emit(chat_id)

    def update_attachment(self, payload):
        draft_id = payload.get("draft_attachment_id")
        for chat_id, draft in list(self._drafts.items()):
            images = deepcopy(draft.images)
            for index, image in enumerate(images):
                if image.get("draft_attachment_id") == draft_id:
                    images[index] = dict(payload)
                    self.set(chat_id, draft.text, images)
                    return

    def submitted(self, chat_id, turn_id):
        self._submitted[turn_id] = (chat_id, self.get(chat_id))

    def committed(self, turn_id):
        item = self._submitted.pop(turn_id, None)
        if item is None:
            return False
        chat_id, sent = item
        current = self.get(chat_id)
        ids = {p["draft_attachment_id"] for p in sent.images}
        images = [p for p in current.images if p.get("draft_attachment_id") not in ids]
        self.set(
            chat_id,
            "" if current.text_revision == sent.text_revision else current.text,
            images,
        )
        return True

    def insert_recognition(self, chat_id, revision, cursor, text):
        if chat_id in self._deleted:
            return
        draft = self.get(chat_id)
        if draft.text_revision != revision:
            cursor = len(draft.text)
        cursor = max(0, min(cursor, len(draft.text)))
        self.set(
            chat_id, draft.text[:cursor] + text + draft.text[cursor:], draft.images
        )

    def delete(self, chat_id):
        self._deleted.add(chat_id)
        self._drafts.pop(chat_id, None)
        self._submitted = {
            key: value for key, value in self._submitted.items() if value[0] != chat_id
        }


class DraftBinding(QObject):
    """输入控件只是草稿视图；同步不抢焦点、不触发附件删除。"""

    def __init__(self, store, view, chat_id):
        super().__init__(view)
        self.store, self.view, self.chat_id = store, view, chat_id
        self.updating = False
        view.text_edit.textChanged.connect(self.write)
        view.draftStateChanged.connect(self.write)
        store.changed.connect(self.read)
        self.read(chat_id)

    def switch(self, chat_id):
        self.chat_id = chat_id
        self.read(chat_id)

    def write(self):
        if not self.updating:
            self.store.set(
                self.chat_id,
                self.view.toPlainText(),
                self.view.pending_draft_payloads(),
            )

    def read(self, chat_id):
        if chat_id != self.chat_id:
            return
        draft = self.store.get(chat_id)
        self.updating = True
        previous = self.view.blockSignals(True)
        try:
            if self.view.toPlainText() != draft.text:
                cursor = self.view.text_edit.textCursor()
                position = cursor.position()
                self.view.text_edit.setPlainText(draft.text)
                cursor = self.view.text_edit.textCursor()
                cursor.setPosition(min(position, len(draft.text)))
                self.view.text_edit.setTextCursor(cursor)
            if self.view.pending_draft_payloads() != draft.images:
                self.view.clear_draft_images(notify_manager=False)
                for payload in draft.images:
                    self.view.add_managed_draft(payload)
        finally:
            self.view.blockSignals(previous)
            self.updating = False

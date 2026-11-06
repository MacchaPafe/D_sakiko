from qfluentwidgets import FluentIconBase, Theme, getIconColor
from enum import Enum


class MyFluentIcon(FluentIconBase, Enum):
    USER = "user"

    def path(self, theme=Theme.AUTO) -> str:
        return f"icons/{self.value}_{getIconColor(theme)}.svg"

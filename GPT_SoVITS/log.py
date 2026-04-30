from __future__ import annotations

import datetime as _datetime
import logging
import pathlib
import re
import sys
import traceback
import zipfile
import typing
from logging.handlers import QueueHandler, QueueListener, TimedRotatingFileHandler
from types import TracebackType
from typing import Final

from rich.console import Console
from rich.logging import RichHandler

if typing.TYPE_CHECKING:
    import multiprocessing


DEFAULT_LOGGER_PREFIX: Final[str] = "d_sakiko"
DEFAULT_LOG_DIR: Final[pathlib.Path] = pathlib.Path("../logs")
DEFAULT_LOG_FORMAT: Final[str] = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s"
DEFAULT_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"

_DATE_IN_LOG_NAME_RE: Final[re.Pattern[str]] = re.compile(r"\.(\d{4}-\d{2}-\d{2})(?:\.log)?$")
_console_handler: logging.Handler | None = None
_queue_listener: QueueListener | None = None
_log_queue: multiprocessing.Queue | None = None


def setup_main_logging(
    log_queue: multiprocessing.Queue,
    *,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    error_file_level: int = logging.WARNING,
    log_dir: str | pathlib.Path = DEFAULT_LOG_DIR,
    backup_days: int = 14,
    capture_warnings: bool = True,
    reset_existing_handlers: bool = True,
    install_except_hook: bool = True,
) -> QueueListener:
    """在主进程中初始化项目日志系统。该函数只能被调用一次（程序一般也只有一个主进程吧）。

    初始化后，当前日志会写入 ``app.log`` 和 ``error.log``；每天午夜自动轮转为
    ``app.log.YYYY-MM-DD`` / ``error.log.YYYY-MM-DD``，并按 ``backup_days`` 清理旧日志。
    主进程与子进程中的项目 logger 都只安装 ``QueueHandler``，真正写入控制台和
    文件的 handler 只属于主进程的 ``QueueListener``。这是多进程日志写入的推荐结构。

    参数:
        log_queue: 一个跨进程传输消息的队列。你可以新建一个队列然后传递进来。在子进程中使用 setup_worker_logging 函数时，需要传入相同的队列
        console_level: 控制台显示的最低日志等级。用户想“只看 WARNING 以上”时改这里。
        file_level: ``app.log`` 保存的最低日志等级。通常建议保存 ``DEBUG``，方便排查 bug。
        log_dir: 日志目录。相对路径会以当前工作目录为基准。
        backup_days: 保留多少天的历史日志文件。
        capture_warnings: 是否把 ``warnings.warn`` 的内容也写入日志。
        reset_existing_handlers: 是否清理旧 handler。开发热重载或重复初始化时建议保持 True。
        install_except_hook: 是否安装未捕获异常日志钩子。安装后主线程未捕获的异常会进入日志文件。

    返回:
        一个 ``logging.handlers.QueueListener`` 实例。通常不需要关心它，但在程序退出前记得调用 ``listener.stop()``。    
    """

    global _console_handler, _queue_listener, _log_queue

    log_path = pathlib.Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    console_handler = _build_console_handler(console_level=console_level)
    app_file_handler = _build_timed_file_handler(
        filename=log_path / "app.log",
        level=file_level,
        backup_days=backup_days,
    )
    error_file_handler = _build_timed_file_handler(
        filename=log_path / "error.log",
        level=error_file_level,
        backup_days=backup_days,
    )

    _console_handler = console_handler
    _log_queue = log_queue

    logging.captureWarnings(capture_warnings)

    if install_except_hook:
        install_excepthook()
    
    listener = QueueListener(log_queue, console_handler, app_file_handler, error_file_handler, respect_handler_level=True)
    listener.start()
    _queue_listener = listener

    _setup_queue_logging_for_current_process(
        log_queue=log_queue,
        level=logging.DEBUG,
        capture_warnings=capture_warnings,
        reset_existing_handlers=reset_existing_handlers,
    )

    return listener


def setup_worker_logging(
    log_queue: multiprocessing.Queue,
    *,
    level: int = logging.DEBUG,
    capture_warnings: bool = False,
    reset_existing_handlers: bool = True,
    silence_multiprocessing_logger: bool = True,
    install_except_hook: bool = True,
) -> None:
    """在子进程中初始化项目日志系统。在任何一个新建的子进程中，都需要调用该函数一次，传入主进程创建的同一个日志队列。这样才能把子进程的日志正确发送到主进程。

    子进程不应该直接创建 ``FileHandler`` 或 ``TimedRotatingFileHandler`` 写入同一个
    日志文件，而是只安装一个 ``QueueHandler``，把日志记录发送给主进程。主进程中的
    ``QueueListener`` 会负责统一写入控制台和日志文件。

    参数:
        log_queue: 主进程传入的跨进程日志队列。
        level: 子进程项目 logger 接收的最低日志等级。通常可以保持 ``DEBUG``。
        capture_warnings: 是否把子进程中的 ``warnings.warn`` 也发送到日志队列。
        reset_existing_handlers: 是否清理当前进程中已继承或已安装的项目日志 handler。
        silence_multiprocessing_logger: 是否把 multiprocessing 内部 logger 限制到 WARNING 以上。
        install_except_hook: 是否安装未捕获异常日志钩子。
    """

    _setup_queue_logging_for_current_process(
        log_queue=log_queue,
        level=level,
        capture_warnings=capture_warnings,
        reset_existing_handlers=reset_existing_handlers,
    )

    if silence_multiprocessing_logger:
        _silence_multiprocessing_logger()

    if install_except_hook:
        install_excepthook()


def setup_logging(
    *,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    error_file_level: int = logging.WARNING,
    log_dir: str | pathlib.Path = DEFAULT_LOG_DIR,
    backup_days: int = 14,
    capture_warnings: bool = True,
    reset_existing_handlers: bool = True,
    install_except_hook: bool = True,
) -> QueueListener:
    """初始化单主进程日志系统的便捷函数。

    如果暂时还没有手动创建 ``multiprocessing.Queue``，可以使用这个函数。它会创建
    一个日志队列并调用 ``setup_main_logging``。将来创建子进程时，可以用
    ``get_log_queue()`` 取出同一个队列并传给 ``setup_worker_logging``。
    """

    import multiprocessing

    log_queue = multiprocessing.Queue(-1)
    return setup_main_logging(
        log_queue,
        console_level=console_level,
        file_level=file_level,
        error_file_level=error_file_level,
        log_dir=log_dir,
        backup_days=backup_days,
        capture_warnings=capture_warnings,
        reset_existing_handlers=reset_existing_handlers,
        install_except_hook=install_except_hook,
    )


def get_log_queue() -> multiprocessing.Queue:
    """返回当前主进程日志队列。

    子进程需要复用同一个队列时，可以在主进程中调用这个函数，然后把返回值作为参数
    传给 ``multiprocessing.Process``。
    """

    if _log_queue is None:
        raise RuntimeError("日志系统尚未初始化，请先调用 setup_main_logging() 或 setup_logging()。")
    return _log_queue


def shutdown_logging(listener: QueueListener | None = None) -> None:
    """停止日志监听器并关闭由监听器管理的 handler。"""

    global _queue_listener

    current_listener = listener or _queue_listener
    if current_listener is None:
        return

    current_listener.stop()
    for handler in current_listener.handlers:
        handler.close()

    if current_listener is _queue_listener:
        _queue_listener = None


def get_logger(name: str | None = None) -> logging.Logger:
    """获取 logger。

    推荐在每个模块顶部这样使用::

        from log import get_logger

        logger = get_logger(__name__)

    然后在代码中调用 ``logger.info(...)``、``logger.warning(...)``、
    ``logger.exception(...)`` 等方法。

    参数:
        name: 模块名。传入 ``__name__`` 时，日志中能看到消息来自哪个模块。
        实际使用的 logger 名称会自动加上 d_sakiko 前缀（以免和其他库的 logger 冲突）。
        例如 ``get_logger("main")`` 会返回名为 ``d_sakiko.main`` 的 logger。

    返回:
        标准库 ``logging.Logger`` 实例。
    """

    if name:
        if name != DEFAULT_LOGGER_PREFIX and not name.startswith(f"{DEFAULT_LOGGER_PREFIX}."):
            name = f"{DEFAULT_LOGGER_PREFIX}.{name}"

        return logging.getLogger(name)
    return logging.getLogger(DEFAULT_LOGGER_PREFIX)


def set_console_level(level: int) -> None:
    """动态修改控制台日志等级。

    这个函数适合接到设置界面中，例如用户选择“只显示 ERROR 以上日志”时调用。
    文件日志不受影响，仍会按 ``setup_logging`` 中设置的 ``file_level`` 持久化保存。

    参数:
        level: 新的控制台最低日志等级，可以是 ``logging.WARNING`` 或 ``"WARNING"``。

    异常:
        RuntimeError: 尚未调用 ``setup_logging`` 时抛出。
    """

    if _console_handler is None:
        raise RuntimeError("日志系统尚未初始化，请先调用 setup_logging()。")
    _console_handler.setLevel(level)


def export_logs(
    *,
    target_zip: str | pathlib.Path | None = None,
    log_dir: str | pathlib.Path = DEFAULT_LOG_DIR,
    start_date: _datetime.date | str | None = None,
    end_date: _datetime.date | str | None = None,
    include_current: bool = True,
) -> pathlib.Path:
    """导出指定日期范围内的日志为 zip 文件。

    用户汇报 bug 时，可以调用这个函数打包日志。对于当前正在写入的 ``app.log`` 和
    ``error.log``，由于文件名里没有日期，默认会一起导出；历史日志会根据文件名中的
    ``YYYY-MM-DD`` 进行日期筛选。

    参数:
        target_zip: 输出 zip 路径。为 None 时自动生成到日志目录中。
        log_dir: 日志目录。
        start_date: 起始日期，包含当天。支持 ``date`` 或 ``"YYYY-MM-DD"``。
        end_date: 结束日期，包含当天。支持 ``date`` 或 ``"YYYY-MM-DD"``。
        include_current: 是否包含当前日志文件 ``app.log`` 和 ``error.log``。

    返回:
        生成的 zip 文件路径。
    """

    log_path = pathlib.Path(log_dir)
    start = _parse_date(start_date)
    end = _parse_date(end_date)

    if start and end and start > end:
        raise ValueError("start_date 不能晚于 end_date。")

    if target_zip is None:
        today = _datetime.date.today().isoformat()
        target_zip_path = log_path / f"logs_export_{today}.zip"
    else:
        target_zip_path = pathlib.Path(target_zip)

    target_zip_path.parent.mkdir(parents=True, exist_ok=True)

    log_files = _select_log_files(
        log_dir=log_path,
        start_date=start,
        end_date=end,
        include_current=include_current,
    )

    with zipfile.ZipFile(target_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for log_file in log_files:
            archive.write(log_file, arcname=log_file.name)

    return target_zip_path


def excepthook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
    """把未捕获异常写入日志文件。

    可以在程序入口中设置 ``sys.excepthook = excepthook``。这样主线程未捕获的异常
    会进入日志文件，用户反馈 bug 时不会只剩控制台截图。
    """

    get_logger().critical(
        "程序发生未捕获异常",
        exc_info=(exc_type, exc, tb),
    )


def install_excepthook() -> None:
    """安装未捕获异常日志钩子。"""

    sys.excepthook = excepthook


def format_exception(exc: BaseException) -> str:
    """把异常对象格式化为字符串。

    只有在确实需要把异常文本放进 UI 或消息框时才建议使用。写日志时请优先使用
    ``logger.exception(...)``。
    """

    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _build_console_handler(*, console_level: int) -> logging.Handler:
    """创建控制台 handler。"""
    handler = RichHandler(
        console=Console(stderr=True),
        rich_tracebacks=True,
        show_time=True,
        show_level=True,
        show_path=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s", datefmt=DEFAULT_DATE_FORMAT))
   
    handler.setLevel(console_level)
    return handler


def _build_timed_file_handler(
    *,
    filename: pathlib.Path,
    level: int,
    backup_days: int,
) -> TimedRotatingFileHandler:
    """创建按日期轮转的文件 handler。"""

    handler = TimedRotatingFileHandler(
        filename=filename,
        when="midnight",
        interval=1,
        backupCount=backup_days,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT, datefmt=DEFAULT_DATE_FORMAT))
    return handler


def _remove_handlers(logger: logging.Logger) -> None:
    """移除并关闭 logger 上已经存在的 handler。"""

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()


def _setup_queue_logging_for_current_process(
    *,
    log_queue: multiprocessing.Queue,
    level: int,
    capture_warnings: bool,
    reset_existing_handlers: bool,
) -> None:
    """为当前进程的项目 logger 安装 QueueHandler。"""

    queue_handler = QueueHandler(log_queue)
    queue_handler.setLevel(level)

    app_logger = logging.getLogger(DEFAULT_LOGGER_PREFIX)
    app_logger.setLevel(level)
    app_logger.propagate = False

    if reset_existing_handlers:
        _remove_handlers(app_logger)

    app_logger.addHandler(queue_handler)

    logging.captureWarnings(capture_warnings)
    if capture_warnings:
        warnings_logger = logging.getLogger("py.warnings")
        warnings_logger.setLevel(logging.WARNING)
        warnings_logger.propagate = False
        if reset_existing_handlers:
            _remove_handlers(warnings_logger)
        warnings_logger.addHandler(queue_handler)


def _silence_multiprocessing_logger() -> None:
    """避免 multiprocessing 内部 DEBUG 日志进入同一个日志队列。"""

    import multiprocessing

    multiprocessing_logger = multiprocessing.get_logger()
    multiprocessing_logger.handlers.clear()
    multiprocessing_logger.setLevel(logging.WARNING)
    multiprocessing_logger.propagate = False


def _parse_date(value: _datetime.date | str | None) -> _datetime.date | None:
    """解析日期参数。"""

    if value is None:
        return None
    if isinstance(value, _datetime.date):
        return value
    return _datetime.date.fromisoformat(value)


def _select_log_files(
    *,
    log_dir: pathlib.Path,
    start_date: _datetime.date | None,
    end_date: _datetime.date | None,
    include_current: bool,
) -> list[pathlib.Path]:
    """筛选要导出的日志文件。"""

    if not log_dir.exists():
        return []

    selected: list[pathlib.Path] = []
    for path in sorted(log_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix == ".zip":
            continue
        if path.name in {"app.log", "error.log"}:
            if include_current:
                selected.append(path)
            continue

        log_date = _extract_date_from_log_name(path.name)
        if log_date is None:
            continue
        if start_date and log_date < start_date:
            continue
        if end_date and log_date > end_date:
            continue
        selected.append(path)

    return selected


def _extract_date_from_log_name(filename: str) -> _datetime.date | None:
    """从轮转日志文件名中提取日期。"""

    match = _DATE_IN_LOG_NAME_RE.search(filename)
    if match is None:
        return None
    return _datetime.date.fromisoformat(match.group(1))

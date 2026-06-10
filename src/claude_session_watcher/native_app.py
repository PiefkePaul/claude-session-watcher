from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .background_service import (
    BackgroundServiceError,
    background_service_status,
    install_background_service,
    restart_background_service,
    start_background_service,
    stop_background_service,
    uninstall_background_service,
)
from .desktop_runtime import (
    MODE_INSTALLED,
    agent_control_path,
    agent_lock_path,
    clear_agent_pid,
    load_mode_state,
    process_running,
    send_agent_command,
    write_agent_pid,
)
from .formatting import build_ui_watcher, format_timestamp
from .insights import build_usage_insights
from .service_control import service_status, start_service, stop_service
from .settings import Settings
from .store import Store

_UI_STATE_FILENAME = "native_ui_state.json"
_ALL_ACCOUNTS_LABEL = "All Accounts"


@dataclass(slots=True)
class UiWatcherRow:
    watcher_id: int
    account_id: int
    account_name: str
    status: str
    enabled: bool
    sessions: str
    five_hour: str
    seven_day: str
    burn_five_hour: str
    reset_five_hour: str
    reset_seven_day: str
    last_check: str
    source: str


@dataclass(slots=True)
class UiSessionRow:
    session_id: int
    account_id: int
    account_name: str
    title: str
    selected: bool
    kind: str
    status: str
    controllable: bool
    last_seen: str
    session_id_text: str


def run_native_app(settings: Settings) -> int:
    try:
        from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
        from PySide6.QtGui import QAction, QIcon
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDialog,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMenu,
            QPlainTextEdit,
            QPushButton,
            QStyle,
            QSystemTrayIcon,
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:  # pragma: no cover - runtime dependency path
        raise SystemExit(
            "PySide6 is not installed. Install native extras first: pip install -e \".[native]\""
        ) from exc

    class _SubprocessWorker(QObject):
        finished = Signal(str, int, str)

        def __init__(self, label: str, command: list[str]):
            super().__init__()
            self.label = label
            self.command = command

        @Slot()
        def run(self) -> None:
            run_kwargs: dict[str, object] = {
                "capture_output": True,
                "text": True,
                "check": False,
            }
            if os.name == "nt":
                run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(self.command, **run_kwargs)
            output = (result.stdout + "\n" + result.stderr).strip()
            self.finished.emit(self.label, result.returncode, output)

    class NativeMainWindow(QMainWindow):
        def __init__(self, app: QApplication, app_settings: Settings):
            super().__init__()
            self.app = app
            self.settings = app_settings
            self.store = _store(self.settings)
            self.threads: list[QThread] = []
            self._tray: QSystemTrayIcon | None = None
            self._watcher_rows: list[UiWatcherRow] = []
            self._running_cli_labels: set[str] = set()
            self._worker_callbacks: dict[str, Callable[[str, int, str], None]] = {}
            self._log_expanded = False
            self._first_launch = self._register_launch()
            self._mode_state = load_mode_state(self.settings)
            self._installed_mode = self._mode_state.mode == MODE_INSTALLED

            self.setWindowTitle("Claude Session Watcher - Native")
            self.resize(1400, 900)
            icon = _load_app_icon(QIcon)
            if not icon.isNull():
                self.setWindowIcon(icon)
            self._build_ui()
            self._build_tray()
            self._ensure_installed_mode_runtime()

            self.refresh_timer = QTimer(self)
            self.refresh_timer.timeout.connect(self.refresh_snapshot)
            self.refresh_timer.start(5000)
            self.control_timer = QTimer(self)
            self.control_timer.timeout.connect(self._poll_control_commands)
            self.control_timer.start(900)
            self.refresh_snapshot()

        def should_start_visible(self) -> bool:
            if os.getenv("CSW_NATIVE_SHOW_WINDOW") == "1":
                return True
            if self._installed_mode:
                return self._first_launch
            if self._first_launch:
                return True
            return self._tray is None

        def _ensure_installed_mode_runtime(self) -> None:
            if not self._installed_mode:
                return
            try:
                status = service_status(self.settings)
                if not status.running:
                    start_service(self.settings)
            except Exception:  # noqa: BLE001
                pass

        def _build_ui(self) -> None:
            root = QWidget(self)
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)
            layout.setSpacing(8)

            title = QLabel("Claude Session Watcher")
            title.setStyleSheet("font-size: 20px; font-weight: 700;")
            layout.addWidget(title)

            self.runtime_label = QLabel("")
            self.daemon_label = QLabel("")
            self.background_label = QLabel("")
            layout.addWidget(self.runtime_label)
            layout.addWidget(self.daemon_label)
            layout.addWidget(self.background_label)

            controls = QHBoxLayout()
            self.btn_start = QPushButton("Start Watcher")
            self.btn_stop = QPushButton("Stop Watcher")
            self.btn_restart = QPushButton("Restart Watcher")
            self.btn_check = QPushButton("Check Now")
            self.btn_refresh = QPushButton("Refresh")
            controls.addWidget(self.btn_start)
            controls.addWidget(self.btn_stop)
            controls.addWidget(self.btn_restart)
            controls.addWidget(self.btn_check)
            controls.addWidget(self.btn_refresh)
            controls.addStretch(1)
            layout.addLayout(controls)

            service_controls = QHBoxLayout()
            self.btn_service_install = QPushButton("Install OS Service")
            self.btn_service_uninstall = QPushButton("Uninstall OS Service")
            self.btn_service_start = QPushButton("Start OS Service")
            self.btn_service_stop = QPushButton("Stop OS Service")
            self.btn_service_restart = QPushButton("Restart OS Service")
            service_controls.addWidget(self.btn_service_install)
            service_controls.addWidget(self.btn_service_uninstall)
            service_controls.addWidget(self.btn_service_start)
            service_controls.addWidget(self.btn_service_stop)
            service_controls.addWidget(self.btn_service_restart)
            service_controls.addStretch(1)
            layout.addLayout(service_controls)

            settings_group = QGroupBox("Quick Settings")
            settings_layout = QGridLayout(settings_group)
            self.keepalive_checkbox = QCheckBox("Browser keepalive")
            self.keepalive_checkbox.setChecked(bool(self.settings.browser_keepalive))
            self.auto_pro_checkbox = QCheckBox("Auto switch to Pro plan")
            self.auto_pro_checkbox.setChecked(bool(self.settings.auto_switch_to_pro_plan))
            self.refresh_combo = QComboBox()
            self.refresh_combo.addItems(["2s", "5s", "10s", "20s"])
            self.refresh_combo.setCurrentText("5s")
            self.btn_apply_settings = QPushButton("Apply")
            settings_layout.addWidget(self.keepalive_checkbox, 0, 0)
            settings_layout.addWidget(self.auto_pro_checkbox, 0, 1)
            settings_layout.addWidget(QLabel("Refresh interval"), 1, 0)
            settings_layout.addWidget(self.refresh_combo, 1, 1)
            settings_layout.addWidget(self.btn_apply_settings, 0, 2, 2, 1)
            layout.addWidget(settings_group)

            account_row = QHBoxLayout()
            self.account_label = QLabel("Accounts")
            self.account_combo = QComboBox()
            self.account_add_button = QPushButton("Add Account")
            self.account_login_button = QPushButton("Login Account")
            self.account_delete_button = QPushButton("Delete Account")
            account_row.addWidget(self.account_label)
            account_row.addWidget(self.account_combo)
            account_row.addWidget(self.account_add_button)
            account_row.addWidget(self.account_login_button)
            account_row.addWidget(self.account_delete_button)
            account_row.addStretch(1)
            layout.addLayout(account_row)

            session_filter_row = QHBoxLayout()
            self.session_scope_label = QLabel("Session Scope")
            self.session_scope_combo = QComboBox()
            self.session_scope_combo.addItem(_ALL_ACCOUNTS_LABEL, userData=None)
            self.session_scope_combo.currentIndexChanged.connect(self._reload_sessions_only)
            self.manage_sessions_button = QPushButton("Manage Sessions")
            self.manage_sessions_button.clicked.connect(self.open_session_manager)
            session_filter_row.addWidget(self.session_scope_label)
            session_filter_row.addWidget(self.session_scope_combo)
            session_filter_row.addWidget(self.manage_sessions_button)
            session_filter_row.addStretch(1)
            layout.addLayout(session_filter_row)

            self.watchers_table = QTableWidget(0, 12)
            self.watchers_table.setHorizontalHeaderLabels(
                [
                    "Watcher",
                    "Account",
                    "Status",
                    "Enabled",
                    "Sessions",
                    "5h",
                    "7d",
                    "Burn 5h",
                    "Reset 5h",
                    "Reset 7d",
                    "Last Check",
                    "Source",
                ]
            )
            self.watchers_table.setSelectionBehavior(QTableWidget.SelectRows)
            self.watchers_table.setSelectionMode(QTableWidget.SingleSelection)
            self.watchers_table.setMinimumHeight(230)
            self.watchers_table.cellClicked.connect(self._on_watcher_row_clicked)
            layout.addWidget(self.watchers_table, 3)

            self.sessions_table = QTableWidget(0, 9)
            self.sessions_table.setHorizontalHeaderLabels(
                [
                    "ID",
                    "Account",
                    "Title",
                    "Selected",
                    "Kind",
                    "Status",
                    "Control",
                    "Last Seen",
                    "Session Key",
                ]
            )
            self.sessions_table.setSelectionBehavior(QTableWidget.SelectRows)
            self.sessions_table.setSelectionMode(QTableWidget.SingleSelection)
            layout.addWidget(self.sessions_table, 4)

            log_bar = QHBoxLayout()
            log_label = QLabel("Recent Events")
            self.log_toggle_button = QPushButton("Show Full Log")
            self.log_toggle_button.clicked.connect(self.toggle_log_expansion)
            log_bar.addWidget(log_label)
            log_bar.addStretch(1)
            log_bar.addWidget(self.log_toggle_button)
            layout.addLayout(log_bar)

            self.events_preview = QPlainTextEdit()
            self.events_preview.setReadOnly(True)
            self.events_preview.setFixedHeight(68)
            self.events_preview.setPlaceholderText("No recent events.")
            layout.addWidget(self.events_preview)

            self.events_log = QPlainTextEdit()
            self.events_log.setReadOnly(True)
            self.events_log.setPlaceholderText("Recent events")
            self.events_log.setVisible(False)
            layout.addWidget(self.events_log, 2)

            self.btn_start.clicked.connect(self.action_start_watcher)
            self.btn_stop.clicked.connect(self.action_stop_watcher)
            self.btn_restart.clicked.connect(self.action_restart_watcher)
            self.btn_check.clicked.connect(self.action_check_now)
            self.btn_refresh.clicked.connect(self.refresh_snapshot)
            self.btn_service_install.clicked.connect(self.action_service_install)
            self.btn_service_uninstall.clicked.connect(self.action_service_uninstall)
            self.btn_service_start.clicked.connect(self.action_service_start)
            self.btn_service_stop.clicked.connect(self.action_service_stop)
            self.btn_service_restart.clicked.connect(self.action_service_restart)
            self.btn_apply_settings.clicked.connect(self.apply_quick_settings)
            self.account_add_button.clicked.connect(self.action_account_add)
            self.account_login_button.clicked.connect(self.action_account_login)
            self.account_delete_button.clicked.connect(self.action_account_delete)

        def _build_tray(self) -> None:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self._error("System tray/status bar is not available on this OS session.")
                return
            tray = QSystemTrayIcon(self)
            icon = _load_app_icon(QIcon)
            if icon.isNull():
                icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
            tray.setIcon(icon if not icon.isNull() else QIcon())
            tray.setToolTip("Claude Session Watcher")
            menu = QMenu(self)
            show_action = QAction("Open Dashboard", self)
            hide_action = QAction("Hide Window", self)
            start_action = QAction("Start Watcher", self)
            stop_action = QAction("Stop Watcher", self)
            check_action = QAction("Check Now", self)
            refresh_action = QAction("Refresh", self)
            quit_action = QAction("Quit", self)
            show_action.triggered.connect(self.show_and_focus)
            hide_action.triggered.connect(self.hide)
            start_action.triggered.connect(self.action_start_watcher)
            stop_action.triggered.connect(self.action_stop_watcher)
            check_action.triggered.connect(self.action_check_now)
            refresh_action.triggered.connect(self.refresh_snapshot)
            quit_action.triggered.connect(self.exit_application)
            menu.addAction(show_action)
            menu.addAction(hide_action)
            menu.addSeparator()
            menu.addAction(start_action)
            menu.addAction(stop_action)
            menu.addAction(check_action)
            menu.addAction(refresh_action)
            menu.addSeparator()
            menu.addAction(quit_action)
            tray.setContextMenu(menu)
            tray.activated.connect(self.on_tray_activated)
            tray.show()
            self._tray = tray

        def _register_launch(self) -> bool:
            state = _load_ui_state(self.settings)
            launches = int(state.get("launch_count") or 0)
            state["launch_count"] = launches + 1
            _save_ui_state(self.settings, state)
            return launches == 0

        @Slot()
        def refresh_snapshot(self) -> None:
            self.store = _store(self.settings)
            daemon = service_status(self.settings)
            try:
                background = background_service_status(self.settings)
                background_text = (
                    f"{background.backend} installed={yes_no(background.installed)} "
                    f"running={yes_no(background.running)}"
                )
            except Exception as exc:  # noqa: BLE001
                background_text = f"background status error: {exc}"
            self.runtime_label.setText(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            self.daemon_label.setText(
                f"Watcher daemon: {'running' if daemon.running else 'stopped'}"
                + (f" (PID {daemon.pid})" if daemon.pid else "")
            )
            self.background_label.setText(f"OS service: {background_text}")
            self._load_account_selector()
            self._load_watchers()
            self._load_session_scope()
            self._load_sessions()
            self._load_events()

        def _load_account_selector(self) -> None:
            current_data = self.account_combo.currentData()
            accounts = self.store.list_accounts()
            self.account_combo.blockSignals(True)
            self.account_combo.clear()
            for account in accounts:
                self.account_combo.addItem(account.name, userData=account.id)
            if accounts:
                index = 0
                for pos in range(self.account_combo.count()):
                    if self.account_combo.itemData(pos) == current_data:
                        index = pos
                        break
                self.account_combo.setCurrentIndex(index)
            self.account_combo.blockSignals(False)
            has_accounts = bool(accounts)
            delete_busy = any(
                label.startswith("account-remove:") for label in self._running_cli_labels
            )
            login_busy = any(
                label.startswith("account-login-") for label in self._running_cli_labels
            )
            self.account_login_button.setEnabled(has_accounts and not login_busy)
            self.account_delete_button.setEnabled(has_accounts and not delete_busy)

        def _load_watchers(self) -> None:
            rows = _watcher_rows(self.store)
            self._watcher_rows = rows
            self.watchers_table.setRowCount(len(rows))
            for row_idx, row in enumerate(rows):
                values = [
                    str(row.watcher_id),
                    row.account_name,
                    row.status,
                    yes_no(row.enabled),
                    row.sessions,
                    row.five_hour,
                    row.seven_day,
                    row.burn_five_hour,
                    row.reset_five_hour,
                    row.reset_seven_day,
                    row.last_check,
                    row.source,
                ]
                for col_idx, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                    self.watchers_table.setItem(row_idx, col_idx, item)
            self.watchers_table.resizeColumnsToContents()

        def _load_session_scope(self) -> None:
            current_data = self.session_scope_combo.currentData()
            accounts = self.store.list_accounts()
            self.session_scope_combo.blockSignals(True)
            self.session_scope_combo.clear()
            self.session_scope_combo.addItem(_ALL_ACCOUNTS_LABEL, userData=None)
            for account in accounts:
                self.session_scope_combo.addItem(account.name, userData=account.id)
            if len(accounts) == 1:
                self.session_scope_combo.setCurrentIndex(1)
            else:
                index = 0
                for pos in range(self.session_scope_combo.count()):
                    if self.session_scope_combo.itemData(pos) == current_data:
                        index = pos
                        break
                self.session_scope_combo.setCurrentIndex(index)
            self.session_scope_combo.blockSignals(False)

        def _load_sessions(self) -> None:
            rows = _session_rows(self.store)
            scoped_account_id = self.session_scope_combo.currentData()
            if scoped_account_id is not None:
                rows = [row for row in rows if row.account_id == int(scoped_account_id)]
            rows = [row for row in rows if row.selected]
            self.sessions_table.setRowCount(len(rows))
            for row_idx, row in enumerate(rows):
                values = [
                    str(row.session_id),
                    row.account_name,
                    row.title,
                    yes_no(row.selected),
                    row.kind,
                    row.status,
                    yes_no(row.controllable),
                    row.last_seen,
                    row.session_id_text,
                ]
                for col_idx, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                    self.sessions_table.setItem(row_idx, col_idx, item)
            hide_account_col = len(self.store.list_accounts()) <= 1
            self.sessions_table.setColumnHidden(1, hide_account_col)
            self.sessions_table.resizeColumnsToContents()

        def _load_events(self) -> None:
            preview_lines: list[str] = []
            full_lines: list[str] = []
            for idx, event in enumerate(self.store.list_account_events(limit=60)):
                session = f"/{event.session_id}" if event.session_id else ""
                line = (
                    f"{format_timestamp(event.created_at)}  "
                    f"#{event.account_watcher_id}{session}  "
                    f"{event.level.upper():<7}  {event.message}"
                )
                full_lines.append(line)
                if idx < 3:
                    preview_lines.append(line)
            preview_text = "\n".join(preview_lines) if preview_lines else "No recent events."
            full_text = "\n".join(full_lines) if full_lines else "No recent events."
            self.events_preview.setPlainText(preview_text)
            self.events_log.setPlainText(full_text)

        def _poll_control_commands(self) -> None:
            path = agent_control_path(self.settings)
            if not path.exists():
                return
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                path.unlink(missing_ok=True)
                return
            path.unlink(missing_ok=True)
            command = str(payload.get("command") or "").strip().lower()
            if command == "show":
                self.show_and_focus()
                return
            if command == "quit":
                self.exit_application()
                return

        @Slot()
        def _reload_sessions_only(self) -> None:
            self._load_sessions()

        def _select_account_context(self, account_id: int) -> None:
            for idx in range(self.account_combo.count()):
                if self.account_combo.itemData(idx) == account_id:
                    self.account_combo.setCurrentIndex(idx)
                    break
            for idx in range(self.session_scope_combo.count()):
                if self.session_scope_combo.itemData(idx) == account_id:
                    self.session_scope_combo.setCurrentIndex(idx)
                    break

        def action_account_add(self) -> None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Add Account")
            dialog.resize(560, 180)
            wrapper = QVBoxLayout(dialog)
            grid = QGridLayout()
            name_label = QLabel("Account name")
            name_input = QLineEdit(dialog)
            profile_label = QLabel("Profile dir (optional)")
            profile_input = QLineEdit(dialog)
            profile_input.setPlaceholderText(str(self.settings.profiles_dir / "account-name"))
            grid.addWidget(name_label, 0, 0)
            grid.addWidget(name_input, 0, 1)
            grid.addWidget(profile_label, 1, 0)
            grid.addWidget(profile_input, 1, 1)
            wrapper.addLayout(grid)
            actions = QHBoxLayout()
            actions.addStretch(1)
            cancel = QPushButton("Cancel")
            create = QPushButton("Create")
            actions.addWidget(cancel)
            actions.addWidget(create)
            wrapper.addLayout(actions)

            cancel.clicked.connect(dialog.reject)

            def _create_account() -> None:
                name = name_input.text().strip()
                if not name:
                    self._error("Account name is required.")
                    return
                profile_value = profile_input.text().strip()
                profile_dir = profile_value or str(self.settings.profiles_dir / _slug(name))
                try:
                    account = self.store.create_account(name=name, profile_dir=profile_dir)
                except Exception as exc:  # noqa: BLE001
                    self._error(f"Could not create account: {exc}")
                    return
                if account.id is not None:
                    self.store.ensure_account_watcher(account.id)
                    self._toast(f"Account created: {account.name}")
                    dialog.accept()
                    self.refresh_snapshot()
                    self._select_account_context(account.id)

            create.clicked.connect(_create_account)
            dialog.exec()

        def action_account_delete(self) -> None:
            account_id = self.account_combo.currentData()
            if account_id is None:
                self._error("No account selected.")
                return
            try:
                account = self.store.get_account(int(account_id))
            except Exception as exc:  # noqa: BLE001
                self._error(f"Could not load account: {exc}")
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("Delete Account")
            dialog.resize(620, 200)
            wrapper = QVBoxLayout(dialog)
            info = QLabel(
                f"Delete account '{account.name}'?\n"
                "All watcher state, sessions and usage history for this account will be removed."
            )
            info.setWordWrap(True)
            purge_checkbox = QCheckBox("Also delete profile directory from disk")
            wrapper.addWidget(info)
            wrapper.addWidget(purge_checkbox)
            actions = QHBoxLayout()
            actions.addStretch(1)
            cancel = QPushButton("Cancel")
            delete = QPushButton("Delete")
            actions.addWidget(cancel)
            actions.addWidget(delete)
            wrapper.addLayout(actions)

            cancel.clicked.connect(dialog.reject)

            def _delete_account() -> None:
                args = ["account", "remove", str(account.id)]
                if purge_checkbox.isChecked():
                    args.append("--purge-profile")
                self._run_cli_worker(f"account-remove:{account.id}", args)
                dialog.accept()

            delete.clicked.connect(_delete_account)
            dialog.exec()

        def action_account_login(self) -> None:
            account_id = self.account_combo.currentData()
            if account_id is None:
                self._error("No account selected.")
                return
            try:
                account = self.store.get_account(int(account_id))
            except Exception as exc:  # noqa: BLE001
                self._error(f"Could not load account: {exc}")
                return

            dialog = QDialog(self)
            dialog.setWindowTitle(f"Login Account - {account.name}")
            dialog.resize(640, 260)
            wrapper = QVBoxLayout(dialog)
            helper = QLabel(
                "Step 1: Enter email and click 'Send OTP'.\n"
                "Step 2: Enter OTP code and click 'Verify OTP'."
            )
            helper.setWordWrap(True)
            wrapper.addWidget(helper)

            grid = QGridLayout()
            email_label = QLabel("Email")
            email_input = QLineEdit(dialog)
            otp_label = QLabel("OTP code")
            otp_input = QLineEdit(dialog)
            otp_input.setMaxLength(6)
            otp_input.setPlaceholderText("6-digit code")
            grid.addWidget(email_label, 0, 0)
            grid.addWidget(email_input, 0, 1)
            grid.addWidget(otp_label, 1, 0)
            grid.addWidget(otp_input, 1, 1)
            wrapper.addLayout(grid)

            status_label = QLabel("Status: idle")
            status_label.setWordWrap(True)
            wrapper.addWidget(status_label)

            actions = QHBoxLayout()
            actions.addStretch(1)
            close_btn = QPushButton("Close")
            send_btn = QPushButton("Send OTP")
            verify_btn = QPushButton("Verify OTP")
            actions.addWidget(close_btn)
            actions.addWidget(send_btn)
            actions.addWidget(verify_btn)
            wrapper.addLayout(actions)
            close_btn.clicked.connect(dialog.reject)

            def _set_login_busy(busy: bool) -> None:
                send_btn.setEnabled(not busy)
                verify_btn.setEnabled(not busy)
                close_btn.setEnabled(not busy)

            def _send_otp() -> None:
                email = email_input.text().strip()
                if not email:
                    self._error("Email is required.")
                    return
                label = f"account-login-start:{account.id}"
                status_label.setText("Status: requesting OTP...")
                _set_login_busy(True)

                def _done(_label: str, code: int, output: str) -> None:
                    _set_login_busy(False)
                    if "Missing OTP code." in output:
                        status_label.setText(
                            "Status: OTP requested. Check your email and enter the code."
                        )
                        return
                    if code == 0 and "Login successful" in output:
                        status_label.setText("Status: logged in successfully.")
                        self.refresh_snapshot()
                        return
                    tail = output.splitlines()[-1] if output else "login start failed"
                    status_label.setText(f"Status: {tail}")

                self._run_cli_worker(
                    label,
                    ["account", "login", str(account.id), "--email", email, "--no-close-browser"],
                    on_done=_done,
                )

            def _verify_otp() -> None:
                email = email_input.text().strip()
                otp = otp_input.text().strip()
                if not email:
                    self._error("Email is required.")
                    return
                if len(otp) != 6 or not otp.isdigit():
                    self._error("OTP code must be 6 digits.")
                    return
                label = f"account-login-otp:{account.id}"
                status_label.setText("Status: verifying OTP...")
                _set_login_busy(True)

                def _done(_label: str, code: int, output: str) -> None:
                    _set_login_busy(False)
                    if code == 0 and "Login successful" in output:
                        status_label.setText("Status: logged in successfully.")
                        self.refresh_snapshot()
                        return
                    tail = output.splitlines()[-1] if output else "OTP verification failed"
                    status_label.setText(f"Status: {tail}")

                self._run_cli_worker(
                    label,
                    ["account", "login", str(account.id), "--email", email, "--otp", otp],
                    on_done=_done,
                )

            send_btn.clicked.connect(_send_otp)
            verify_btn.clicked.connect(_verify_otp)
            dialog.exec()

        def _resolve_manage_account_id(self) -> int | None:
            scoped = self.session_scope_combo.currentData()
            if scoped is not None:
                return int(scoped)
            accounts = self.store.list_accounts()
            if len(accounts) == 1 and accounts[0].id is not None:
                return int(accounts[0].id)
            return None

        def open_session_manager(self) -> None:
            account_id = self._resolve_manage_account_id()
            if account_id is None:
                self._error("Select an account in Session Scope first.")
                return
            account = next(
                (item for item in self.store.list_accounts() if item.id == account_id),
                None,
            )
            if account is None:
                self._error("Selected account no longer exists.")
                return
            sessions = self.store.list_sessions(account_id)
            sessions.sort(
                key=lambda item: (
                    _session_status_rank(item.status),
                    str(item.status or ""),
                    str(item.title or "").lower(),
                )
            )
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Manage Sessions - {account.name}")
            dialog.resize(980, 560)
            dialog_layout = QVBoxLayout(dialog)
            helper = QLabel(
                "Choose which sessions stay active. "
                "Only selected sessions are shown in the main view."
            )
            dialog_layout.addWidget(helper)
            table = QTableWidget(len(sessions), 7, dialog)
            table.setHorizontalHeaderLabels(
                ["Active", "Title", "Status", "Kind", "Control", "Last Seen", "Session Key"]
            )
            for row_idx, session in enumerate(sessions):
                active_item = QTableWidgetItem("")
                active_item.setFlags(
                    active_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                )
                active_item.setCheckState(Qt.Checked if session.watch_enabled else Qt.Unchecked)
                active_item.setData(Qt.ItemDataRole.UserRole, session.id)
                table.setItem(row_idx, 0, active_item)
                values = [
                    session.title,
                    session.status,
                    session.kind,
                    yes_no(session.control_supported),
                    format_timestamp(session.last_seen_at),
                    session.session_key,
                ]
                for col_idx, value in enumerate(values, start=1):
                    item = QTableWidgetItem(str(value or ""))
                    item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                    table.setItem(row_idx, col_idx, item)
            table.resizeColumnsToContents()
            dialog_layout.addWidget(table, 1)
            actions = QHBoxLayout()
            actions.addStretch(1)
            cancel = QPushButton("Cancel")
            save = QPushButton("Save")
            actions.addWidget(cancel)
            actions.addWidget(save)
            dialog_layout.addLayout(actions)

            cancel.clicked.connect(dialog.reject)

            def _save():
                for row_idx in range(table.rowCount()):
                    check_item = table.item(row_idx, 0)
                    if check_item is None:
                        continue
                    session_id = check_item.data(Qt.ItemDataRole.UserRole)
                    if session_id is None:
                        continue
                    enabled = check_item.checkState() == Qt.Checked
                    self.store.set_session_watch_enabled(int(session_id), enabled)
                dialog.accept()
                self.refresh_snapshot()

            save.clicked.connect(_save)
            dialog.exec()

        @Slot(int, int)
        def _on_watcher_row_clicked(self, row: int, _column: int) -> None:
            if row < 0 or row >= len(self._watcher_rows):
                return
            account_id = self._watcher_rows[row].account_id
            self._select_account_context(account_id)

        def toggle_log_expansion(self) -> None:
            self._log_expanded = not self._log_expanded
            self.events_log.setVisible(self._log_expanded)
            self.log_toggle_button.setText(
                "Hide Full Log" if self._log_expanded else "Show Full Log"
            )

        def action_start_watcher(self) -> None:
            self._run_safe_action(lambda: start_service(self.settings), "Watcher started.")

        def action_stop_watcher(self) -> None:
            self._run_safe_action(lambda: stop_service(self.settings), "Watcher stopped.")

        def action_restart_watcher(self) -> None:
            def _restart():
                stop_service(self.settings)
                return start_service(self.settings)

            self._run_safe_action(_restart, "Watcher restarted.")

        def action_check_now(self) -> None:
            self._run_cli_worker("check", ["watcher", "check", "--all"])

        def action_service_install(self) -> None:
            self._run_safe_action(
                lambda: install_background_service(self.settings), "OS service installed."
            )

        def action_service_uninstall(self) -> None:
            self._run_safe_action(
                lambda: uninstall_background_service(self.settings), "OS service uninstalled."
            )

        def action_service_start(self) -> None:
            self._run_safe_action(
                lambda: start_background_service(self.settings),
                "OS service started.",
            )

        def action_service_stop(self) -> None:
            self._run_safe_action(
                lambda: stop_background_service(self.settings),
                "OS service stopped.",
            )

        def action_service_restart(self) -> None:
            self._run_safe_action(
                lambda: restart_background_service(self.settings), "OS service restarted."
            )

        def apply_quick_settings(self) -> None:
            try:
                _update_env_key(
                    "CSW_BROWSER_KEEPALIVE",
                    bool_to_env(self.keepalive_checkbox.isChecked()),
                )
                _update_env_key(
                    "CSW_AUTO_SWITCH_TO_PRO_PLAN",
                    bool_to_env(self.auto_pro_checkbox.isChecked()),
                )
                interval = self.refresh_combo.currentText().strip().rstrip("s")
                self.refresh_timer.setInterval(max(2, int(interval)) * 1000)
                self.settings = Settings()
                self._toast("Quick settings applied.")
                self.refresh_snapshot()
            except Exception as exc:  # noqa: BLE001
                self._error(f"Could not apply quick settings: {exc}")

        def _run_safe_action(self, action, success_message: str) -> None:
            try:
                action()
                self._toast(success_message)
            except BackgroundServiceError as exc:
                self._error(str(exc))
            except Exception as exc:  # noqa: BLE001
                self._error(str(exc))
            self.refresh_snapshot()

        def _run_cli_worker(
            self,
            label: str,
            args: list[str],
            *,
            on_done: Callable[[str, int, str], None] | None = None,
        ) -> None:
            if label in self._running_cli_labels:
                self._toast(f"{label}: already running")
                return
            self._running_cli_labels.add(label)
            self._set_action_busy(label, True)
            if on_done is not None:
                self._worker_callbacks[label] = on_done
            command = cli_command(args)
            thread = QThread(self)
            worker = _SubprocessWorker(label, command)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(self._on_cli_worker_done)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(lambda: self._drop_thread(thread))
            self.threads.append(thread)
            thread.start()

        @Slot(str, int, str)
        def _on_cli_worker_done(self, label: str, code: int, output: str) -> None:
            self._running_cli_labels.discard(label)
            self._set_action_busy(label, False)
            callback = self._worker_callbacks.pop(label, None)
            visible_label = label.split(":", 1)[0]
            if label.startswith("account-login-start:") and "Missing OTP code." in output:
                self._toast("OTP requested. Enter code and click Verify OTP.")
            elif code == 0:
                self._toast(f"{visible_label}: ok")
            else:
                self._error(f"{visible_label}: failed (exit {code})")
            if output:
                self.events_log.appendPlainText("")
                self.events_log.appendPlainText(f"[{visible_label}] {output}")
                parts = output.splitlines()
                tail = parts[-1] if parts else output
                self.events_preview.appendPlainText(f"[{visible_label}] {tail[:120]}")
            if callback is not None:
                try:
                    callback(label, code, output)
                except Exception as exc:  # noqa: BLE001
                    self._error(f"{visible_label}: callback failed ({exc})")
            self.refresh_snapshot()

        def _set_action_busy(self, label: str, busy: bool) -> None:
            if label == "check":
                self.btn_check.setEnabled(not busy)
                self.btn_check.setText("Check running..." if busy else "Check Now")
            if label.startswith("account-remove:"):
                self.account_delete_button.setEnabled(not busy)

        def _drop_thread(self, thread: QThread) -> None:
            self.threads = [item for item in self.threads if item is not thread]

        def _toast(self, message: str) -> None:
            if self._tray:
                self._tray.showMessage(
                    "Claude Session Watcher",
                    message,
                    QSystemTrayIcon.Information,
                    3000,
                )

        def _error(self, message: str) -> None:
            if self._tray:
                self._tray.showMessage(
                    "Claude Session Watcher",
                    message,
                    QSystemTrayIcon.Warning,
                    5000,
                )
            self.events_log.appendPlainText("")
            self.events_log.appendPlainText(f"[ERROR] {message}")
            self.events_preview.appendPlainText(f"[ERROR] {message[:120]}")

        def on_tray_activated(self, reason) -> None:
            if reason == QSystemTrayIcon.Trigger:
                if self.isVisible():
                    self.hide()
                else:
                    self.show_and_focus()

        def show_and_focus(self) -> None:
            self.show()
            self.raise_()
            self.activateWindow()
            self.refresh_snapshot()

        def exit_application(self) -> None:
            if self._installed_mode:
                try:
                    stop_service(self.settings)
                except Exception:  # noqa: BLE001
                    pass
            if self._tray:
                self._tray.hide()
            self.app.quit()

        def closeEvent(self, event) -> None:  # noqa: N802
            if self._tray and self._tray.isVisible():
                self.hide()
                event.ignore()
                self._toast("Application is still running in tray.")
                return
            super().closeEvent(event)

    qt_app = QApplication.instance() or QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    lock = _acquire_instance_lock(settings)
    if lock is None:
        send_agent_command(settings, "show")
        return 0
    window = NativeMainWindow(qt_app, settings)
    write_agent_pid(settings, os.getpid())
    qt_app.aboutToQuit.connect(lambda: _release_instance_lock(settings, lock))
    qt_app.aboutToQuit.connect(lambda: clear_agent_pid(settings))
    if window.should_start_visible():
        window.show()
    else:
        window.hide()
    return qt_app.exec()


def _store(settings: Settings) -> Store:
    settings.ensure_dirs()
    return Store(settings.db_path)


def _watcher_rows(store: Store) -> list[UiWatcherRow]:
    rows: list[UiWatcherRow] = []
    for watcher in store.list_account_watchers():
        account = store.get_account(watcher.account_id)
        sessions = store.list_sessions(watcher.account_id)
        samples = store.list_usage_samples(watcher.id)
        ui = build_ui_watcher(watcher)
        insights = build_usage_insights(watcher, samples)
        rows.append(
            UiWatcherRow(
                watcher_id=watcher.id or -1,
                account_id=watcher.account_id,
                account_name=account.name,
                status=insights.status,
                enabled=watcher.enabled,
                sessions=f"{sum(1 for item in sessions if item.watch_enabled)}/{len(sessions)}",
                five_hour=pct(ui.five_hour.utilization),
                seven_day=pct(ui.seven_day.utilization),
                burn_five_hour=burn(insights.five_hour_burn_per_hour),
                reset_five_hour=ui.five_hour.reset_display or "",
                reset_seven_day=ui.seven_day.reset_display or "",
                last_check=ui.last_checked_display or "",
                source=ui.usage_source or "",
            )
        )
    return rows


def _session_rows(store: Store) -> list[UiSessionRow]:
    rows: list[UiSessionRow] = []
    account_names = {account.id: account.name for account in store.list_accounts()}
    for session in store.list_sessions():
        rows.append(
            UiSessionRow(
                session_id=session.id or -1,
                account_id=session.account_id,
                account_name=account_names.get(session.account_id, str(session.account_id)),
                title=session.title,
                selected=session.watch_enabled,
                kind=session.kind,
                status=session.status,
                controllable=session.control_supported,
                last_seen=format_timestamp(session.last_seen_at),
                session_id_text=session.session_key,
            )
        )
    return rows


def _session_status_rank(status: str | None) -> int:
    normalized = str(status or "").strip().lower()
    order = {
        "active": 0,
        "online": 1,
        "idle": 2,
        "unknown": 3,
        "offline": 4,
        "archived": 5,
    }
    return order.get(normalized, 6)


def pct(value: object) -> str:
    if value is None:
        return ""
    return f"{float(value):.1f}%"


def burn(value: object) -> str:
    if value is None:
        return ""
    return f"{float(value):.1f}%/h"


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def bool_to_env(value: bool) -> str:
    return "true" if value else "false"


def _slug(value: str) -> str:
    lowered = value.strip().lower()
    slug = "".join(ch if ch.isalnum() else "-" for ch in lowered)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "account"


def cli_command(args: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "claude_session_watcher.cli", *args]


def _ui_state_path(settings: Settings) -> Path:
    settings.ensure_dirs()
    return settings.data_dir / _UI_STATE_FILENAME


def _load_ui_state(settings: Settings) -> dict[str, object]:
    path = _ui_state_path(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_ui_state(settings: Settings, payload: dict[str, object]) -> None:
    path = _ui_state_path(settings)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _update_env_key(env_key: str, value: str) -> None:
    env_path = Path.cwd() / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    for idx, line in enumerate(lines):
        if line.strip().startswith(f"{env_key}="):
            lines[idx] = f"{env_key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{env_key}={value}")
    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _load_app_icon(icon_cls):
    base = Path(__file__).resolve().parent / "assets"
    for name in ("csw_icon.ico", "csw_icon.png"):
        candidate = base / name
        if candidate.exists():
            return icon_cls(str(candidate))
    return icon_cls()


def _acquire_instance_lock(settings: Settings):
    path = agent_lock_path(settings)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        return path
    except FileExistsError:
        try:
            existing_pid = int(path.read_text(encoding="utf-8").strip())
        except Exception:  # noqa: BLE001
            existing_pid = None
        if process_running(existing_pid):
            return None
        path.unlink(missing_ok=True)
        fd = os.open(path, flags)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        return path


def _release_instance_lock(settings: Settings, lock_path: Path | None) -> None:
    if lock_path is None:
        return
    lock_path.unlink(missing_ok=True)

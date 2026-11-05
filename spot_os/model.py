"""Pure startup policy. The signed launcher remains the sole app-lock owner."""
from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    BOOTING = 'booting'
    SESSION = 'session_starting'
    STARTING = 'app_starting'
    WAITING = 'waiting_for_app_ready'
    RUNNING = 'running'
    RESTARTING = 'app_restarting'
    UPDATING = 'updating'
    ROLLBACK = 'rolling_back'
    RECOVERY = 'recovery'
    FAILED = 'failed'


@dataclass
class Evidence:
    authenticated: bool = False
    release: bool = False
    wayland: bool = False
    mapped: bool = False
    painted: bool = False
    responsive: bool = False
    enabled: bool = False
    initialized: bool = False
    size: tuple = (0, 0)
    camera: bool = False
    position: tuple = (0, 0)
    screen: tuple = (0, 0)
    fullscreen: bool = False

    @property
    def gui(self):
        return (all((self.authenticated, self.release, self.wayland, self.mapped,
                     self.painted, self.responsive, self.enabled, self.initialized))
                and tuple(self.size) == (800, 480) and tuple(self.position) == (0, 0)
                and tuple(self.screen) == (800, 480) and self.fullscreen)


class Policy:
    def __init__(self, camera_grace=12, startup_timeout=100, max_attempts=3):
        self.state = State.BOOTING
        self.camera_grace = camera_grace
        self.startup_timeout = startup_timeout
        self.max_attempts = max_attempts
        self.attempts = 0
        self.started = 0
        self.gui_at = None
        self.next_attempt = 0
        self.camera_available = False
        self.reason = ''

    def start(self, now):
        if self.attempts >= self.max_attempts:
            self.state = State.FAILED
            return False
        self.attempts += 1
        self.started = now
        self.gui_at = None
        self.state = State.WAITING
        return True

    def observe(self, now, evidence, update_locked=False):
        self.camera_available = evidence.camera
        if evidence.gui:
            if self.gui_at is None:
                self.gui_at = now
            if not update_locked and (evidence.camera or now - self.gui_at >= self.camera_grace):
                self.state = State.RUNNING
                self.reason = '' if evidence.camera else 'camera_unavailable'
                return 'reveal'
        else:
            self.gui_at = None
        if self.state != State.RUNNING and now - self.started >= self.startup_timeout:
            self.state = State.RECOVERY
            self.reason = 'startup_timeout'
        return 'cover'

    def exited(self, now, update_locked):
        self.gui_at = None
        if update_locked:
            self.state = State.UPDATING
            return 'defer'
        if self.attempts >= self.max_attempts:
            self.state = State.FAILED
            self.reason = 'retry_limit'
            return 'recovery'
        self.state = State.RESTARTING
        self.next_attempt = now + min(30, 2 ** self.attempts)
        return 'retry'

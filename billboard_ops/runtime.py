"""The running process's settings, set once at start-up by the web app or the worker."""

config = None


def init(cfg):
    global config
    config = cfg

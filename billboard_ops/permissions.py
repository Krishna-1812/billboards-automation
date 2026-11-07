"""Who may do what. One table, so it can be read (and tested) in one place."""

TEAM = ("owner", "manager", "sales", "ops")

CAN = {
    "view":             ("owner", "manager", "sales", "ops"),
    "hold":             ("owner", "manager", "sales"),          # place, edit or extend a hold
    "cancel_hold":      ("owner", "manager", "sales"),
    "confirm":          ("owner", "manager"),
    "edit_confirmed":   ("owner", "manager"),
    "cancel_confirmed": ("owner", "manager"),
    "override":         ("owner", "manager"),                   # book past a screen's capacity, with a reason
    "edit_screens":     ("owner", "manager", "ops"),
    "edit_clients":     ("owner", "manager", "sales"),
    "import":           ("owner", "manager"),
    "review_sheet":     ("owner", "manager"),
    "settings":         ("owner",),
    "people":           ("owner",),
}


def can(user, capability):
    role = getattr(user, "role", None) if user is not None else None
    if role is None or not getattr(user, "active", True):
        return False
    return role in CAN.get(capability, ())


class Forbidden(Exception):
    pass


def require(user, capability):
    if not can(user, capability):
        raise Forbidden(capability)

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class Permission(StrEnum):
    CHAT_USE = "chat:use"
    DOCUMENT_READ = "document:read"
    DOCUMENT_WRITE = "document:write"
    DOCUMENT_DELETE = "document:delete"
    MEMBER_MANAGE = "member:manage"
    APIKEY_MANAGE = "apikey:manage"
    AUDIT_READ = "audit:read"


_VIEWER = frozenset({Permission.CHAT_USE, Permission.DOCUMENT_READ})
_EDITOR = _VIEWER | {Permission.DOCUMENT_WRITE, Permission.DOCUMENT_DELETE}
_ADMIN = _EDITOR | {Permission.MEMBER_MANAGE, Permission.APIKEY_MANAGE, Permission.AUDIT_READ}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.EDITOR: frozenset(_EDITOR),
    Role.ADMIN: frozenset(_ADMIN),
}

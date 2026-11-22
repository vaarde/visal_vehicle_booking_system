"""
Typed application errors. Services raise these; one handler in create_app turns them into
`{"error": "<message>"}` JSON, which is the shape the frontend already shows in its toasts.
Messages must be accurate and readable by end users.
"""


class AppError(Exception):
    status_code = 400

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class BadRequestError(AppError):
    status_code = 400


class UnauthorizedError(AppError):
    status_code = 401


class ForbiddenError(AppError):
    status_code = 403


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class TooManyRequestsError(AppError):
    status_code = 429

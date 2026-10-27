from asyncpg.exceptions import (
    CheckViolationError,
    ForeignKeyViolationError,
    UniqueViolationError,
)
from grpc import StatusCode

from src.domain.equipment.errors import (
    EquipmentDomainError,
    EquipmentUnavailableError,
    InvalidDataError,
    NotFoundError,
    RentalStateError,
    TelematicsNotConfiguredError,
    TelematicsRejectedError,
    TelematicsUnavailableError,
    VersionConflictError,
)


class ExceptionStatusMapper:
    def status(self, error: Exception) -> StatusCode:
        for exception_type, status in self._mappings():
            if isinstance(error, exception_type):
                return status
        return StatusCode.INTERNAL

    @staticmethod
    def _mappings() -> tuple[tuple[type[Exception], StatusCode], ...]:
        return (
            (NotFoundError, StatusCode.NOT_FOUND),
            (EquipmentUnavailableError, StatusCode.FAILED_PRECONDITION),
            (RentalStateError, StatusCode.FAILED_PRECONDITION),
            (VersionConflictError, StatusCode.ABORTED),
            (TelematicsNotConfiguredError, StatusCode.FAILED_PRECONDITION),
            (TelematicsRejectedError, StatusCode.INVALID_ARGUMENT),
            (TelematicsUnavailableError, StatusCode.UNAVAILABLE),
            (InvalidDataError, StatusCode.INVALID_ARGUMENT),
            (EquipmentDomainError, StatusCode.INVALID_ARGUMENT),
            (UniqueViolationError, StatusCode.ALREADY_EXISTS),
            (ForeignKeyViolationError, StatusCode.FAILED_PRECONDITION),
            (CheckViolationError, StatusCode.INVALID_ARGUMENT),
        )

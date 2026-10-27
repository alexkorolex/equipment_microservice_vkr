class EquipmentDomainError(ValueError):
    """Base error of invalid equipment, rental or cost data."""


class InvalidDataError(EquipmentDomainError):
    """The request violates a field rule (mapped to INVALID_ARGUMENT)."""


class NotFoundError(EquipmentDomainError):
    """The resource does not exist in the caller's organization."""


class EquipmentUnavailableError(EquipmentDomainError):
    """The equipment cannot be rented for the period (out of service or booked)."""


class RentalStateError(EquipmentDomainError):
    """The rental is in a state that does not allow the action."""


class VersionConflictError(EquipmentDomainError):
    """``expectedVersion`` does not match the stored version."""


class TelematicsNotConfiguredError(EquipmentDomainError):
    """The organization has no telematics account, or the service has no encryption key."""


class TelematicsRejectedError(EquipmentDomainError):
    """The telematics provider rejected the credentials or the request."""


class TelematicsUnavailableError(EquipmentDomainError):
    """The telematics provider cannot be reached now; the call may be repeated."""

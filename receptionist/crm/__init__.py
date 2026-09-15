"""CRM integrations used by receptionist function tools."""

from receptionist.crm.espocrm import (
    CRMAppointment,
    CRMContact,
    CRMIdentityError,
    CRMKnowledgeResult,
    CRMNotFoundError,
    CRMRequestError,
    CRMSlotUnavailableError,
    EspoCRMClient,
)

__all__ = [
    "CRMAppointment",
    "CRMContact",
    "CRMIdentityError",
    "CRMKnowledgeResult",
    "CRMNotFoundError",
    "CRMRequestError",
    "CRMSlotUnavailableError",
    "EspoCRMClient",
]

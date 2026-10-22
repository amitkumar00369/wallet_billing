from rest_framework import status
from rest_framework.exceptions import APIException


class BusinessRuleViolation(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "business_rule_violation"


class InvalidTransition(BusinessRuleViolation):
    default_code = "invalid_transition"


class InsufficientBalance(BusinessRuleViolation):
    default_code = "insufficient_balance"


class ActiveSessionExists(BusinessRuleViolation):
    default_code = "active_session_exists"

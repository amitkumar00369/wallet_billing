from rest_framework import status
from rest_framework.exceptions import APIException


class RechargeConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "recharge_conflict"

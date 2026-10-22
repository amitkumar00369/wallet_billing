from django.http import JsonResponse
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

PRIVATE_SEGMENT = "private"


def _unauthorized(message):
    response = JsonResponse({"detail": message}, status=401)
    response["WWW-Authenticate"] = "Bearer"
    return response


class PrivateRouteJWTMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response
        self.jwt = JWTAuthentication()

    def __call__(self, request):
        if PRIVATE_SEGMENT in request.path.strip("/").split("/"):
            try:
                authenticated = self.jwt.authenticate(request)
            except AuthenticationFailed:
                return _unauthorized("Token is invalid or expired.")
            if authenticated is None:
                return _unauthorized("Authentication credentials were not provided.")
        return self.get_response(request)

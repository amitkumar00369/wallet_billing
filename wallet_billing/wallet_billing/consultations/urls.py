from django.urls import path

from .views import SessionViewSet

# Private means all authenticated routes pk-primary key
urlpatterns = [
    path("private/create/", SessionViewSet.as_view({"post": "create"}), name="session-create"),
    path("private/<int:pk>/details/", SessionViewSet.as_view({"get": "retrieve"}), name="session-details"),
    path("private/<int:pk>/assign/", SessionViewSet.as_view({"post": "assign"}), name="session-assign"),
    path("private/<int:pk>/accept/", SessionViewSet.as_view({"post": "accept"}), name="session-accept"),
    path("private/<int:pk>/start/", SessionViewSet.as_view({"post": "start"}), name="session-start"),
    path("private/<int:pk>/end/", SessionViewSet.as_view({"post": "end"}), name="session-end"),
    path("private/<int:pk>/bill/", SessionViewSet.as_view({"post": "bill"}), name="session-bill"),
]

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ConfigView, HealthView, RunViewSet, SamplesView

router = DefaultRouter(trailing_slash=True)
router.register("runs", RunViewSet, basename="run")

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("config/", ConfigView.as_view(), name="config"),
    path("samples/", SamplesView.as_view(), name="samples"),
    path("", include(router.urls)),
]

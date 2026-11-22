"""Stand-in documented items, so History can be tested before the real ones exist."""

from django.db import models

from netatlas.history.models import TrackedModel


class Widget(TrackedModel):
    history_kind = "widget"
    history_exclude = ("internal_note",)

    name = models.CharField(max_length=50, unique=True)
    notes = models.TextField(blank=True)
    size = models.IntegerField(default=1)
    internal_note = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return self.name


class Part(TrackedModel):
    history_kind = "part"

    widget = models.ForeignKey(Widget, on_delete=models.CASCADE, related_name="parts")
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name


class Label(TrackedModel):
    """Survives its Widget's deletion, as an IP Address survives its Device's (ADR 0005)."""

    history_kind = "label"

    widget = models.ForeignKey(
        Widget, null=True, blank=True, on_delete=models.SET_NULL, related_name="labels"
    )
    spare_widget = models.ForeignKey(
        Widget, null=True, blank=True, on_delete=models.SET_NULL, related_name="spare_labels"
    )
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name

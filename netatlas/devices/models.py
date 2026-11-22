from django.db import models
from django.utils.translation import gettext_lazy as _

from netatlas.history.models import TrackedModel


class DeviceType(TrackedModel):
    """A reusable make and model. Devices copy from it, so edits never reach existing Devices."""

    history_kind = "device-type"

    make = models.CharField(max_length=100)
    # Not `model`: create_item takes the model class under that name.
    model_name = models.CharField(max_length=100)
    rack_height = models.PositiveSmallIntegerField(default=1)
    interfaces = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["make", "model_name"],
                name="unique_device_type_make_model",
                violation_error_message=_("A Device Type with this make and model already exists."),
            )
        ]

    def __str__(self):
        return f"{self.make} {self.model_name}"

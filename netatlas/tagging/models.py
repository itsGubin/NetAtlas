from django.db import models

from netatlas.history.models import TrackedModel


class Tag(TrackedModel):
    """A free label such as "prod". Spelling variants like `Prod` and `prod` are one Tag."""

    history_kind = "tag"
    history_exclude = ("key",)

    name = models.CharField(max_length=50)
    # The case-folded name, which is what makes a Tag unique.
    key = models.CharField(max_length=150, unique=True)

    def __str__(self):
        return self.name


class TaggableModel(TrackedModel):
    """A documented item that can carry Tags. Changes to them are recorded in its History."""

    tags = models.ManyToManyField(Tag, blank=True, related_name="%(app_label)s_%(class)s_items")

    class Meta:
        abstract = True

from django.db import migrations

DEFAULT_SKELETON = "{{section:Notes}}"
DEFAULTS = {
    "customer": "Customer default",
    "site": "Site default",
    "rack": "Rack default",
    "device": "Device default",
    "vlan": "VLAN default",
    "subnet": "Subnet default",
}


def create_defaults(apps, schema_editor):
    """Give each kind its default, and keep every Page's text in that default's one section."""
    Page = apps.get_model("pages", "Page")
    PageTemplate = apps.get_model("pages", "PageTemplate")
    Change = apps.get_model("history", "Change")
    FieldChange = apps.get_model("history", "FieldChange")

    for kind, name in DEFAULTS.items():
        template = PageTemplate.objects.create(name=name, item_kind=kind, body=DEFAULT_SKELETON)
        change = Change.objects.create(
            item_kind="page-template",
            item_id=template.pk,
            item_label=name,
            action="created",
            actor=None,
            actor_name="system",
        )
        FieldChange.objects.bulk_create(
            FieldChange(change=change, field=field, old=None, new=new)
            for field, new in (
                ("name", name),
                ("item_kind", kind),
                ("device_type", None),
                ("body", DEFAULT_SKELETON),
            )
        )

    # The visible text does not change, so this move is not a History entry.
    pages = Page.objects.exclude(item_id=None).exclude(body="")
    for page in pages.iterator():
        page.sections = {"Notes": page.body}
        page.save(update_fields=["sections"])


def remove_defaults(apps, schema_editor):
    apps.get_model("history", "Change").objects.filter(item_kind="page-template").delete()
    apps.get_model("pages", "PageTemplate").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("pages", "0003_page_templates")]

    operations = [migrations.RunPython(create_defaults, remove_defaults)]

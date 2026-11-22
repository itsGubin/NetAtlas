from django.db import migrations

ITEM_KINDS = {
    "customer": ("places", "Customer"),
    "site": ("places", "Site"),
    "rack": ("devices", "Rack"),
    "device": ("devices", "Device"),
    "vlan": ("addressing", "Vlan"),
    "subnet": ("addressing", "Subnet"),
}


def create_item_pages(apps, schema_editor):
    """Give every existing item its Item Page, recorded in History as made by NetAtlas."""
    Page = apps.get_model("pages", "Page")
    Change = apps.get_model("history", "Change")
    FieldChange = apps.get_model("history", "FieldChange")

    for kind, (app_label, model_name) in ITEM_KINDS.items():
        model = apps.get_model(app_label, model_name)
        for item_id in model.objects.values_list("pk", flat=True).iterator():
            page = Page.objects.create(item_kind=kind, item_id=item_id)
            # Model methods don't exist here, so the name comes from the item's own History.
            latest = (
                Change.objects.filter(item_kind=kind, item_id=item_id)
                .order_by("-at", "-pk")
                .first()
            )
            change = Change.objects.create(
                item_kind="page",
                item_id=page.pk,
                item_label=latest.item_label if latest else f"{kind} {item_id}",
                action="created",
                actor=None,
                actor_name="system",
            )
            FieldChange.objects.bulk_create(
                FieldChange(change=change, field=field, old=None, new=new)
                for field, new in (
                    ("title", ""),
                    ("body", ""),
                    ("item_kind", kind),
                    ("item_id", item_id),
                )
            )


def remove_pages(apps, schema_editor):
    apps.get_model("history", "Change").objects.filter(item_kind="page").delete()
    apps.get_model("pages", "Page").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0001_initial"),
        ("history", "0001_initial"),
        ("places", "0002_customer_tags_site_tags"),
        ("devices", "0007_cable_tags_device_tags_devicetype_tags_and_more"),
        ("addressing", "0004_dhcprange_tags_ipaddress_tags_subnet_tags_vlan_tags"),
    ]

    operations = [migrations.RunPython(create_item_pages, remove_pages)]

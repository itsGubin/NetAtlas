import pytest
from django.apps import apps
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_user, ensure_initial_admin
from netatlas.history.models import TrackedModel, UntrackedWrite
from netatlas.history.services import create_item, delete_item, item_history, update_item
from tests.history_items.models import Label, Part, Widget

# Users, Roles, tokens and History itself are not documented items.
UNTRACKED_APPS = {"accounts", "history"}
# Rebuilt from a tracked Page body on every save, so the body's History already covers it.
DERIVED_MODELS = {"pages.Mention"}


@pytest.fixture
def admin(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


def changes_of(entry):
    return {change.field: (change.old, change.new) for change in entry.changes}


def test_creating_an_item_records_who_what_and_when(admin):
    before = timezone.now()

    widget = create_item(admin, Widget, name="alpha", size=3)

    [entry] = item_history(admin, "widget", widget.pk)
    assert entry.action == "created"
    assert entry.actor == "root"
    assert entry.item_label == "alpha"
    assert before <= entry.at <= timezone.now()
    assert changes_of(entry) == {"name": (None, "alpha"), "notes": (None, ""), "size": (None, 3)}


def test_updating_records_only_what_changed(admin):
    widget = create_item(admin, Widget, name="alpha", size=3)

    update_item(admin, widget, name="beta", size=3)

    latest = item_history(admin, "widget", widget.pk)[0]
    assert latest.action == "updated"
    assert latest.item_label == "beta"
    assert changes_of(latest) == {"name": ("alpha", "beta")}
    assert Widget.objects.get(pk=widget.pk).name == "beta"


def test_an_update_that_changes_nothing_records_nothing(admin):
    widget = create_item(admin, Widget, name="alpha", size=3)

    update_item(admin, widget, name="alpha", size="3")

    assert len(item_history(admin, "widget", widget.pk)) == 1


def test_old_value_comes_from_the_database_not_a_stale_object(admin):
    widget = create_item(admin, Widget, name="alpha")
    stale = Widget.objects.get(pk=widget.pk)
    update_item(admin, widget, name="beta")

    update_item(admin, stale, name="gamma")

    assert changes_of(item_history(admin, "widget", widget.pk)[0]) == {"name": ("beta", "gamma")}


def test_excluded_fields_are_saved_but_never_recorded(admin):
    widget = create_item(admin, Widget, name="alpha", internal_note="first")

    update_item(admin, widget, internal_note="second")

    assert Widget.objects.get(pk=widget.pk).internal_note == "second"
    entries = item_history(admin, "widget", widget.pk)
    assert len(entries) == 1
    assert "internal_note" not in changes_of(entries[0])


def test_an_invalid_change_saves_and_records_nothing(admin):
    widget = create_item(admin, Widget, name="alpha")

    with pytest.raises(ValidationError):
        update_item(admin, widget, name="")

    assert Widget.objects.get(pk=widget.pk).name == "alpha"
    assert len(item_history(admin, "widget", widget.pk)) == 1


def test_updating_an_unknown_field_is_a_programming_error(admin):
    widget = create_item(admin, Widget, name="alpha")

    with pytest.raises(TypeError):
        update_item(admin, widget, colour="red")


def test_history_outlives_a_deleted_item(admin):
    widget = create_item(admin, Widget, name="alpha", size=3)
    widget_id = widget.pk

    delete_item(admin, widget)

    assert not Widget.objects.filter(pk=widget_id).exists()
    [deleted, created] = item_history(admin, "widget", widget_id)
    assert deleted.action == "deleted"
    assert deleted.item_label == "alpha"
    assert changes_of(deleted)["size"] == (3, None)
    assert created.action == "created"


def test_deleting_records_every_item_removed_with_it(admin):
    widget = create_item(admin, Widget, name="alpha")
    part = create_item(admin, Part, widget=widget, name="bolt")
    part_id = part.pk

    delete_item(admin, widget)

    latest = item_history(admin, "part", part_id)[0]
    assert latest.action == "deleted"
    assert latest.actor == "root"
    assert latest.item_label == "bolt"


def test_deleting_records_items_it_changes(admin):
    widget = create_item(admin, Widget, name="alpha")
    widget_id = widget.pk
    label = create_item(admin, Label, widget=widget, name="keep me")

    delete_item(admin, widget)

    latest = item_history(admin, "label", label.pk)[0]
    assert latest.action == "updated"
    assert changes_of(latest) == {"widget": (widget_id, None)}


def test_an_item_pointing_twice_at_a_deleted_item_gets_one_entry(admin):
    widget = create_item(admin, Widget, name="alpha")
    widget_id = widget.pk
    label = create_item(admin, Label, widget=widget, spare_widget=widget, name="keep me")

    delete_item(admin, widget)

    [updated, created] = item_history(admin, "label", label.pk)
    assert created.action == "created"
    assert changes_of(updated) == {
        "widget": (widget_id, None),
        "spare_widget": (widget_id, None),
    }


def test_every_tracked_model_has_its_own_history_kind():
    kinds = [
        model.history_kind for model in apps.get_models() if issubclass(model, TrackedModel)
    ]

    assert kinds
    assert all(kinds)
    assert len(kinds) == len(set(kinds))


def test_a_failed_transaction_keeps_neither_the_change_nor_its_history(admin):
    widget = create_item(admin, Widget, name="alpha")

    with pytest.raises(RuntimeError), transaction.atomic():
        update_item(admin, widget, name="beta")
        raise RuntimeError

    assert Widget.objects.get(pk=widget.pk).name == "alpha"
    assert len(item_history(admin, "widget", widget.pk)) == 1


@pytest.mark.parametrize(
    "bypass",
    [
        pytest.param(lambda w: Widget(name="new").save(), id="save-new"),
        pytest.param(lambda w: w.save(), id="save-existing"),
        pytest.param(lambda w: w.delete(), id="delete-instance"),
        pytest.param(lambda w: Widget.objects.create(name="new"), id="manager-create"),
        pytest.param(lambda w: Widget.objects.get_or_create(name="new"), id="get-or-create"),
        pytest.param(
            lambda w: Widget.objects.update_or_create(pk=w.pk, defaults={"size": 9}),
            id="update-or-create",
        ),
        pytest.param(lambda w: Widget.objects.filter(pk=w.pk).update(size=9), id="queryset-update"),
        pytest.param(lambda w: Widget.objects.filter(pk=w.pk).delete(), id="queryset-delete"),
        pytest.param(lambda w: Widget.objects.bulk_create([Widget(name="new")]), id="bulk-create"),
        pytest.param(lambda w: Widget.objects.bulk_update([w], ["size"]), id="bulk-update"),
        pytest.param(lambda w: w.parts.all().delete(), id="related-delete"),
    ],
)
def test_writing_past_the_history_is_refused(admin, bypass):
    widget = create_item(admin, Widget, name="alpha", size=1)

    with pytest.raises(UntrackedWrite):
        bypass(widget)

    assert Widget.objects.get(pk=widget.pk).size == 1
    assert Widget.objects.count() == 1


@pytest.mark.parametrize("role", [Role.ADMIN, Role.EDITOR])
def test_admins_and_editors_can_write_items(admin, role):
    actor = admin if role == Role.ADMIN else create_user(admin, "e", "another-long-password", role)

    widget = create_item(actor, Widget, name="alpha")
    update_item(actor, widget, name="beta")
    delete_item(actor, widget)

    assert not Widget.objects.exists()


def test_a_viewer_cannot_write_items(admin):
    viewer = create_user(admin, "viewer", "another-long-password", Role.VIEWER)
    widget = create_item(admin, Widget, name="alpha")

    for write in (
        lambda: create_item(viewer, Widget, name="beta"),
        lambda: update_item(viewer, widget, name="beta"),
        lambda: delete_item(viewer, widget),
    ):
        with pytest.raises(PermissionDenied):
            write()

    assert Widget.objects.get().name == "alpha"
    assert len(item_history(admin, "widget", widget.pk)) == 1


@pytest.mark.parametrize("role", [Role.ADMIN, Role.EDITOR, Role.VIEWER])
def test_every_role_can_read_history(admin, role):
    reader = admin if role == Role.ADMIN else create_user(admin, "r", "another-long-password", role)
    widget = create_item(admin, Widget, name="alpha")

    assert len(item_history(reader, "widget", widget.pk)) == 1


@pytest.mark.parametrize("make_actor", [lambda: AnonymousUser(), lambda: roleless_user()])
def test_someone_without_a_role_cannot_read_or_write(admin, make_actor):
    actor = make_actor()
    widget = create_item(admin, Widget, name="alpha")

    with pytest.raises(PermissionDenied):
        item_history(actor, "widget", widget.pk)
    with pytest.raises(PermissionDenied):
        create_item(actor, Widget, name="beta")


def test_history_of_something_never_recorded_is_not_found(admin):
    with pytest.raises(Http404):
        item_history(admin, "widget", 999999)


def test_every_netatlas_model_is_tracked_unless_it_is_infrastructure():
    untracked = {
        model._meta.label
        for model in apps.get_models()
        if model.__module__.startswith("netatlas.")
        and model._meta.app_label not in UNTRACKED_APPS
        and not issubclass(model, TrackedModel)
    }

    assert untracked == DERIVED_MODELS


def roleless_user():
    return get_user_model().objects.create_user("roleless", password="another-long-password")

import json
import re

import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from netatlas.accounts.roles import Role
from netatlas.accounts.services import create_token, create_user, ensure_initial_admin
from netatlas.addressing.services import (
    create_subnet,
    create_vlan,
    list_ip_addresses,
    list_subnets,
    list_vlans,
)
from netatlas.devices.services import (
    create_device,
    create_device_type,
    create_interface,
    list_devices,
    list_interfaces,
)
from netatlas.history.services import item_history
from netatlas.imports.services import (
    MAX_CSV_BYTES,
    MAX_ROWS,
    ImportFailed,
    inspect_csv,
    preview_import,
    read_upload,
    run_import,
)
from netatlas.places.services import create_site


@pytest.fixture
def editor(db):
    ensure_initial_admin(username="root", password="correct-horse-battery")
    return authenticate(username="root", password="correct-horse-battery")


@pytest.fixture
def site(editor):
    return create_site(editor, name="Basel")


def names(actor, site):
    return sorted(device.name for device in list_devices(actor, site_id=site.id))


def errors_of(result, number):
    return next(row.errors for row in result.rows if row.number == number)


def test_preview_writes_nothing(editor, site):
    result = preview_import(editor, "device", "name,site\nsw1,Basel\nsw2,Basel\n")
    assert (result.total, result.valid) == (2, 2)
    assert [row.label for row in result.rows] == ["sw1", "sw2"]
    assert names(editor, site) == []


def test_import_creates_rows_with_history(editor, site):
    result = run_import(editor, "device", "name,site\nsw1,Basel\n")
    assert result.valid == 1
    device = list_devices(editor, site_id=site.id)[0]
    entry = item_history(editor, "device", device.id)[0]
    assert (entry.action, entry.actor) == ("created", "root")


def test_one_bad_row_writes_nothing(editor, site):
    text = "name,site\nsw1,Basel\nsw2,Nowhere\nsw3,Basel\n"
    with pytest.raises(ImportFailed) as failed:
        run_import(editor, "device", text)
    assert failed.value.preview.valid == 2
    assert "No Site is named Nowhere." in errors_of(failed.value.preview, 3)
    assert names(editor, site) == []


def test_errors_are_reported_for_every_row_with_spreadsheet_numbers(editor, site):
    text = "name,site\n,Basel\n\nsw1,Nowhere\nsw2,Basel,extra\nsw3,Basel\n"
    result = preview_import(editor, "device", text)
    assert result.valid == 1
    assert [row.number for row in result.rows] == [2, 4, 5, 6]
    assert "Name is required." in errors_of(result, 2)
    assert "No Site is named Nowhere." in errors_of(result, 4)
    assert "more cells" in errors_of(result, 5)[0]
    assert errors_of(result, 6) == []


def test_preview_matches_the_real_rules(editor, site):
    create_device(editor, name="taken", kind="physical", site_id=site.id)
    text = "name,site\ntaken,Basel\ndup,Basel\ndup,Basel\n"
    result = preview_import(editor, "device", text)
    assert "already exists" in errors_of(result, 2)[0]
    assert errors_of(result, 3) == []
    assert "already exists" in errors_of(result, 4)[0]


def test_devices_with_type_and_hypervisor_in_any_order(editor, site):
    create_device_type(
        editor,
        make="Acme",
        model_name="S1",
        rack_height=2,
        interfaces=[{"name": "eth0", "kind": "physical"}],
    )
    text = (
        "name,site,hypervisor,device_type\n"
        "vm2,Basel,vm1,\n"
        "vm1,Basel,host,\n"
        "host,Basel,,acme s1\n"
    )
    assert run_import(editor, "device", text).valid == 3
    devices = {d.name: d for d in list_devices(editor, site_id=site.id)}
    assert devices["vm2"].parent_name == "vm1"
    assert devices["vm1"].kind == "virtual"
    assert devices["host"].rack_height == 2
    assert [i.name for i in devices["host"].interfaces] == ["eth0"]


def test_hypervisor_cycle_is_an_error(editor, site):
    text = "name,site,hypervisor\na,Basel,b\nb,Basel,a\n"
    result = preview_import(editor, "device", text)
    assert result.valid == 0


def test_interfaces_with_lag_in_any_order(editor, site):
    device = create_device(editor, name="sw1", kind="physical", site_id=site.id)
    text = (
        "device,site,name,kind,lag\n"
        "sw1,Basel,ge-0,physical,bond0\n"
        "sw1,Basel,bond0,lag,\n"
    )
    assert run_import(editor, "interface", text).valid == 2
    interfaces = {i.name: i for i in list_interfaces(editor, device.id)}
    assert interfaces["ge-0"].lag_id == interfaces["bond0"].id


def test_subnets_with_vlan(editor, site):
    create_vlan(editor, number=10, site_id=site.id)
    assert run_import(editor, "subnet", "cidr,site,vlan\n10.0.0.0/24,Basel,10\n").valid == 1
    assert list_subnets(editor, site_id=site.id)[0].vlan_id is not None
    result = preview_import(editor, "subnet", "cidr,site,vlan\n10.1.0.0/24,Basel,99\n")
    assert "No VLAN 99" in errors_of(result, 2)[0]


def test_ip_addresses_assigned_and_reserved(editor, site):
    create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    device = create_device(editor, name="sw1", kind="physical", site_id=site.id)
    port = create_interface(editor, device.id, name="ge-0", kind="physical")
    text = (
        "address,site,device,interface,description\n"
        "10.0.0.1,Basel,sw1,ge-0,gateway\n"
        "10.0.0.2,Basel,,,spare\n"
    )
    assert run_import(editor, "ip-address", text).valid == 2
    found = {a.address: a for a in list_ip_addresses(editor, site_id=site.id)}
    assert found["10.0.0.1"].interface_id == port.id
    assert found["10.0.0.2"].interface_id is None
    assert found["10.0.0.2"].description == "spare"


def test_ip_address_needs_device_and_interface_together(editor, site):
    create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    text = "address,site,device\n10.0.0.1,Basel,sw1\n"
    assert "both" in errors_of(preview_import(editor, "ip-address", text), 2)[0]


def test_vlans(editor, site):
    assert run_import(editor, "vlan", "number,site,name\n10,Basel,mgmt\n").valid == 1
    assert list_vlans(editor, site_id=site.id)[0].name == "mgmt"
    result = preview_import(editor, "vlan", "number,site\n5000,Basel\nabc,Basel\n٣,Basel\n")
    assert result.valid == 0


def test_ambiguous_site_is_an_error(editor, site):
    create_site(editor, name="basel")
    result = preview_import(editor, "device", "name,site\nsw1,Basel\n")
    assert "Several Sites" in errors_of(result, 2)[0]


@pytest.mark.parametrize("separator", [",", ";", "\t"])
def test_separators(editor, site, separator):
    text = f"name{separator}site\nsw1{separator}Basel\n"
    assert preview_import(editor, "device", text).valid == 1


def test_quoted_cells(editor, site):
    create_subnet(editor, cidr="10.0.0.0/24", site_id=site.id)
    text = 'address,site,description\n10.0.0.1,Basel,"one, two ""three"""\n'
    run_import(editor, "ip-address", text)
    assert list_ip_addresses(editor, site_id=site.id)[0].description == 'one, two "three"'


def test_excel_encodings():
    bom = SimpleUploadedFile("a.csv", "﻿name,site\nsw,Zürich\n".encode())
    assert read_upload(bom).startswith("name,site")
    legacy = SimpleUploadedFile("a.csv", "name;site\nsw;Zürich\n".encode("cp1252"))
    assert "Zürich" in read_upload(legacy)


def test_columns_are_guessed(editor):
    columns = inspect_csv(editor, "device", "Hostname,Standort,Device Type\nsw1,Basel,x\n")
    assert columns.mapping == {
        "name": "Hostname",
        "site": "Standort",
        "device_type": "Device Type",
    }
    assert columns.row_count == 1


def test_explicit_mapping(editor, site):
    text = "A,B,ignored\nsw1,Basel,x\n"
    assert run_import(editor, "device", text, {"name": "A", "site": "B"}).valid == 1
    assert names(editor, site) == ["sw1"]


@pytest.mark.parametrize(
    "mapping",
    [
        {"name": "A"},
        {"name": "A", "site": "missing"},
        {"name": "A", "site": "A"},
        {"name": "A", "site": "B", "nonsense": "A"},
    ],
)
def test_bad_mapping_is_refused(editor, site, mapping):
    with pytest.raises(ValidationError):
        preview_import(editor, "device", "A,B\nx,y\n", mapping)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n",
        "name,site\n",
        "name,name\nx,y\n",
        "name,\nx,y\n",
        "name,site\nx\x00,y\n",
        'name,site\n"unterminated,y\n',
    ],
)
def test_unreadable_files_are_refused(editor, text):
    with pytest.raises(ValidationError):
        inspect_csv(editor, "device", text)


def test_unknown_kind_is_refused(editor):
    with pytest.raises(ValidationError):
        inspect_csv(editor, "cable", "name\nx\n")


def test_limits(editor, site):
    rows = "".join(f"d{n},Basel\n" for n in range(MAX_ROWS + 1))
    with pytest.raises(ValidationError):
        preview_import(editor, "device", f"name,site\n{rows}")
    with pytest.raises(ValidationError):
        preview_import(editor, "device", "name,site\n" + "x" * MAX_CSV_BYTES)
    big = SimpleUploadedFile("a.csv", b"x" * (MAX_CSV_BYTES + 1))
    with pytest.raises(ValidationError):
        read_upload(big)


def test_viewer_cannot_import(editor, site):
    viewer = create_user(editor, "viewer", "another-long-password", Role.VIEWER)
    for call in (inspect_csv, preview_import, run_import):
        with pytest.raises(PermissionDenied):
            call(viewer, "device", "name,site\nsw1,Basel\n")


def _hidden(html, name):
    value = re.search(rf'name="{name}" value="([^"]*)"', html).group(1)
    return (
        value.replace("&#x27;", "'").replace("&quot;", '"').replace("&lt;", "<")
        .replace("&gt;", ">").replace("&amp;", "&")
    )


def test_web_upload_map_preview_import(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Basel")
    upload = SimpleUploadedFile("devices.csv", "Hostname;Standort\nsw1;Basel\n".encode())

    mapped = logged_in_client.post(
        "/import/", {"step": "upload", "kind": "device", "file": upload}
    )
    html = mapped.content.decode()
    assert mapped.status_code == 200
    assert '<option value="Hostname" selected>' in html
    text = _hidden(html, "csv")

    preview = logged_in_client.post(
        "/import/",
        {
            "step": "preview",
            "kind": "device",
            "csv": text,
            "map_name": "Hostname",
            "map_site": "Standort",
        },
    )
    assert preview.status_code == 200
    assert 'name="step" value="import"' in preview.content.decode()
    assert names(admin, site) == []

    done = logged_in_client.post(
        "/import/",
        {
            "step": "import",
            "kind": "device",
            "csv": text,
            "map_name": "Hostname",
            "map_site": "Standort",
        },
    )
    assert done.status_code == 302
    assert names(admin, site) == ["sw1"]
    assert "Imported 1 row:" in logged_in_client.get("/import/").content.decode()


def test_web_preview_shows_errors_and_offers_no_import(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    create_site(admin, name="Basel")
    response = logged_in_client.post(
        "/import/",
        {
            "step": "preview",
            "kind": "device",
            "csv": "name,site\n<b>x</b>,Nowhere\n",
            "map_name": "name",
            "map_site": "site",
        },
    )
    html = response.content.decode()
    assert "No Site is named Nowhere." in html
    assert 'value="import"' not in html
    assert "<b>x</b>" not in html


def test_web_import_rechecks_and_writes_nothing_when_stale(logged_in_client):
    admin = authenticate(username="root", password="correct-horse-battery")
    site = create_site(admin, name="Basel")
    create_device(admin, name="taken", kind="physical", site_id=site.id)
    response = logged_in_client.post(
        "/import/",
        {
            "step": "import",
            "kind": "device",
            "csv": "name,site\nnew,Basel\ntaken,Basel\n",
            "map_name": "name",
            "map_site": "site",
        },
    )
    assert response.status_code == 409
    assert names(admin, site) == ["taken"]


@pytest.mark.parametrize(
    "data",
    [
        {"step": "upload", "kind": "device"},
        {"step": "upload", "kind": "nonsense"},
        {"step": "preview", "kind": "nonsense", "csv": "a\nb\n"},
        {"step": "preview", "kind": "device", "csv": ""},
        {"step": "nonsense", "kind": "device", "csv": "name,site\nx,y\n"},
    ],
)
def test_web_bad_requests_show_an_error(logged_in_client, data):
    assert logged_in_client.post("/import/", data).status_code == 400


def test_web_viewer_is_refused(client_with_role):
    client = client_with_role(Role.VIEWER)
    assert client.get("/import/").status_code == 403
    assert 'href="/import/"' not in client.get("/").content.decode()


def test_api_preview_and_import(editor, site):
    api = Client(HTTP_AUTHORIZATION=f"Bearer {create_token(editor, 't').secret}")
    body = {"kind": "device", "csv": "Host,Where\nsw1,Basel\n", "mapping": {"name": "Host", "site": "Where"}}

    preview = api.post("/api/imports/preview/", json.dumps(body), content_type="application/json")
    assert preview.status_code == 200
    assert preview.json()["valid"] == 1
    assert names(editor, site) == []

    done = api.post("/api/imports/", json.dumps(body), content_type="application/json")
    assert done.status_code == 201
    assert names(editor, site) == ["sw1"]

    again = api.post("/api/imports/", json.dumps(body), content_type="application/json")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "import_failed"
    assert "already exists" in again.json()["preview"]["rows"][0]["errors"][0]


def test_api_bad_mapping_uses_the_error_shape(editor, site):
    api = Client(HTTP_AUTHORIZATION=f"Bearer {create_token(editor, 't').secret}")
    body = {"kind": "device", "csv": "a,b\nx,y\n", "mapping": {"name": "a"}}
    response = api.post("/api/imports/preview/", json.dumps(body), content_type="application/json")
    assert response.status_code == 400
    assert "site" in response.json()["error"]["fields"]
